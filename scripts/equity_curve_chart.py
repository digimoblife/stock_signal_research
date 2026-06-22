"""
Generate equity curve comparison chart for all 5 strategy variants.
Run: python scripts/equity_curve_chart.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import DATA_DIR, TOTAL_COST
from universe import get_universe
from research import load_ticker

DATA_DIR = Path(DATA_DIR)
INITIAL_CAPITAL = 100_000_000
MAX_POSITIONS = 10

VARIANTS = {
    "A_CURRENT": {"stop_atr": 2, "target_atr": 4, "hold": 5, "vol_min": 0, "caps": "all"},
    "B_OPTIMIZED": {"stop_atr": 3, "target_atr": None, "hold": 15, "vol_min": 2.0, "caps": "large_mid", "regime": "bear"},
    "C_BALANCED": {"stop_atr": 3, "target_atr": None, "hold": 15, "vol_min": 2.0, "caps": "large_mid"},
    "D_CONSERVATIVE": {"stop_atr": 3, "target_atr": None, "hold": 15, "vol_min": 2.5, "caps": "large"},
    "E_TRAILING": {"stop_atr": 3, "target_atr": None, "hold": 20, "vol_min": 2.0, "caps": "large_mid", "trail_atr": 2},
}


def compute_atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def classify_liquidity_series(df, ticker_volumes):
    med_vol = df["volume"].median()
    ticker_med = ticker_volumes.get(df.name, med_vol)
    if ticker_med is None:
        return "mid"
    return "large" if ticker_med >= med_vol else "mid"


def portfolio_backtest_variant(data, variant_params):
    stop_atr = variant_params["stop_atr"]
    target_atr = variant_params["target_atr"]
    hold = variant_params["hold"]
    vol_min = variant_params["vol_min"]
    caps = variant_params["caps"]
    regime_filter = variant_params.get("regime")
    trail_atr = variant_params.get("trail_atr")

    ticker_volumes = {t: data[t]["volume"].median() for t in data}

    equity = pd.Series(dtype=float)
    all_dates = pd.DatetimeIndex(sorted({
        d for df in data.values() for d in df.index
        if d >= pd.Timestamp("2019-01-01")
    }))

    cash = INITIAL_CAPITAL
    positions = []
    equity_curve = []

    for date in all_dates:
        for pos in list(positions):
            ticker = pos["ticker"]
            df = data[ticker]
            if date not in df.index:
                continue
            row = df.loc[date]
            direction = pos["direction"]

            if direction == 1:
                hit_stop = row["low"] <= pos["stop"]
                hit_target = target_atr and row["high"] >= pos["target"]
            else:
                hit_stop = row["high"] >= pos["stop"]
                hit_target = target_atr and row["low"] <= pos["target"]

            trail_hit = False
            if trail_atr and pos.get("highest_close"):
                new_trail = pos["highest_close"] - trail_atr * pos["atr"]
                pos["stop"] = max(pos["stop"], new_trail) if direction == 1 else min(pos["stop"], new_trail)
            pos["highest_close"] = max(pos.get("highest_close", 0), row["close"]) if direction == 1 else min(pos.get("highest_close", float("inf")), row["close"])

            if direction == 1:
                trail_check = row["low"] <= pos["stop"]
            else:
                trail_check = row["high"] >= pos["stop"]
            if trail_atr and trail_check:
                trail_hit = True

            days = pos["days"] + 1
            pos["days"] = days

            if hit_stop or hit_target or trail_hit or days > hold:
                if direction == 1:
                    pnl = (pos["stop"] / pos["entry_price"] - 1) - TOTAL_COST if hit_stop or trail_hit else \
                          (pos["target"] / pos["entry_price"] - 1) - TOTAL_COST if hit_target else \
                          (row["close"] / pos["entry_price"] - 1) - TOTAL_COST
                else:
                    pnl = (pos["entry_price"] / pos["stop"] - 1) - TOTAL_COST if hit_stop or trail_hit else \
                          (pos["entry_price"] / pos["target"] - 1) - TOTAL_COST if hit_target else \
                          (pos["entry_price"] / row["close"] - 1) - TOTAL_COST
                cash += pos["position_size"] * (1 + pnl)
                positions.remove(pos)

        for ticker in data:
            if ticker in [p["ticker"] for p in positions]:
                continue
            if len(positions) >= MAX_POSITIONS:
                break
            df = data[ticker]
            if date not in df.index:
                continue

            idx = df.index.get_loc(date)
            if idx + 1 >= len(df):
                continue
            row = df.loc[date]

            ret_5 = df["close"].pct_change(5).iloc[idx] if idx >= 5 else 0
            vol_5 = df["volume"].iloc[idx] / df["volume"].iloc[max(0, idx-5):idx].mean() if idx >= 5 else 0

            if vol_5 <= 0 or (vol_min > 0 and vol_5 < vol_min):
                continue

            bull_div = ret_5 < -0.02 and vol_5 > 1.3
            bear_div = ret_5 > 0.02 and vol_5 < 0.7
            if not bull_div and not bear_div:
                continue

            bull_streak = 0
            bear_streak = 0
            for i in range(max(0, idx-2), idx+1):
                r = df["close"].pct_change(5).iloc[i] if i >= 5 else 0
                v = df["volume"].iloc[i] / df["volume"].iloc[max(0, i-5):i].mean() if i >= 5 else 1
                if r < -0.02 and v > 1.3:
                    bull_streak += 1
                elif r > 0.02 and v < 0.7:
                    bear_streak += 1
            if bull_streak < 2 and bear_streak < 2:
                continue

            direction = 1 if bull_streak >= 2 else -1

            liq = classify_liquidity_series(df, ticker_volumes)
            if caps == "large" and liq != "large":
                continue
            if caps == "large_mid" and liq not in ("large", "mid"):
                continue

            if regime_filter == "bear":
                mkt_ret = sum(
                    d["close"].pct_change(20).iloc[-1]
                    for d in data.values() if len(d) > 20
                ) / len(data)
                if mkt_ret is None or mkt_ret >= -0.03:
                    continue

            next_idx = idx + 1
            entry_price = df.iloc[next_idx]["open"]
            atr_val = compute_atr(df["high"], df["low"], df["close"]).iloc[next_idx]
            if pd.isna(atr_val) or atr_val <= 0:
                continue

            stop = entry_price - stop_atr * atr_val if direction == 1 else entry_price + stop_atr * atr_val
            target = entry_price + target_atr * atr_val if direction == 1 and target_atr else \
                     entry_price - target_atr * atr_val if direction == -1 and target_atr else None

            pos_size = cash / max(1, MAX_POSITIONS - len(positions) + 1)
            pos_size = min(pos_size, cash)

            positions.append({
                "ticker": ticker,
                "direction": direction,
                "entry_price": entry_price,
                "stop": stop,
                "target": target,
                "atr": atr_val,
                "position_size": pos_size,
                "days": 0,
                "highest_close": entry_price,
            })
            cash -= pos_size

        portfolio_value = cash + sum(p["position_size"] for p in positions)
        equity_curve.append({"date": date, "equity": portfolio_value})

    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_df["equity"] = eq_df["equity"].ffill()
    return eq_df


def main():
    data = {}
    for t in get_universe():
        df = load_ticker(t)
        if df.empty or len(df) < 100:
            continue
        df.name = t
        data[t] = df
    print(f"Loaded {len(data)} tickers")

    curves = {}
    for name, params in VARIANTS.items():
        print(f"Backtesting {name}...")
        eq = portfolio_backtest_variant(data, params)
        curves[name] = eq
        cagr = ((eq["equity"].iloc[-1] / INITIAL_CAPITAL) ** (252 / len(eq)) - 1) * 100
        dd = (eq["equity"] / eq["equity"].cummax() - 1).min() * 100
        yr = (eq["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100
        print(f"  CAGR: {cagr:+.2f}%  Total: {yr:+.1f}%  MaxDD: {dd:.1f}%")

    fig, ax = plt.subplots(figsize=(14, 7))
    colors = {"A_CURRENT": "#ff6b6b", "B_OPTIMIZED": "#ffd93d", "C_BALANCED": "#6bcb77",
              "D_CONSERVATIVE": "#4d96ff", "E_TRAILING": "#ff6bff"}
    styles = {"A_CURRENT": "--", "B_OPTIMIZED": ":", "C_BALANCED": "-",
              "D_CONSERVATIVE": "-.", "E_TRAILING": ":"}

    for name in ["A_CURRENT", "C_BALANCED", "D_CONSERVATIVE", "B_OPTIMIZED", "E_TRAILING"]:
        eq = curves[name]
        eq["equity_pct"] = (eq["equity"] / INITIAL_CAPITAL - 1) * 100
        ax.plot(eq.index, eq["equity_pct"], label=name, color=colors[name],
                linestyle=styles[name], linewidth=2 if name == "C_BALANCED" else 1)

    ax.axhline(y=0, color="gray", linestyle="-", linewidth=0.5, alpha=0.5)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter('%.0f%%'))
    ax.set_xlabel("Date")
    ax.set_ylabel("Return (%)")
    ax.set_title("Portfolio Equity Curve Comparison — All Variants (2019–2026)")
    ax.legend(loc="best", framealpha=0.9)
    ax.grid(True, alpha=0.3)

    out = Path(__file__).resolve().parent.parent / "exports" / "equity_curve_comparison.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\nChart saved: {out}")
    plt.close(fig)


if __name__ == "__main__":
    main()
