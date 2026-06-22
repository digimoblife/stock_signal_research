"""
research.py — tests multiple strategies against historical data.
Outputs ranked performance table. You pick the best one for paper trading.

Strategies implemented:
  1. volume_divergence  — price goes down while volume goes up
  2. momentum           — buy recent winners, sell recent losers
  3. rsi_mean_reversion — buy oversold, sell overbought
  4. breakout           — break above/below recent range
"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from settings import DATA_DIR, TRAIN_START, TRAIN_END, TEST_START, TOTAL_COST
from universe import get_universe
import filter

log = logging.getLogger("research")
DATA_DIR = Path(DATA_DIR)


# ── Data loading ─────────────────────────────────────────────────

def load_ticker(ticker: str) -> pd.DataFrame:
    """Load single ticker CSV. Returns DataFrame with columns: open, high, low, close, volume."""
    path = DATA_DIR / f"{ticker}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index.name = "date"
    # Drop columns from Yahoo that we don't need
    for col in ["Dividends", "Stock Splits", "ticker"]:
        if col in df.columns:
            df.drop(columns=[col], inplace=True)
    df = df.sort_index()
    return df


def load_all(start=None, end=None, use_eligible: bool = True) -> dict[str, pd.DataFrame]:
    """
    Load tickers into a dict. Filters by date range if given.
    
    If use_eligible is True (default), only loads tickers that pass the
    liquidity filter (MIN_PRICE / MIN_ADV). This is used by backtests
    to match the same universe as signal generation.
    """
    data = {}
    if use_eligible:
        from universe import get_eligible_tickers
        tickers, _ = get_eligible_tickers()
    else:
        tickers = get_universe()
    
    for t in tickers:
        df = load_ticker(t)
        if df.empty:
            continue
        if start:
            df = df[df.index >= start]
        if end:
            df = df[df.index <= end]
        if len(df) > 100:  # minimum data
            data[t] = df
    return data


# ── Strategy 1: Volume Divergence ────────────────────────────────

def volume_divergence_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect: price declines while volume increases (accumulation).
    Returns DataFrame with signal dates.
    """
    signals = pd.DataFrame(index=df.index)
    signals["close"] = df["close"]
    signals["volume"] = df["volume"]

    # Price trend: 5-day return
    signals["ret_5"] = signals["close"].pct_change(5)

    # Volume trend: 5-day volume change ratio
    signals["vol_5"] = signals["volume"] / signals["volume"].shift(5).rolling(5).mean()

    # Divergence: price down AND volume up (bullish divergence)
    signals["bull_div"] = (signals["ret_5"] < -0.02) & (signals["vol_5"] > 1.3)

    # Bearish divergence: price up AND volume down
    signals["bear_div"] = (signals["ret_5"] > 0.02) & (signals["vol_5"] < 0.7)

    # Consecutive days filter: need at least 2 days of divergence
    signals["bull_streak"] = signals["bull_div"].rolling(2).sum()
    signals["bear_streak"] = signals["bear_div"].rolling(2).sum()

    signals["signal"] = 0
    signals.loc[signals["bull_streak"] >= 2, "signal"] = 1   # BUY
    signals.loc[signals["bear_streak"] >= 2, "signal"] = -1  # SELL

    return signals


# ── Strategy 2: Momentum ─────────────────────────────────────────

