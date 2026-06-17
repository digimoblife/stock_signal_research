"""
analyze.py — dissect the volume divergence strategy to find hidden edges.

Strategy: volume_divergence
Current: 47.1% precision, Sharpe -0.06, PF 0.99

Goal: Determine whether filtering on confidence, regime, liquidity,
or frequency can extract a profitable subset.

Method:
  1. Re-run backtest with per-trade metadata
  2. Compute confidence score for each signal
  3. Classify regime, liquidity, frequency per trade
  4. Slice by each dimension → compute precision, Sharpe, PF
  5. Report best-performing subsets
  6. If subset passes thresholds (precision>55%, PF>1, Sharpe>0.5), design filter
"""
import logging
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from itertools import groupby

import numpy as np
import pandas as pd

from settings import DATA_DIR, TICKERS, TOTAL_COST, TEST_START
from research import load_ticker, volume_divergence_signals

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("analyze")
DATA_DIR = Path(DATA_DIR)


# ── Helpers ──────────────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_confidence(row: pd.Series) -> int:
    """
    Confidence score (0-100) for a volume divergence signal.
    Replicates logic from gen_signal.py but operates on the signal row.
    """
    conf = 50
    vol_ratio = row.get("vol_5", 1.0)
    ret_5 = row.get("ret_5", 0.0)

    # Divergence strength: stronger price drop + volume spike = higher confidence
    conf += min(25, int(abs(ret_5) * 1000))
    if vol_ratio > 1.5:
        conf += 15
    elif vol_ratio > 1.3:
        conf += 10
    # Bullish divergence bonus: ret_5 negative + vol_5 high
    if ret_5 < 0 and vol_ratio > 1.3:
        conf += 10
    # Bearish divergence bonus: ret_5 positive + vol_5 low
    if ret_5 > 0 and vol_ratio < 0.7:
        conf += 10

    # Penalty for extreme values (noise)
    if vol_ratio > 5:
        conf -= 20
    if abs(ret_5) > 0.25:
        conf -= 15

    return max(0, min(100, conf))


# ── Market regime ────────────────────────────────────────────────

def compute_market_proxy(data: dict) -> pd.DataFrame:
    """
    Build an equal-weighted market index from all stocks.
    Used for regime classification.
    """
    returns = []
    for ticker, df in data.items():
        ret = df["close"].pct_change().rename(ticker)
        returns.append(ret)
    index = pd.concat(returns, axis=1).mean(axis=1).dropna()
    return index.to_frame(name="market_ret")


def classify_regime(market_ret: pd.Series, lookback: int = 20) -> pd.Series:
    """
    Classify each date as 'bull', 'bear', or 'sideways' based on
    the cumulative market return over the lookback period.
    """
    cum_ret = market_ret.rolling(lookback).sum()
    regime = pd.Series("sideways", index=cum_ret.index)
    regime[cum_ret > 0.03] = "bull"
    regime[cum_ret < -0.03] = "bear"
    return regime


# ── Liquidity bucketing ──────────────────────────────────────────

def compute_liquidity_buckets(data: dict) -> dict:
    """
    Classify each ticker as 'large' or 'mid' cap based on
    median daily volume.
    """
    volumes = {}
    for ticker, df in data.items():
        volumes[ticker] = df["volume"].median()
    median_vol = pd.Series(volumes).median()
    buckets = {}
    for ticker, vol in volumes.items():
        buckets[ticker] = "large" if vol >= median_vol else "mid"
    return buckets


# ── Detailed backtest with per-trade metadata ────────────────────

