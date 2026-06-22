"""
signal.py — generates next-day signals using the best strategy.
Called daily by cron. Outputs to stdout and optionally to Telegram.
"""
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from settings import (
    DATA_DIR, MIN_CONFIDENCE, MAX_DAILY_SIGNALS, MIN_RISK_REWARD,
    LONG_ONLY_MODE, STOP_ATR, TAKE_PROFIT_ATR, MAX_HOLD_DAYS, MIN_VOL_RATIO,
)
from universe import get_eligible_tickers
from research import (
    load_ticker, volume_divergence_signals, momentum_signals,
    rsi_signals, breakout_signals,
)
import filter
from filter import should_trade, get_market_regime, classify_liquidity, augment_signal
from track import get_holding_stats, get_open_signals
from ai_explain import generate_signal_explanation

log = logging.getLogger("signal")

# Which strategy to use (set this after running research.py)
ACTIVE_STRATEGY = "volume_divergence"

# Map strategy names to functions
STRATEGY_MAP = {
    "volume_divergence": volume_divergence_signals,
    "momentum_20d": lambda df: momentum_signals(df, 20),
    "rsi_mean_reversion": rsi_signals,
    "breakout_20d": lambda df: breakout_signals(df, 20),
}


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def confidence_score(signal_type: str, df: pd.DataFrame, idx: int) -> int:
    """
    Compute confidence (0-100) based on analysis findings.
    For volume_divergence, delegates to filter.score_signal().
    """
    if signal_type == "volume_divergence":
        vol_ratio = df["volume"].iloc[idx] / df["volume"].iloc[max(0, idx-5):idx].mean()
        ret_5 = df["close"].pct_change(5).iloc[idx]
        bull_streak = 0
        bear_streak = 0
        # Rough streak estimate from recent data
        for i in range(max(0, idx-5), idx + 1):
            if i > 0:
                r = df["close"].pct_change(5).iloc[i] if i >= 5 else 0
                v = df["volume"].iloc[i] / df["volume"].iloc[max(0, i-5):i].mean() if i >= 5 else 1
                if r < -0.02 and v > 1.3:
                    bull_streak += 1
                elif r > 0.02 and v < 0.7:
                    bear_streak += 1

        # Use the improved score from our analysis
        row = pd.Series({"vol_5": vol_ratio, "ret_5": ret_5,
                         "bull_streak": bull_streak, "bear_streak": bear_streak})
        return filter.score_signal(row)

    elif signal_type == "momentum_20d":
        ret = df["close"].pct_change(20).iloc[idx]
        conf = 50 + min(30, int(abs(ret) * 500))

    elif signal_type == "rsi_mean_reversion":
        rsi_val = df["rsi"].iloc[idx] if "rsi" in df.columns else 50
        if rsi_val < 25 or rsi_val > 75:
            conf += 20
        elif rsi_val < 30 or rsi_val > 70:
            conf += 10

    elif signal_type == "breakout_20d":
        close = df["close"].iloc[idx]
        high_max = df["high"].rolling(20).max().iloc[idx]
        low_min = df["low"].rolling(20).min().iloc[idx]
        if close > high_max:
            conf += min(25, int((close / high_max - 1) * 1000))
        elif close < low_min:
            conf += min(25, int((low_min / close - 1) * 1000))

    return min(100, max(0, conf))


def _build_ai_context(df, ticker, confidence, direction_str,
                       holding, regime, liquidity):
    """Extract ticker-level data for the AI explanation generator."""
    close_price = float(df["close"].iloc[-1])
    volume_today = float(df["volume"].iloc[-1])
    vol_20 = df["volume"].rolling(20).mean()
    volume_avg_20d = float(vol_20.iloc[-1]) if not pd.isna(vol_20.iloc[-1]) else volume_today

    ret_series = df["close"].pct_change(5)
    ret_5_pct = float(ret_series.iloc[-1] * 100) if not pd.isna(ret_series.iloc[-1]) else 0.0

    # Volume ratio: today vs average of previous 5 days
    prev_vol = df["volume"].iloc[max(0, len(df) - 6):len(df) - 1].mean()
    vol_ratio = float(volume_today / prev_vol) if prev_vol > 0 else 1.0

    last_5 = df.tail(5)
    prices_5d = [float(x) for x in last_5["close"].values]
    volumes_5d = [float(x) for x in last_5["volume"].values]

    return {
        "ticker": ticker,
        "direction": direction_str,
        "confidence": confidence,
        "regime": regime or "unknown",
        "liquidity": liquidity or "unknown",
        "ret_5_pct": ret_5_pct,
        "vol_ratio": vol_ratio,
        "close_price": close_price,
        "volume_today": volume_today,
        "volume_avg_20d": volume_avg_20d,
        "prices_5d": prices_5d,
        "volumes_5d": volumes_5d,
        "holding_stats": holding,
    }