def momentum_signals(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """
    Buy stocks with strong recent return, sell weak ones.
    """
    signals = pd.DataFrame(index=df.index)
    signals["close"] = df["close"]
    signals["ret"] = signals["close"].pct_change(lookback)

    # Cross-sectional rank within universe is better, but for single-stock:
    signals["signal"] = 0
    signals.loc[signals["ret"] > 0.05, "signal"] = 1    # strong uptrend
    signals.loc[signals["ret"] < -0.05, "signal"] = -1  # strong downtrend

    return signals


# ── Strategy 3: RSI Mean Reversion ──────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def rsi_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought).
    """
    signals = pd.DataFrame(index=df.index)
    signals["close"] = df["close"]
    signals["rsi"] = rsi(df["close"])

    signals["signal"] = 0
    signals.loc[signals["rsi"] < 30, "signal"] = 1    # oversold → buy
    signals.loc[signals["rsi"] > 70, "signal"] = -1   # overbought → sell

    return signals


# ── Strategy 4: Breakout ─────────────────────────────────────────

def breakout_signals(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Break above recent high = buy. Break below recent low = sell.
    """
    signals = pd.DataFrame(index=df.index)
    signals["close"] = df["close"]
    signals["high_max"] = df["high"].rolling(window).max().shift(1)
    signals["low_min"] = df["low"].rolling(window).min().shift(1)

    signals["signal"] = 0
    signals.loc[signals["close"] > signals["high_max"], "signal"] = 1
    signals.loc[signals["close"] < signals["low_min"], "signal"] = -1

    return signals


# ── Backtest engine (simple, vectorized) ─────────────────────────

def backtest_strategy(name: str, signal_func, data: dict,
                      test_start: str = TEST_START):
    """
    Run a simple backtest: when signal triggers, enter next day at open,
    exit after 5 days at close, or stop/target.

    Returns dict with metrics or None if < MIN_TRADES.
    Each trade dict includes exit_reason, confidence, and signal metadata.
    """
    all_trades = []

    for ticker, df in data.items():
        # Generate signals
        sig = signal_func(df)
        sig = sig[sig.index >= test_start]

        signal_days = sig[sig["signal"] != 0]
        if signal_days.empty:
            continue

        # Find next trading day's open for entry
        for idx, row in signal_days.iterrows():
            entry_idx = df.index.get_indexer([idx], method="bfill")[0] + 1
            if entry_idx >= len(df):
                continue

            entry_date = df.index[entry_idx]
            entry_price = df.iloc[entry_idx]["open"]
            direction = row["signal"]

            # 5-day hold
            exit_idx = min(entry_idx + 5, len(df) - 1)
            exit_date = df.index[exit_idx]
            exit_price = df.iloc[exit_idx]["close"]

            # Stop loss at 2 * ATR(14)
            recent = df.iloc[max(0, entry_idx - 14):entry_idx]
            tr = np.maximum(
                recent["high"] - recent["low"],
                np.maximum(
                    abs(recent["high"] - recent["close"].shift(1)),
                    abs(recent["low"] - recent["close"].shift(1)),
                )
            )
            atr = tr.mean()

            if direction == 1:  # BUY
                stop = entry_price - 2 * atr
                target = entry_price + 4 * atr
                held = df.iloc[entry_idx:exit_idx + 1]
                hit_stop = held["low"].min() <= stop
                hit_target = held["high"].max() >= target
            else:  # SELL
                stop = entry_price + 2 * atr
                target = entry_price - 4 * atr
                held = df.iloc[entry_idx:exit_idx + 1]
                hit_stop = held["high"].max() >= stop
                hit_target = held["low"].min() <= target

            if hit_stop:
                exit_reason = "stop_loss"
                exit_price = stop
                # Find which day stop was hit
                actual_exit = exit_idx
                for stop_idx in range(entry_idx, exit_idx + 1):
                    if (direction == 1 and df.iloc[stop_idx]["low"] <= stop) or \
                       (direction != 1 and df.iloc[stop_idx]["high"] >= stop):
                        exit_date = df.index[stop_idx]
                        actual_exit = stop_idx
                        break
            elif hit_target:
                exit_reason = "take_profit"
                exit_price = target
                actual_exit = exit_idx
                for tp_idx in range(entry_idx, exit_idx + 1):
                    if (direction == 1 and df.iloc[tp_idx]["high"] >= target) or \
                       (direction != 1 and df.iloc[tp_idx]["low"] <= target):
                        exit_date = df.index[tp_idx]
                        actual_exit = tp_idx
                        break
            else:
                exit_reason = "time_stop"
                actual_exit = exit_idx

            if direction == 1:
                pnl = (exit_price / entry_price - 1) - TOTAL_COST
            else:
                pnl = (entry_price / exit_price - 1) - TOTAL_COST

            days_held = actual_exit - entry_idx  # trading days

            # Compute confidence for volume divergence trades
            if name == "volume_divergence":
                sig_row = {
                    "vol_5": row.get("vol_5", 1.0),
                    "ret_5": row.get("ret_5", 0.0),
                    "bull_streak": row.get("bull_streak", 0),
                    "bear_streak": row.get("bear_streak", 0),
                }
                confidence = filter.score_signal(pd.Series(sig_row))
            else:
                confidence = 50

            all_trades.append({
                "ticker": ticker,
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "exit_date": exit_date.strftime("%Y-%m-%d"),
                "direction": "BUY" if direction == 1 else "SELL",
                "entry_price": float(round(entry_price, 2)),
                "exit_price": float(round(exit_price, 2)),
                "exit_reason": exit_reason,
                "pnl_pct": float(round(pnl * 100, 2)),
                "days_held": int(days_held),
                "confidence": int(confidence),
            })

    if len(all_trades) < 10:
        return None, []

    # Compute metrics
    df_trades = pd.DataFrame(all_trades)
    pnls = df_trades["pnl_pct"].values / 100
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    return {
        "strategy": name,
        "trades": len(pnls),
        "precision": float(len(wins) / len(pnls)),
        "avg_return": float(np.mean(pnls)),
        "total_return": float(np.sum(pnls)),
        "avg_win": float(np.mean(wins)) if len(wins) > 0 else 0,
        "avg_loss": float(np.mean(losses)) if len(losses) > 0 else 0,
        "profit_factor": (
            float(np.sum(wins) / abs(np.sum(losses)))
            if len(losses) > 0 and np.sum(losses) != 0
            else float("inf")
        ),
        "sharpe": float(np.mean(pnls) / np.std(pnls) * np.sqrt(252)) if np.std(pnls) > 0 else 0,
        "max_cons_losses": int(
            max(sum(1 for _ in g) for k, g in
                __import__('itertools').groupby(pnls <= 0) if k)
        ) if len(pnls) > 0 else 0,
    }, all_trades


# ── Save backtest trades to database ──────────────────────────

def save_backtest_trades(all_trades, strategy):
    """Save backtest trade results to the database for holding period analysis."""
    from track import connect

    conn = connect()
    try:
        for t in all_trades:
            conn.execute(
                """INSERT OR IGNORE INTO trades
                   (id, ticker, direction, entry_date, entry_price,
                    exit_date, exit_price, exit_reason, pnl_pct, days_held,
                    source, strategy, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"BT-{t['entry_date'].replace('-', '')}-{t['ticker']}-{strategy[:4]}",
                    t["ticker"], t["direction"],
                    t["entry_date"], t["entry_price"],
                    t["exit_date"], t["exit_price"],
                    t.get("exit_reason", "time_stop"),
                    t["pnl_pct"], t["days_held"],
                    "backtest", strategy, t.get("confidence", 50),
                ),
            )
        conn.commit()
        log.info(f"Saved {len(all_trades)} backtest trades for {strategy}")
    finally:
        conn.close()


# ── Run all strategies ──────────────────────────────────────────

def run():
    """Run all strategies, print ranked results. Saves trades to DB."""
    data = load_all()

    strategies = [
        ("volume_divergence", volume_divergence_signals),
        ("momentum_20d", lambda df: momentum_signals(df, 20)),
        ("rsi_mean_reversion", rsi_signals),
        ("breakout_20d", lambda df: breakout_signals(df, 20)),
    ]

    # Clear old backtest trades before re-running
    from track import connect
    db_conn = connect()
    try:
        db_conn.execute("DELETE FROM trades WHERE source = 'backtest'")
        db_conn.commit()
    finally:
        db_conn.close()

    results = []
    for name, func in strategies:
        log.info(f"Testing {name}...")
        result, trades = backtest_strategy(name, func, data)
        if result:
            results.append(result)
            save_backtest_trades(trades, name)
            log.info(f"  {name}: {result['trades']} trades, "
                     f"precision {result['precision']*100:.1f}%, "
                     f"sharpe {result['sharpe']:.2f}")
        else:
            log.warning(f"  {name}: insufficient trades")

    if not results:
        print("\nNo strategy produced enough trades for evaluation.")
        return None

    ranked = sorted(results, key=lambda r: r["sharpe"], reverse=True)

    print("\n" + "=" * 70)
    print("STRATEGY RANKING (sorted by Sharpe)")
    print("=" * 70)
    print(f"{'Rank':<6} {'Strategy':<22} {'Trades':<8} {'Prec':<8} "
          f"{'Sharpe':<8} {'AvgRet':<8} {'PF':<8} {'MaxLoss':<8}")
    print("-" * 70)

    for i, r in enumerate(ranked, 1):
        print(f"{i:<6} {r['strategy']:<22} {r['trades']:<8} "
              f"{r['precision']*100:<7.1f}% "
              f"{r['sharpe']:<8.2f} "
              f"{r['avg_return']*100:<+7.2f}% "
              f"{r['profit_factor']:<8.2f} "
              f"{r['max_cons_losses']:<8}")

    print("=" * 70)

    # Also show buy-and-hold benchmark
    print("\nBenchmark — Buy & Hold IHSG (approximate):")
    benchmark_returns = []
    for ticker, df in data.items():
        test = df[df.index >= TEST_START]
        if len(test) > 0:
            ret = (test["close"].iloc[-1] / test["close"].iloc[0] - 1)
            benchmark_returns.append(ret)
    if benchmark_returns:
        avg_bh = np.mean(benchmark_returns)
        print(f"  Avg stock return: {avg_bh*100:+.2f}% (universe)"
              f"\n  Annualized: {((1+avg_bh)**(252/len(test))-1)*100:.1f}%")

    print("\nRecommended: pick the strategy with highest Sharpe AND precision > 55%.")
    print("If none satisfy, extend the research period or add new strategies.\n")

    return ranked


if __name__ == "__main__":
    run()
