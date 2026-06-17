"""
filter.py — signal quality filters for the volume divergence strategy.

Based on empirical analysis:
  - Baseline precision: 47.3% (no edge)
  - Bear market × Large caps: 56.3% precision, Sharpe 1.45 (EDGE)
  - Regime threshold is robust (2%-7% all work)
  - Confidence 80-89 is better than 90+ (excessive volume is noise)

The filters must reduce signal quantity while improving signal quality.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from settings import DATA_DIR, TICKERS

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
    for ticker in TICKERS:
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
        for t in TICKERS:
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

    # Volume confirmation: spike but NOT extreme
    if 1.3 < vol_5 <= 2.5:
        conf += 15   # sweet spot
    elif vol_5 > 2.5:
        conf += 8    # very high volume, likely noise
    elif 1.1 < vol_5 <= 1.3:
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
    Apply all filters to determine if a signal should be acted on.

    Returns: (should_trade: bool, reason: str, adjusted_confidence: int)
    """
    if as_of is None:
        as_of = datetime.now().strftime("%Y-%m-%d")

    regime = get_market_regime(as_of=as_of)
    liquidity = classify_liquidity(ticker)
    conf = score_signal(signal_row)

    reasons = []

    # PRIMARY FILTER: regime × liquidity
    if regime == "bear" and liquidity == "large":
        pass  # best case, let it through
    elif regime == "bear" and liquidity == "mid":
        reasons.append("mid-cap in bear market (marginal)")
        conf -= 5
    elif regime == "bull" and liquidity == "large":
        reasons.append("bull market — divergence unreliable on large caps")
        conf -= 20
    elif regime == "bull" and liquidity == "mid":
        reasons.append("bull market — suppressing mid-cap signals")
        return (False, "bull + mid-cap: low edge", conf)
    elif regime == "sideways":
        reasons.append("sideways market — high false positive rate")
        conf -= 15

    # CONFIDENCE FILTER: prefer 80-89, penalize 90+
    if conf >= 90:
        reasons.append("very high confidence (may indicate noise)")
        conf -= 5
    elif conf < 60:
        reasons.append("low confidence")
        conf -= 10

    if conf < 50:
        reasons.append("confidence below threshold")
        return (False, "; ".join(reasons), conf)

    return (True, "; ".join(reasons) if reasons else "all filters pass", conf)


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