def generate_signals(today: str = None) -> list[dict]:
    """
    Generate signals for tonight/tomorrow.
    Returns list of signal dicts, sorted by confidence descending.
    """
    if today is None:
        today = datetime.now()

    today_dt = today if isinstance(today, datetime) else datetime.now()

    # Load tickers with existing open signals — block duplicates
    open_tickers = {s["ticker"] for s in get_open_signals()}
    if open_tickers:
        log.info(f"Tickers with open signals (skipped): {sorted(open_tickers)}")

    strategy_func = STRATEGY_MAP.get(ACTIVE_STRATEGY)
    if not strategy_func:
        log.error(f"Unknown strategy: {ACTIVE_STRATEGY}")
        return []

    signal_func = ACTIVE_STRATEGY
    signals = []

    tickers, counts = get_eligible_tickers()
    log.info(f"Signal scan: {counts['eligible']} eligible / {counts['scanned']} scanned")

    for ticker in tickers:
        if ticker in open_tickers:
            log.debug(f"Skipping {ticker}: open signal exists")
            continue

        df = load_ticker(ticker)
        if df.empty or len(df) < 60:
            continue

        # Generate signals
        sig = strategy_func(df)
        if sig.empty or "signal" not in sig.columns:
            continue

        # Find ALL non-zero signals in the last N days
        cutoff = df.index[-1] - pd.Timedelta(days=10)
        recent_signals = sig[(sig["signal"] != 0) & (sig.index >= cutoff)]

        if recent_signals.empty:
            continue

        # Take the most recent signal
        latest = recent_signals.iloc[-1]
        signal_date = latest.name
        direction = int(latest["signal"])

        if LONG_ONLY_MODE and direction == -1:
            log.info(f"{ticker} SELL skipped (long-only mode)")
            continue

        days_ago = (df.index[-1] - signal_date).days
        if days_ago > 10:
            continue

        # Current price + ATR for risk calculation
        current_price = df["close"].iloc[-1]
        atr = compute_atr(df).iloc[-1]

        if pd.isna(atr) or atr <= 0:
            continue

        # Volume ratio filter (C_BALANCED: min 2.0)
        idx_loc = df.index.get_loc(signal_date)
        prev_vol = df["volume"].iloc[max(0, idx_loc-5):idx_loc].mean()
        vol_ratio = df["volume"].iloc[idx_loc] / prev_vol if prev_vol > 0 else 0
        if vol_ratio < MIN_VOL_RATIO:
            continue

        # Entry zone: current price ± 1%
        entry_low = current_price * 0.99
        entry_high = current_price * 1.01

        # Stop: STOP_ATR × ATR (C_BALANCED: 3×)
        if direction == 1:  # BUY
            stop = current_price - STOP_ATR * atr
            tp = current_price + TAKE_PROFIT_ATR * atr if TAKE_PROFIT_ATR else None
        else:  # SELL
            stop = current_price + STOP_ATR * atr
            tp = current_price - TAKE_PROFIT_ATR * atr if TAKE_PROFIT_ATR else None

        # Risk-reward (skip if no take profit)
        if TAKE_PROFIT_ATR:
            risk = abs(current_price - stop) / current_price
            reward = abs(current_price - tp) / current_price
            rr = reward / risk if risk > 0 else 0
            if rr < MIN_RISK_REWARD:
                continue
        else:
            rr = None

        # Confidence
        conf = confidence_score(ACTIVE_STRATEGY, df, idx_loc)

        if conf < MIN_CONFIDENCE:
            continue

        # Reasoning string
        if direction == 1:
            reasoning = f"{signal_func.replace('_', ' ').title()}: bullish signal detected"
        else:
            reasoning = f"{signal_func.replace('_', ' ').title()}: bearish signal detected"

        # Attach holding period stats, regime, liquidity
        holding = get_holding_stats(ACTIVE_STRATEGY, conf)
        regime = get_market_regime()
        liq = classify_liquidity(ticker)

        # Build AI explanation context from current ticker data
        ai_context = _build_ai_context(df, ticker, conf,
                                       direction_str="BUY" if direction == 1 else "SELL",
                                       holding=holding, regime=regime, liquidity=liq)
        ai_explanation = generate_signal_explanation(ai_context)

        signals.append({
            "ticker": ticker,
            "date": today_dt.strftime("%Y-%m-%d"),
            "direction": "BUY" if direction == 1 else "SELL",
            "confidence": conf,
            "entry_low": round(entry_low, 2),
            "entry_high": round(entry_high, 2),
            "stop_loss": round(stop, 2),
            "take_profit": round(tp, 2) if tp else None,
            "risk_reward": round(rr, 2) if rr else None,
            "reasoning": reasoning,
            "strategy": signal_func,
            "holding_stats": holding,
            "regime": regime,
            "liquidity": liq,
            "max_hold_days": MAX_HOLD_DAYS,
            "ai_explanation": ai_explanation,
        })

    # Sort by confidence
    signals.sort(key=lambda s: s["confidence"], reverse=True)

    # Limit to max daily
    signals = signals[:MAX_DAILY_SIGNALS]

    return signals


if __name__ == "__main__":
    signals = generate_signals()
    if signals:
        for s in signals:
            print(f"{s['direction']} {s['ticker']}  confidence={s['confidence']}  "
                  f"entry={s['entry_low']:.0f}-{s['entry_high']:.0f}  "
                  f"stop={s['stop_loss']:.0f}  tp={s['take_profit']:.0f}  "
                  f"rr={s['risk_reward']:.1f}")
            print(f"  {s['reasoning']}")
    else:
        print("No signals generated today.")
