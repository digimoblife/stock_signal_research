"""
filter.py — signal quality filters for the volume divergence strategy.

Based on C_BALANCED analysis:
  - No regime filter (all regimes tradeable)
  - Min volume ratio 2.0 (stronger volume confirmation required)
  - Large + Mid caps only (small caps excluded)
  - No take profit (let winners run)
  - 3× ATR stop, 15d max hold
  - Confidence 80-89 is better than 90+ (excessive volume is noise)
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from settings import DATA_DIR, MIN_VOL_RATIO
from universe import get_universe

log = logging.getLogger("filter")
DATA_DIR = Path(DATA_DIR)


# ── Market proxy ────────────────────────────────────────────────

def build_market_proxy() -> pd.DataFrame:
    """
    Build equal-weighted market index from universe.
    Cached after first computation.
    """
    cache_path = DATA_DIR / ".market_proxy.csv"

    if cache_path.exists():
        return pd.read_csv(cache_path, index_col=0, parse_dates=True)

    returns = []
    for ticker in get_universe():
        path = DATA_DIR / f"{ticker}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) < 100:
            continue
        ret = df["close"].pct_change().rename(ticker)
        returns.append(ret)

    if not returns:
        log.warning("No data available for market proxy")
        return pd.DataFrame()

    proxy = pd.concat(returns, axis=1).mean(axis=1).dropna().to_frame(name="market_ret")
    proxy.index.name = "date"
    proxy.to_csv(cache_path)
    log.info(f"Market proxy built: {len(proxy)} days")
    return proxy


def get_market_regime(lookback: int = 20, threshold: float = 0.03,
                      as_of: str = None) -> str:
    """
    Classify current market regime.

    Returns: 'bull', 'bear', or 'sideways'
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")

    proxy = build_market_proxy()
    if proxy.empty:
        return "unknown"

    # Find the most recent date <= as_of
    proxy = proxy[proxy.index <= as_of]
    if len(proxy) < lookback:
        return "unknown"

    recent = proxy.tail(lookback)
    cum_ret = recent["market_ret"].sum()

    if cum_ret > threshold:
        return "bull"
    elif cum_ret < -threshold:
        return "bear"
    else:
        return "sideways"


def is_market_bullish(as_of: str = None, ma_period: int = 50) -> bool:
    """
    Check if the market benchmark (IHSG or market proxy) is above its MA50.
    Used for T6_USE_MARKET_FILTER.
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")

    ihsg_path = DATA_DIR / "IHSG.csv"
    if ihsg_path.exists():
        df = pd.read_csv(ihsg_path, index_col=0, parse_dates=True)
        df = df[df.index <= as_of]
        if len(df) >= ma_period:
            ma50 = df["close"].rolling(ma_period).mean().iloc[-1]
            close = df["close"].iloc[-1]
            return not pd.isna(ma50) and close >= ma50

    proxy = build_market_proxy()
    if proxy.empty:
        return True
    proxy = proxy[proxy.index <= as_of]
    if len(proxy) < ma_period:
        return True

    cumulative = (1 + proxy["market_ret"]).cumprod()
    ma50 = cumulative.rolling(ma_period).mean().iloc[-1]
    close = cumulative.iloc[-1]
    return not pd.isna(ma50) and close >= ma50


def compute_relative_strength(ticker: str, as_of: str = None, window: int = 20) -> float:
    """
    Compute Relative Strength vs IHSG benchmark over a rolling window.
    RS_20 = (1 + stock_ret) / (1 + market_ret)
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")

    ticker_path = DATA_DIR / f"{ticker}.csv"
    if not ticker_path.exists():
        return 1.0

    df_stock = pd.read_csv(ticker_path, index_col=0, parse_dates=True)
    df_stock = df_stock[df_stock.index <= as_of]
    if len(df_stock) < window + 1:
        return 1.0

    stock_ret = (df_stock["close"].iloc[-1] / df_stock["close"].iloc[-window - 1]) - 1.0

    ihsg_path = DATA_DIR / "IHSG.csv"
    if ihsg_path.exists():
        df_mkt = pd.read_csv(ihsg_path, index_col=0, parse_dates=True)
        df_mkt = df_mkt[df_mkt.index <= as_of]
        if len(df_mkt) >= window + 1:
            mkt_ret = (df_mkt["close"].iloc[-1] / df_mkt["close"].iloc[-window - 1]) - 1.0
        else:
            mkt_ret = 0.0
    else:
        proxy = build_market_proxy()
        proxy = proxy[proxy.index <= as_of]
        mkt_ret = proxy["market_ret"].tail(window).sum() if len(proxy) >= window else 0.0

    return (1.0 + stock_ret) / (1.0 + mkt_ret) if (1.0 + mkt_ret) > 0 else 1.0



# ── Liquidity classification ────────────────────────────────────

_liquidity_cache = None


def classify_liquidity(ticker: str) -> str:
    """
    Classify a stock as 'large' or 'mid' cap based on median daily volume.
    Uses the 50th percentile of all stocks as the split point.
    """
    global _liquidity_cache

    if _liquidity_cache is None:
        volumes = {}
        for t in get_universe():
            path = DATA_DIR / f"{t}.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) > 100:
                volumes[t] = df["volume"].median()

        if not volumes:
            return "unknown"

        median_vol = pd.Series(volumes).median()
        _liquidity_cache = {
            t: "large" if v >= median_vol else "mid"
            for t, v in volumes.items()
        }

    return _liquidity_cache.get(ticker, "unknown")


# ── Confidence re-scoring ───────────────────────────────────────