def backtest_with_metadata(data: dict, liquidity_buckets: dict,
                           market_regime: pd.Series) -> pd.DataFrame:
    """
    Run volume divergence backtest. Return DataFrame with one row per trade,
    including metadata for filtering analysis.
    """
    all_trades = []

    for ticker, df in data.items():
        # Generate signals
        sig = volume_divergence_signals(df)
        sig = sig[sig.index >= TEST_START]
        signal_days = sig[sig["signal"] != 0]

        if signal_days.empty:
            continue

        for idx, row in signal_days.iterrows():
            # Next day entry
            entry_idx = df.index.get_indexer([idx], method="bfill")[0] + 1
            if entry_idx >= len(df):
                continue

            entry_date = df.index[entry_idx]
            entry_price = df.iloc[entry_idx]["open"]
            direction = int(row["signal"])

            # Compute ATR for stop/target at entry time
            atr_series = compute_atr(df)
            atr_val = atr_series.iloc[entry_idx] if entry_idx < len(atr_series) else atr_series.iloc[-1]
            if pd.isna(atr_val) or atr_val <= 0:
                # Fallback to price-based stop
                atr_val = entry_price * 0.02

            # Stop and target
            if direction == 1:  # BUY
                stop = entry_price - 2 * atr_val
                target = entry_price + 4 * atr_val
            else:  # SELL
                stop = entry_price + 2 * atr_val
                target = entry_price - 4 * atr_val

            # 5-day hold
            exit_idx = min(entry_idx + 5, len(df) - 1)
            exit_date = df.index[exit_idx]
            exit_price = df.iloc[exit_idx]["close"]

            # Check if stop/target hit during hold
            held = df.iloc[entry_idx:exit_idx + 1]
            if direction == 1:
                hit_stop = held["low"].min() <= stop
                hit_target = held["high"].max() >= target
                if hit_stop:
                    exit_price = stop
                elif hit_target:
                    exit_price = target
                pnl = (exit_price / entry_price - 1) - TOTAL_COST
            else:
                hit_stop = held["high"].max() >= stop
                hit_target = held["low"].min() <= target
                if hit_stop:
                    exit_price = stop
                elif hit_target:
                    exit_price = target
                pnl = (entry_price / exit_price - 1) - TOTAL_COST

            days_held = (exit_date - entry_date).days

            # ── METADATA ──

            # Confidence score
            conf = compute_confidence(row)

            # Market regime at entry
            # Find the closest market regime date <= entry_date
            regime_dates = market_regime.index[market_regime.index <= entry_date]
            if len(regime_dates) > 0:
                regime = market_regime.loc[regime_dates[-1]]
            else:
                regime = "unknown"

            # Signal recency: how many signals on this stock in last 30 days
            lookback_start = idx - pd.Timedelta(days=30)
            recent_signals = signal_days[
                (signal_days.index >= lookback_start) &
                (signal_days.index < idx)
            ]
            signals_last_30d = len(recent_signals)

            # Remaining metadata from the signal row
            vol_5 = row.get("vol_5", np.nan)
            ret_5 = row.get("ret_5", np.nan)
            bull_streak = row.get("bull_streak", 0)
            bear_streak = row.get("bear_streak", 0)

            all_trades.append({
                "ticker": ticker,
                "signal_date": idx.strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "exit_date": exit_date.strftime("%Y-%m-%d"),
                "direction": "BUY" if direction == 1 else "SELL",
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "pnl_pct": round(pnl * 100, 4),
                "days_held": days_held,
                "hit_stop": bool(hit_stop),
                "hit_target": bool(hit_target),

                # Filter dimensions
                "confidence": conf,
                "confidence_bucket": bucket_confidence(conf),
                "regime": regime,
                "liquidity": liquidity_buckets.get(ticker, "unknown"),
                "signals_last_30d": signals_last_30d,

                # Raw signal features
                "vol_5": vol_5 if not pd.isna(vol_5) else 0,
                "ret_5": ret_5 if not pd.isna(ret_5) else 0,
                "bull_streak": bull_streak,
                "bear_streak": bear_streak,
            })

    return pd.DataFrame(all_trades)


def bucket_confidence(conf: int) -> str:
    """Map confidence score to bucket label."""
    if conf >= 90: return "90+"
    if conf >= 80: return "80-89"
    if conf >= 70: return "70-79"
    if conf >= 60: return "60-69"
    return "50-59"


# ── Metrics computation ──────────────────────────────────────────

def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute performance metrics for a DataFrame of trades."""
    if df.empty or len(df) < 3:
        return {"trades": len(df), "precision": 0, "sharpe": 0,
                "profit_factor": 0, "avg_return": 0, "error": "insufficient data"}

    pnls = df["pnl_pct"].values / 100
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    # Annualize by average holding period (not fixed 1-day)
    avg_days = float(df["days_held"].mean()) if "days_held" in df.columns else 5.0
    ann_factor = np.sqrt(252 / max(avg_days, 1))

    sharpe = float(np.mean(pnls) / np.std(pnls) * ann_factor) if len(pnls) > 1 and np.std(pnls) > 0 else 0
    pf = float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else float("inf") if len(wins) > 0 else 0

    # Max consecutive losses
    loss_streak = max(
        (sum(1 for _ in g) for k, g in groupby(pnls <= 0) if k),
        default=0
    )

    return {
        "trades": len(pnls),
        "precision": round(len(wins) / len(pnls) * 100, 1),
        "sharpe": round(sharpe, 3),
        "profit_factor": round(pf, 3),
        "avg_return": round(float(np.mean(pnls)) * 100, 3),
        "total_return": round(float(np.sum(pnls)) * 100, 3),
        "avg_win": round(float(np.mean(wins)) * 100, 2) if len(wins) > 0 else 0,
        "avg_loss": round(float(np.mean(losses)) * 100, 2) if len(losses) > 0 else 0,
        "max_cons_losses": int(loss_streak),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
    }


# ── Printer ──────────────────────────────────────────────────────

def print_metrics(name: str, m: dict, indent: str = ""):
    """Print one row of metrics."""
    if m.get("error"):
        print(f"{indent}{name:<30} {m['trades']:>5} trades — {m['error']}")
        return
    print(f"{indent}{name:<30} {m['trades']:>5} trades  "
          f"prec={m['precision']:>5.1f}%  "
          f"sharpe={m['sharpe']:>6.3f}  "
          f"pf={m['profit_factor']:>5.2f}  "
          f"avg={m['avg_return']:>+6.2f}%  "
          f"w/l={m['wins']}/{m['losses']}  "
          f"maxL={m['max_cons_losses']}")


# ── Main analysis ────────────────────────────────────────────────

def run():
    """Full analysis pipeline."""
    print("\n" + "=" * 75)
    print("  VOLUME DIVERGENCE — DEEP STRATEGY DISSECTION")
    print("=" * 75)

    # 1. Load data
    print("\n[1/5] Loading data...", end=" ")
    data = {}
    for t in TICKERS:
        df = load_ticker(t)
        if not df.empty and len(df) > 100:
            data[t] = df
    print(f"{len(data)} tickers loaded")

    # 2. Liquidity buckets
    print("[2/5] Computing liquidity buckets...", end=" ")
    liquidity = compute_liquidity_buckets(data)
    large = [t for t, b in liquidity.items() if b == "large"]
    mid = [t for t, b in liquidity.items() if b == "mid"]
    print(f"{len(large)} large, {len(mid)} mid")

    # 3. Market regime
    print("[3/5] Computing market regimes...", end=" ")
    market = compute_market_proxy(data)
    regime_series = classify_regime(market["market_ret"])
    regime_counts = regime_series.value_counts()
    print(f"bull={regime_counts.get('bull',0)}d, "
          f"bear={regime_counts.get('bear',0)}d, "
          f"sideways={regime_counts.get('sideways',0)}d")

    # 4. Run detailed backtest
    print("[4/5] Running detailed backtest (this may take a minute)...")
    trades = backtest_with_metadata(data, liquidity, regime_series)

    if trades.empty:
        print("  No trades generated. Check data and date ranges.")
        return

    print(f"  Total trades: {len(trades)}")
    print(f"  Date range: {trades['entry_date'].min()} to {trades['entry_date'].max()}")

    overall = compute_metrics(trades)
    print_metrics("\n  BASELINE (ALL SIGNALS)", overall)

    # ── 5. Analysis by dimension ──────────────────────────────

    print("\n\n" + "=" * 75)
    print("  SECTION 1: PERFORMANCE BY CONFIDENCE BUCKET")
    print("=" * 75)

    conf_buckets = ["50-59", "60-69", "70-79", "80-89", "90+"]
    conf_results = {}
    for bucket in conf_buckets:
        subset = trades[trades["confidence_bucket"] == bucket]
        conf_results[bucket] = compute_metrics(subset)
        print_metrics(f"  Confidence {bucket}", conf_results[bucket], indent="")

    # ── 6. Performance by regime ─────────────────────────────

    print("\n" + "=" * 75)
    print("  SECTION 2: PERFORMANCE BY MARKET REGIME")
    print("=" * 75)

    regime_results = {}
    for regime in ["bull", "bear", "sideways"]:
        subset = trades[trades["regime"] == regime]
        regime_results[regime] = compute_metrics(subset)
        print_metrics(f"  {regime.upper()}", regime_results[regime])

    # ── 7. Performance by liquidity ──────────────────────────

    print("\n" + "=" * 75)
    print("  SECTION 3: PERFORMANCE BY LIQUIDITY")
    print("=" * 75)

    liq_results = {}
    for liq in ["large", "mid"]:
        subset = trades[trades["liquidity"] == liq]
        liq_results[liq] = compute_metrics(subset)
        print_metrics(f"  {liq.upper()}", liq_results[liq])

    # ── 8. Performance by signal frequency ───────────────────

    print("\n" + "=" * 75)
    print("  SECTION 4: PERFORMANCE BY SIGNAL FREQUENCY")
    print("=" * 75)

    # Bucket by signals in last 30 days
    freq_buckets = [
        (0, 1, "1 signal/mo"),
        (2, 3, "2-3 signals/mo"),
        (4, 6, "4-6 signals/mo"),
        (7, 99, "7+ signals/mo"),
    ]
    freq_results = {}
    for lo, hi, label in freq_buckets:
        subset = trades[(trades["signals_last_30d"] >= lo) &
                        (trades["signals_last_30d"] <= hi)]
        freq_results[label] = compute_metrics(subset)
        print_metrics(f"  {label}", freq_results[label])

    # ── 9. Cross-filters: confidence × regime ────────────────

    print("\n" + "=" * 75)
    print("  SECTION 5: CROSS-FILTER ANALYSIS")
    print("  Looking for combined filters that beat baseline")
    print("=" * 75)

    best_filters = []

    # Confidence × Regime
    print("\n  Confidence × Regime:")
    for bucket in ["70-79", "80-89", "90+"]:
        for regime in ["bull", "bear", "sideways"]:
            subset = trades[(trades["confidence_bucket"] == bucket) &
                           (trades["regime"] == regime)]
            m = compute_metrics(subset)
            if m["trades"] >= 10 and m["precision"] > 50:
                print_metrics(f"  {bucket} × {regime.upper():<10}", m)
                if m["precision"] > 55 and m["sharpe"] > 0.5:
                    best_filters.append((f"conf={bucket} & regime={regime}", m))

    # Confidence × Liquidity
    print("\n  Confidence × Liquidity:")
    for bucket in ["70-79", "80-89", "90+"]:
        for liq in ["large", "mid"]:
            subset = trades[(trades["confidence_bucket"] == bucket) &
                           (trades["liquidity"] == liq)]
            m = compute_metrics(subset)
            if m["trades"] >= 10 and m["precision"] > 50:
                print_metrics(f"  {bucket} × {liq.upper():<10}", m)
                if m["precision"] > 55 and m["sharpe"] > 0.5:
                    best_filters.append((f"conf={bucket} & liq={liq}", m))

    # Regime × Liquidity
    print("\n  Regime × Liquidity:")
    for regime in ["bull", "bear", "sideways"]:
        for liq in ["large", "mid"]:
            subset = trades[(trades["regime"] == regime) &
                           (trades["liquidity"] == liq)]
            m = compute_metrics(subset)
            if m["trades"] >= 10 and m["precision"] > 50:
                print_metrics(f"  {regime.upper():<10} × {liq.upper():<10}", m)
                if m["precision"] > 55 and m["sharpe"] > 0.5:
                    best_filters.append((f"regime={regime} & liq={liq}", m))

    # Confidence × Frequency
    print("\n  Confidence × Frequency:")
    for bucket in ["70-79", "80-89", "90+"]:
        for lo, hi, label in [(0, 1, "rare"), (2, 3, "normal"), (4, 99, "freq")]:
            subset = trades[(trades["confidence_bucket"] == bucket) &
                           (trades["signals_last_30d"] >= lo) &
                           (trades["signals_last_30d"] <= hi)]
            m = compute_metrics(subset)
            if m["trades"] >= 10 and m["precision"] > 50:
                print_metrics(f"  {bucket} × {label:<10}", m)
                if m["precision"] > 55 and m["sharpe"] > 0.5:
                    best_filters.append((f"conf={bucket} & freq={label}", m))

    # ── 10. Best filter summary ───────────────────────────────

    print("\n" + "=" * 75)
    print("  SECTION 6: BEST FILTERS FOUND")
    print("=" * 75)

    if best_filters:
        best_filters.sort(key=lambda x: x[1]["sharpe"], reverse=True)
        for name, m in best_filters:
            print_metrics(f"  {name:<35}", m)
    else:
        print("  No filter combination passed precision>55% AND Sharpe>0.5.")
        print("  Showing best available combinations:")

        # Find anything with precision > 50% and at least 10 trades
        candidates = []
        for bucket in conf_buckets:
            m = conf_results.get(bucket, {})
            if m.get("trades", 0) >= 10 and m.get("precision", 0) > 50:
                candidates.append((f"confidence={bucket}", m))

        for regime, m in regime_results.items():
            if m.get("trades", 0) >= 10 and m.get("precision", 0) > 50:
                candidates.append((f"regime={regime}", m))

        for liq, m in liq_results.items():
            if m.get("trades", 0) >= 10 and m.get("precision", 0) > 50:
                candidates.append((f"liquidity={liq}", m))

        candidates.sort(key=lambda x: x[1]["sharpe"], reverse=True)
        if candidates:
            for name, m in candidates:
                print_metrics(f"  {name:<35}", m)
        else:
            print("  No subset with >50% precision found.")
            print("  The volume divergence signal, in any form tested here,")
            print("  does not contain a hidden edge that can be extracted")
            print("  through simple filtering.")

    # ── 11. Regime threshold sensitivity ───────────────────────

    print("\n" + "=" * 75)
    print("  SECTION 7: REGIME THRESHOLD SENSITIVITY")
    print("  Testing robustness of the bear-market filter")
    print("=" * 75)

    for threshold in [0.02, 0.03, 0.04, 0.05, 0.07]:
        # Re-classify regime with different threshold
        temp_regime = pd.Series("sideways", index=regime_series.index)
        temp_regime[market["market_ret"].rolling(20).sum() > threshold] = "bull"
        temp_regime[market["market_ret"].rolling(20).sum() < -threshold] = "bear"

        # Re-map each trade's regime
        temp_trades = trades.copy()
        trade_regimes = []
        for i, row in temp_trades.iterrows():
            entry_date = pd.Timestamp(row["entry_date"])
            rdates = temp_regime.index[temp_regime.index <= entry_date]
            if len(rdates) > 0:
                trade_regimes.append(temp_regime.loc[rdates[-1]])
            else:
                trade_regimes.append("unknown")
        temp_trades["regime"] = trade_regimes

        bear_large = temp_trades[(temp_trades["regime"] == "bear") &
                                 (temp_trades["liquidity"] == "large")]
        m = compute_metrics(bear_large)
        print_metrics(f"  bear ({threshold*100:.0f}%) × large", m)

    # ── 12. Final verdict ──────────────────────────────────────

    print("\n" + "=" * 75)
    print("  VERDICT")
    print("=" * 75)

    b = overall
    print(f"\n  Baseline: {b['trades']} trades, {b['precision']}% precision, "
          f"Sharpe {b['sharpe']}, PF {b['profit_factor']}")

    if best_filters:
        best = best_filters[0]
        print(f"\n  ✅ FILTERABLE EDGE DETECTED")
        print(f"  Best filter: {best[0]}")
        print_metrics(f"  → ", best[1])
        print(f"\n  This filter extracts {best[1]['trades']} trades "
              f"({best[1]['trades']/b['trades']*100:.0f}% of total)")
        print(f"  with {best[1]['precision']}% precision, Sharpe {best[1]['sharpe']},")
        print(f"  profit factor {best[1]['profit_factor']}.")
    else:
        print(f"\n  ❌ NO HIDDEN EDGE FOUND THROUGH FILTERING")
        print(f"  No confidence/regime/liquidity/frequency subset")
        print(f"  consistently produces precision >55% with Sharpe >0.5.")
        print(f"\n  Conclusion: The volume divergence signal does NOT contain")
        print(f"  a tradeable edge that can be extracted by filtering.")
        print(f"  Next steps:")
        print(f"    a) Add new signal features (not just volume divergence)")
        print(f"    b) Try ensemble combination with other strategies")
        print(f"    c) Accept that daily OHLCV volume divergence may not")
        print(f"       have a real edge in IDX with current parameters")

    print("\n" + "=" * 75 + "\n")
    return trades


if __name__ == "__main__":
    run()