def score_signal(row: pd.Series) -> int:
    """
    Improved confidence score for volume divergence signals.
    Based on analysis showing 80-89 bucket outperforms 90+.

    Key insight: extreme volume spikes (vol_5 > 1.5) are often noise,
    not accumulation. Moderate volume+price divergence is more reliable.
    """
    conf = 50

    vol_5 = row.get("vol_5", 1.0)
    ret_5 = row.get("ret_5", 0.0)
    bull_streak = row.get("bull_streak", 0)
    bear_streak = row.get("bear_streak", 0)

    # Price trend contribution (stronger move = more signal)
    conf += min(20, int(abs(ret_5) * 800))

    # Volume confirmation: C_BALANCED sweet spot 2.0-2.5
    if 2.0 <= vol_5 <= 2.5:
        conf += 15   # sweet spot — strong but not extreme
    elif vol_5 > 2.5:
        conf += 8    # very high volume, noise risk
    elif 1.3 < vol_5 < 2.0:
        conf += 8    # modest volume increase

    # Consecutive divergence days bonus
    if bull_streak >= 3 or bear_streak >= 3:
        conf += 10   # stronger confirmation
    elif bull_streak >= 1 or bear_streak >= 1:
        conf += 5

    # Direction correctness
    if ret_5 < 0 and vol_5 > 1.1:
        conf += 5    # bullish divergence confirmed
    elif ret_5 > 0 and vol_5 < 0.8:
        conf += 5    # bearish divergence confirmed

    # Foreign flow / Bandarmology bonus
    if row.get("foreign_flow_positive", False):
        try:
            from settings import T6_FOREIGN_FLOW_BONUS
            conf += T6_FOREIGN_FLOW_BONUS
        except ImportError:
            conf += 15

    # Penalty: noise indicators
    if vol_5 > 5.0:
        conf -= 25   # extreme volume = data error or one-time event
    if abs(ret_5) > 0.20:
        conf -= 10   # too extreme, likely corporate action effect

    return max(0, min(100, conf))


# ── Master filter ────────────────────────────────────────────────

def should_trade(ticker: str, signal_row: pd.Series,
                 as_of: str = None) -> tuple:
    """
    Apply all filters to determine if a signal should be acted on (C_BALANCED / T6).

    Rules:
      - Market filter (IHSG > MA50 if T6_USE_MARKET_FILTER enabled)
      - Relative Strength vs IHSG >= T6_MIN_RS20 (1.05)
      - Large + Mid caps only via classify_liquidity
      - Min volume ratio MIN_VOL_RATIO (2.0)
      - Confidence scoring with Foreign Flow bonus

    Returns: (should_trade: bool, reason: str, adjusted_confidence: int)
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")

    reasons = []

    # MARKET TREND FILTER (IHSG > MA50)
    try:
        from settings import T6_USE_MARKET_FILTER, T6_MIN_RS20
        if T6_USE_MARKET_FILTER and not is_market_bullish(as_of=as_of):
            reasons.append("IHSG market trend is below MA50")
            return (False, "; ".join(reasons), 0)

        rs20 = compute_relative_strength(ticker, as_of=as_of)
        if rs20 < T6_MIN_RS20:
            reasons.append(f"Relative strength {rs20:.2f} < {T6_MIN_RS20:.2f}")
            return (False, "; ".join(reasons), 0)
    except ImportError:
        pass

    regime = get_market_regime(as_of=as_of)
    liquidity = classify_liquidity(ticker)
    conf = score_signal(signal_row)


    reasons = []

    # VOLUME RATIO FILTER: C_BALANCED requires strong volume confirmation
    vol_5 = signal_row.get("vol_5", 1.0)
    if vol_5 < MIN_VOL_RATIO:
        reasons.append(f"vol_ratio {vol_5:.1f} < {MIN_VOL_RATIO}")
        return (False, "; ".join(reasons), conf)

    # CONFIDENCE FILTER: prefer 80-89, penalize 90+ (noise)
    if conf >= 90:
        reasons.append("very high confidence (may indicate noise)")
        conf -= 5
    elif conf < 60:
        reasons.append("low confidence")
        conf -= 10

    if conf < 50:
        reasons.append("confidence below threshold")
        return (False, "; ".join(reasons), conf)

    # Append market context for transparency (not used for filtering)
    reasons.append(f"regime={regime}, liquidity={liquidity}")

    return (True, "; ".join(reasons), conf)


# ── Signal augmentation ─────────────────────────────────────────

def augment_signal(signal: dict) -> dict:
    """
    Add filter metadata to a signal dict before saving.
    Call this after signal generation but before delivery.
    """
    ticker = signal["ticker"]
    signal_date = signal.get("date", datetime.now().strftime("%Y-%m-%d"))

    # Create a mock row for scoring
    row = pd.Series({
        "vol_5": signal.get("vol_5", 1.0),
        "ret_5": signal.get("ret_5", 0.0),
        "bull_streak": signal.get("bull_streak", 0),
        "bear_streak": signal.get("bear_streak", 0),
    })

    should, reason, conf = should_trade(ticker, row, signal_date)

    signal["confidence"] = conf
    signal["filter_result"] = "PASS" if should else "FAIL"
    signal["filter_reason"] = reason
    signal["regime"] = get_market_regime(as_of=signal_date)
    signal["liquidity"] = classify_liquidity(ticker)

    return signal


if __name__ == "__main__":
    # Quick test
    print(f"Current regime: {get_market_regime()}")
    print(f"BBCA liquidity: {classify_liquidity('BBCA')}")
    print(f"BBRI liquidity: {classify_liquidity('BBRI')}")
    print(f"ADRO liquidity: {classify_liquidity('ADRO')}")
    print(f"HMSP liquidity: {classify_liquidity('HMSP')}")
