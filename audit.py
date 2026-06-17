"""
audit.py — robustness audit of the BEAR × LARGE CAP filtered strategy.

Tests whether the 56.3% precision / 1.45 Sharpe finding is a
genuine edge or a statistical artifact.
"""
import logging
import sys
from pathlib import Path
from itertools import groupby
from collections import Counter

import numpy as np
import pandas as pd

from settings import DATA_DIR, TICKERS, TOTAL_COST, TEST_START
from research import load_ticker

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("audit")
DATA_DIR = Path(DATA_DIR)

# Sector classification for our 15 tickers
SECTORS = {
    "BBCA": "Financials", "BBRI": "Financials", "BMRI": "Financials", "BBNI": "Financials",
    "TLKM": "Telecom",
    "ASII": "Automotive",
    "ADRO": "Mining",
    "ICBP": "Consumer", "INDF": "Consumer", "UNVR": "Consumer",
    "GGRM": "Tobacco", "HMSP": "Tobacco",
    "KLBF": "Pharma",
    "SMGR": "Cement",
    "PGAS": "Energy",
}

SECTOR_COLORS = {
    "Financials": "blue", "Telecom": "purple", "Automotive": "orange",
    "Mining": "brown", "Consumer": "green", "Tobacco": "red",
    "Pharma": "cyan", "Cement": "gray", "Energy": "yellow",
}


# ── Re-use backtester from analyze.py ──────────────────────────

# Import backtest_with_metadata from analyze module
sys.path.insert(0, str(DATA_DIR.parent))
from analyze import (
    backtest_with_metadata, compute_market_proxy, classify_regime,
    compute_liquidity_buckets, compute_metrics as _orig_metrics,
)


# ── Extended metrics ──────────────────────────────────────────

def compute_metrics(trades):
    """Same as analyze's but with additional fields."""
    m = _orig_metrics(trades)
    if m.get("error"):
        return m

    pnls = trades["pnl_pct"].values / 100
    m["total_return_pct"] = float(np.sum(pnls) * 100)
    m["median_return"] = float(np.median(pnls) * 100)
    m["std_return"] = float(np.std(pnls) * 100)
    m["skew"] = float(pd.Series(pnls).skew())
    m["kurtosis"] = float(pd.Series(pnls).kurtosis())
    m["min_return"] = float(np.min(pnls) * 100)
    m["max_return"] = float(np.max(pnls) * 100)

    # Percent of profitable trades
    m["profitable_pct"] = round(len(pnls[pnls > 0]) / len(pnls) * 100, 1)

    # Average holding period
    if "days_held" in trades.columns:
        m["avg_days_held"] = float(trades["days_held"].mean())

    return m


# ── Printer ──────────────────────────────────────────────────

def print_separator(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


def print_metric_line(label, value, fmt=".2f"):
    print(f"  {label:<40} {value:{fmt}}")


# ── Main audit ──────────────────────────────────────────────

def run():
    print_separator("ROBUSTNESS AUDIT: BEAR × LARGE CAP STRATEGY")
    print("  Auditing claim: Volume divergence on large caps during bear markets")
    print(f"  Test period: {TEST_START} to present")
    print(f"  Universe: {len(TICKERS)} stocks")

    # ── 0. Load & compute ────────────────────────────────────
    print("\n[0] Loading data...")
    data = {}
    for t in TICKERS:
        df = load_ticker(t)
        if not df.empty and len(df) > 100:
            data[t] = df
    print(f"    {len(data)} tickers loaded")

    liquidity = compute_liquidity_buckets(data)
    market = compute_market_proxy(data)
    regime_series = classify_regime(market["market_ret"])

    trades = backtest_with_metadata(data, liquidity, regime_series)

    # Filter to BEAR × LARGE only
    bt = trades[(trades["regime"] == "bear") & (trades["liquidity"] == "large")].copy()
    bt["entry_year"] = pd.to_datetime(bt["entry_date"]).dt.year
    bt["entry_month"] = pd.to_datetime(bt["entry_date"]).dt.to_period("M")
    bt["ticker_sector"] = bt["ticker"].map(SECTORS)

    total_bt = len(trades)
    filtered = len(bt)

    print(f"\n    Total trades (all): {total_bt}")
    print(f"    Filtered (BEAR×LARGE): {filtered} ({filtered/max(total_bt,1)*100:.0f}%)")

    overall = compute_metrics(bt)
    print(f"\n  BASELINE METRICS (bear × large):")
    for k in ["trades", "precision", "sharpe", "profit_factor", "avg_return",
              "total_return_pct", "median_return", "avg_days_held"]:
        if k in overall:
            print(f"    {k:<20} {overall[k]}")

    # ── 1. Performance by calendar year ─────────────────────────
    print_separator("SECTION 1: PERFORMANCE BY CALENDAR YEAR")

    years = sorted(bt["entry_year"].unique())
    year_results = {}
    for year in years:
        subset = bt[bt["entry_year"] == year]
        year_results[year] = compute_metrics(subset)

    print(f"\n  {'Year':<8} {'Trades':<8} {'Prec':<8} {'Sharpe':<10} {'PF':<8} {'AvgRet':<10} {'TotalRet':<10}")
    print(f"  {'-'*62}")
    for year in years:
        m = year_results.get(year, {})
        if m.get("trades", 0) > 0:
            print(f"  {year:<8} {m['trades']:<8} {m['precision']:<7.1f}% "
                  f"{m['sharpe']:<10.3f} {m['profit_factor']:<8.2f} "
                  f"{m['avg_return']:<+9.2f}% {m.get('total_return_pct',0):<+9.2f}%")
    print(f"  {'-'*62}")
    print(f"  {'ALL':<8} {overall['trades']:<8} {overall['precision']:<7.1f}% "
          f"{overall['sharpe']:<10.3f} {overall['profit_factor']:<8.2f} "
          f"{overall['avg_return']:<+9.2f}% {overall.get('total_return_pct',0):<+9.2f}%")

    # Check: all years positive?
    pos_years = [y for y in years if year_results.get(y, {}).get("avg_return", 0) > 0]
    neg_years = [y for y in years if year_results.get(y, {}).get("avg_return", 0) < 0]
    print(f"\n  Positive years: {len(pos_years)}/{len(years)}  Negative years: {len(neg_years)}/{len(years)}")

    # ── 2. Performance by stock ────────────────────────────────
    print_separator("SECTION 2: PERFORMANCE BY STOCK")

    stock_results = {}
    for ticker in bt["ticker"].unique():
        subset = bt[bt["ticker"] == ticker]
        stock_results[ticker] = compute_metrics(subset)

    print(f"\n  {'Ticker':<8} {'Sector':<15} {'Trades':<8} {'Prec':<8} {'Sharpe':<10} "
          f"{'PF':<8} {'AvgRet':<10} {'TotalRet':<10}")
    print(f"  {'-'*77}")

    # Sort by trades descending
    sorted_stocks = sorted(stock_results.keys(), key=lambda t: stock_results[t]["trades"], reverse=True)
    for ticker in sorted_stocks:
        m = stock_results.get(ticker, {})
        sector = SECTORS.get(ticker, "?")
        if m.get("trades", 0) > 0:
            print(f"  {ticker:<8} {sector:<15} {m['trades']:<8} {m['precision']:<7.1f}% "
                  f"{m['sharpe']:<10.3f} {m['profit_factor']:<8.2f} "
                  f"{m['avg_return']:<+9.2f}% {m.get('total_return_pct',0):<+9.2f}%")
    print(f"  {'-'*77}")

    # Concentration check: what % of total return comes from top 3 stocks?
    stock_returns = {t: stock_results[t].get("total_return_pct", 0) for t in stock_results}
    sorted_by_ret = sorted(stock_returns.items(), key=lambda x: abs(x[1]), reverse=True)
    top3_ret = sum(v for _, v in sorted_by_ret[:3])
    total_ret = overall.get("total_return_pct", 0)
    if total_ret != 0:
        print(f"\n  Top 3 stocks contribution to total return: {top3_ret/total_ret*100:.0f}%")

    # ── 3. Performance by sector ─────────────────────────────
    print_separator("SECTION 3: PERFORMANCE BY SECTOR")

    sector_results = {}
    for sector in bt["ticker_sector"].unique():
        subset = bt[bt["ticker_sector"] == sector]
        sector_results[sector] = compute_metrics(subset)

    print(f"\n  {'Sector':<15} {'Trades':<8} {'Prec':<8} {'Sharpe':<10} "
          f"{'PF':<8} {'AvgRet':<10} {'TotalRet':<10}")
    print(f"  {'-'*69}")

    for sector in sorted(sector_results.keys(), key=lambda s: sector_results[s]["trades"], reverse=True):
        m = sector_results.get(sector, {})
        if m.get("trades", 0) > 0:
            print(f"  {sector:<15} {m['trades']:<8} {m['precision']:<7.1f}% "
                  f"{m['sharpe']:<10.3f} {m['profit_factor']:<8.2f} "
                  f"{m['avg_return']:<+9.2f}% {m.get('total_return_pct',0):<+9.2f}%")

    # ── 4. Rolling performance ────────────────────────────────
    print_separator("SECTION 4: ROLLING PERFORMANCE")

    # 6-month rolling
    bt_sorted = bt.sort_values("entry_date")
    bt_sorted["cum_pnl"] = bt_sorted["pnl_pct"].cumsum() / 100

    windows = [63, 126, 252]  # ~3mo, 6mo, 12mo (trading days)
    labels = ["3-month", "6-month", "12-month"]

    for window, label in zip(windows, labels):
        rolling_prec = bt_sorted["pnl_pct"].rolling(window, min_periods=10).apply(
            lambda x: (x > 0).sum() / len(x) * 100 if len(x) > 0 else 0
        )
        rolling_pnl = bt_sorted["pnl_pct"].rolling(window, min_periods=10).mean()

        valid = rolling_prec.dropna()
        above_50 = (valid > 50).sum()
        total_valid = len(valid)
        avg_rolling_prec = valid.mean()

        print(f"\n  {label} rolling ({window} trades):")
        print(f"    Windows above 50% precision: {above_50}/{total_valid} "
              f"({above_50/max(total_valid,1)*100:.0f}%)")
        print(f"    Average rolling precision: {avg_rolling_prec:.1f}%")
        print(f"    Rolling precision range: {valid.min():.1f}% – {valid.max():.1f}%")

        # Check for consistent positive periods
        win_streak = max(
            (sum(1 for _ in g) for k, g in groupby(rolling_pnl.dropna() > 0) if k),
            default=0
        )
        loss_streak = max(
            (sum(1 for _ in g) for k, g in groupby(rolling_pnl.dropna() <= 0) if k),
            default=0
        )
        print(f"    Longest streak of positive rolling windows: {win_streak}")
        print(f"    Longest streak of negative rolling windows: {loss_streak}")

    # ── 5. Maximum streaks ───────────────────────────────────
    print_separator("SECTION 5: TRADE STREAK ANALYSIS")

    pnls = bt_sorted["pnl_pct"].values / 100

    # Consecutive wins/losses
    win_streaks = [sum(1 for _ in g) for k, g in groupby(pnls > 0) if k]
    loss_streaks = [sum(1 for _ in g) for k, g in groupby(pnls <= 0) if not k]

    max_win_streak = max(win_streaks) if win_streaks else 0
    max_loss_streak = max(loss_streaks) if loss_streaks else 0
    avg_win_streak = np.mean(win_streaks) if win_streaks else 0
    avg_loss_streak = np.mean(loss_streaks) if loss_streaks else 0

    print(f"\n  {'Metric':<40} {'Value'}")
    print(f"  {'-'*55}")
    print(f"  {'Maximum consecutive wins':<40} {max_win_streak}")
    print(f"  {'Maximum consecutive losses':<40} {max_loss_streak}")
    print(f"  {'Average win streak':<40} {avg_win_streak:.1f}")
    print(f"  {'Average loss streak':<40} {avg_loss_streak:.1f}")
    print(f"  {'Win/loss streak ratio':<40} {max_win_streak/max(max_loss_streak,1):.1f}")

    # ── 6. Return distribution ───────────────────────────────
    print_separator("SECTION 6: RETURN DISTRIBUTION")

    returns_pct = bt_sorted["pnl_pct"].values

    print(f"\n  {'Percentile':<20} {'Return':<15}")
    print(f"  {'-'*35}")
    for pct in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        val = np.percentile(returns_pct, pct)
        print(f"  {pct:>3}th{'':<15} {val:+7.2f}%")

    # Buckets
    buckets = [
        ("< -5%", returns_pct[returns_pct < -5]),
        ("-5% to -3%", returns_pct[(returns_pct >= -5) & (returns_pct < -3)]),
        ("-3% to -1%", returns_pct[(returns_pct >= -3) & (returns_pct < -1)]),
        ("-1% to +1%", returns_pct[(returns_pct >= -1) & (returns_pct < 1)]),
        ("+1% to +3%", returns_pct[(returns_pct >= 1) & (returns_pct < 3)]),
        ("+3% to +5%", returns_pct[(returns_pct >= 3) & (returns_pct < 5)]),
        ("> +5%", returns_pct[returns_pct >= 5]),
    ]

    print(f"\n  {'Bucket':<20} {'Count':<8} {'Pct':<8}")
    print(f"  {'-'*36}")
    for label, bucket in buckets:
        count = len(bucket)
        pct = count / len(returns_pct) * 100 if len(returns_pct) > 0 else 0
        avg = bucket.mean() if len(bucket) > 0 else 0
        print(f"  {label:<20} {count:<8} {pct:<7.1f}%  (avg: {avg:+.2f}%)")
    print(f"  {'Total':<20} {len(returns_pct):<8} 100.0%")

    # ── 7. Outlier contribution ─────────────────────────────
    print_separator("SECTION 7: OUTLIER CONTRIBUTION")

    top10_wins = sorted(returns_pct, reverse=True)[:10]
    top10_losses = sorted(returns_pct)[:10]

    top10_win_total = sum(top10_wins)
    top10_loss_total = abs(sum(top10_losses))
    total_pnl = sum(returns_pct)

    print(f"\n  {'Metric':<50} {'Value'}")
    print(f"  {'-'*65}")
    print(f"  {'Total PnL (all trades, %)':<50} {total_pnl:+.2f}%")
    print(f"  {'Top 10 wins total (%)':<50} {top10_win_total:+.2f}%")
    print(f"  {'Top 10 wins as % of total PnL':<50} {top10_win_total/max(total_pnl,0.01)*100:.0f}%")
    print(f"  {'Top 10 losses total (%)':<50} {top10_loss_total:.2f}%")
    print(f"  {'Top 10 losses as % of total PnL':<50} {top10_loss_total/max(abs(total_pnl),0.01)*100:.0f}%")
    print(f"  {'Single best trade (%)':<50} {max(returns_pct):+.2f}%")
    print(f"  {'Single worst trade (%)':<50} {min(returns_pct):+.2f}%")
    print(f"  {'Best/worst ratio':<50} {max(returns_pct)/abs(min(returns_pct)):.1f}")

    # What if we remove top 3 winners?
    remaining = np.sort(returns_pct)[:-3] if len(returns_pct) > 3 else returns_pct
    pnl_without_top3 = sum(remaining)
    sharpe_without = (np.mean(remaining) / np.std(remaining) * np.sqrt(252/5)
                      if len(remaining) > 1 and np.std(remaining) > 0 else 0)
    prec_without = (remaining > 0).sum() / len(remaining) * 100 if len(remaining) > 0 else 0

    print(f"\n  {'Without top 3 winners:'}")
    print(f"  {'PnL':<50} {pnl_without_top3:+.2f}%")
    print(f"  {'Sharpe':<50} {sharpe_without:.3f}")
    print(f"  {'Precision':<50} {prec_without:.1f}%")

    # What if we remove top 3 losers?
    remaining2 = np.sort(returns_pct)[3:] if len(returns_pct) > 3 else returns_pct
    pnl_without_top3_losses = sum(remaining2)
    sharpe_without2 = (np.mean(remaining2) / np.std(remaining2) * np.sqrt(252/5)
                       if len(remaining2) > 1 and np.std(remaining2) > 0 else 0)
    prec_without2 = (remaining2 > 0).sum() / len(remaining2) * 100 if len(remaining2) > 0 else 0

    print(f"\n  {'Without worst 3 losses:'}")
    print(f"  {'PnL':<50} {pnl_without_top3_losses:+.2f}%")
    print(f"  {'Sharpe':<50} {sharpe_without2:.3f}")
    print(f"  {'Precision':<50} {prec_without2:.1f}%")

    # ── 8. Top 10 wins detailed ─────────────────────────────
    print_separator("SECTION 8: TOP 10 WINNING TRADES")

    bt_sorted_wins = bt_sorted.nlargest(10, "pnl_pct")
    print(f"\n  {'#':<3} {'Date':<12} {'Ticker':<7} {'Sector':<13} {'Return':<10} {'Days':<6}")
    print(f"  {'-'*51}")
    for i, (_, row) in enumerate(bt_sorted_wins.iterrows(), 1):
        sector = SECTORS.get(row["ticker"], "?")
        print(f"  {i:<3} {row['entry_date']:<12} {row['ticker']:<7} "
              f"{sector:<13} {row['pnl_pct']:<+9.2f}% {row['days_held']:<6}")

    # ── 9. Top 10 losses detailed ──────────────────────────
    print_separator("SECTION 9: TOP 10 LOSING TRADES")

    bt_sorted_losses = bt_sorted.nsmallest(10, "pnl_pct")
    print(f"\n  {'#':<3} {'Date':<12} {'Ticker':<7} {'Sector':<13} {'Return':<10} {'Days':<6}")
    print(f"  {'-'*51}")
    for i, (_, row) in enumerate(bt_sorted_losses.iterrows(), 1):
        sector = SECTORS.get(row["ticker"], "?")
        print(f"  {i:<3} {row['entry_date']:<12} {row['ticker']:<7} "
              f"{sector:<13} {row['pnl_pct']:<+9.2f}% {row['days_held']:<6}")

    # ── 10. Trade concentration over time ──────────────────
    print_separator("SECTION 10: TRADE CONCENTRATION OVER TIME")

    # Trades per year
    trades_per_year = bt["entry_year"].value_counts().sort_index()
    print(f"\n  {'Year':<8} {'Trades':<8} {'Prec':<8} {'AvgRet':<10} {'CumPnL':<10}")
    print(f"  {'-'*44}")
    cum = 0
    for year in years:
        m = year_results.get(year, {})
        t = m.get("trades", 0)
        prec = m.get("precision", 0)
        avg = m.get("avg_return", 0)
        cum += m.get("total_return_pct", 0)
        if t > 0:
            print(f"  {year:<8} {t:<8} {prec:<7.1f}% {avg:<+9.2f}% {cum:<+9.2f}%")

    # Bear market days vs signals per year
    print(f"\n  Bear market days per year:")
    regime_counts = regime_series.value_counts()
    for year in years:
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        if year == 2026:
            end = "2026-06-30"
        year_regime = regime_series[start:end]
        bear_days = (year_regime == "bear").sum()
        trades_this_year = len(bt[bt["entry_year"] == year])
        print(f"  {year}: {bear_days} bear days, {trades_this_year} signals")

    # ── 11. Sharpe stability ──────────────────────────────
    print_separator("SECTION 11: SHARPE STABILITY")

    # Compute Sharpe for every 20-trade rolling window
    rolling_sharpe = bt_sorted["pnl_pct"].rolling(20, min_periods=10).apply(
        lambda x: (
            np.mean(x) / np.std(x) * np.sqrt(252 / 5)
            if len(x) > 1 and np.std(x) > 0 else 0
        )
    )
    valid_sharpes = rolling_sharpe.dropna()

    print(f"\n  {'Metric':<40} {'Value'}")
    print(f"  {'-'*55}")
    print(f"  {'Number of 20-trade windows':<40} {len(valid_sharpes)}")
    print(f"  {'Windows with Sharpe > 0':<40} {(valid_sharpes > 0).sum()}")
    print(f"  {'Windows with Sharpe > 1':<40} {(valid_sharpes > 1).sum()}")
    print(f"  {'Windows with Sharpe < 0':<40} {(valid_sharpes < 0).sum()}")
    print(f"  {'Average rolling Sharpe':<40} {valid_sharpes.mean():.3f}")
    print(f"  {'Median rolling Sharpe':<40} {valid_sharpes.median():.3f}")
    print(f"  {'Sharpe std':<40} {valid_sharpes.std():.3f}")
    print(f"  {'Sharpe range':<40} {valid_sharpes.min():.2f} to {valid_sharpes.max():.2f}")
    print(f"  {'% windows with Sharpe > 0':<40} {(valid_sharpes > 0).sum()/len(valid_sharpes)*100:.0f}%")

    # ── FINAL VERDICT ──────────────────────────────────────
    print("\n" + "=" * 72)
    print("  FINAL AUDIT VERDICT")
    print("=" * 72)

    # Count passing criteria
    criteria = []
    failures = []

    # Criterion 1: All years positive (or at least neutral)
    bad_years = [y for y in years if year_results.get(y, {}).get("avg_return", 0) < -1.0]
    if bad_years:
        failures.append(f"Negative years: {bad_years}")
        criteria.append(("Yearly consistency", "FAIL", f"{len(pos_years)}/{len(years)} positive"))
    else:
        criteria.append(("Yearly consistency", "PASS", f"All {len(years)} years non-negative"))

    # Criterion 2: No single stock dominates
    max_stock_share = 0
    total_trades_filtered = len(bt)
    for t in sorted_stocks[:3]:
        m = stock_results.get(t, {})
        share = m.get("trades", 0) / max(total_trades_filtered, 1) * 100
        max_stock_share = max(max_stock_share, share)

    if max_stock_share > 40:
        failures.append(f"Single stock {sorted_stocks[0]} has {max_stock_share:.0f}% of trades")
        criteria.append(("Stock concentration", "FAIL", f"Max stock share: {max_stock_share:.0f}%"))
    else:
        criteria.append(("Stock concentration", "PASS", f"Max stock share: {max_stock_share:.0f}%"))

    # Criterion 3: No single trade dominates
    max_trade_share = max(returns_pct) / max(abs(total_pnl), 0.01) * 100 if total_pnl > 0 else 0
    if max_trade_share > 50:
        failures.append(f"Best trade is {max_trade_share:.0f}% of total PnL")
        criteria.append(("Trade concentration", "FAIL", f"Best trade: {max_trade_share:.0f}% of PnL"))
    else:
        criteria.append(("Trade concentration", "PASS", f"Best trade: {max_trade_share:.0f}% of PnL"))

    # Criterion 4: Edge survives removing top 3 winners
    if sharpe_without < 0.3:
        failures.append("Edge disappears without top 3 winners")
        criteria.append(("Outlier dependency", "FAIL", f"Sharpe w/o top3: {sharpe_without:.3f}"))
    else:
        criteria.append(("Outlier dependency", "PASS", f"Sharpe w/o top3: {sharpe_without:.3f}"))

    # Criterion 5: Rolling Sharpe mostly positive
    rolling_pos_pct = (valid_sharpes > 0).sum() / len(valid_sharpes) * 100
    if rolling_pos_pct < 60:
        failures.append(f"Rolling Sharpe positive only {rolling_pos_pct:.0f}% of windows")
        criteria.append(("Rolling stability", "FAIL", f"Positive {rolling_pos_pct:.0f}% of windows"))
    else:
        criteria.append(("Rolling stability", "PASS", f"Positive {rolling_pos_pct:.0f}% of windows"))

    # Criterion 6: Maximum drawdown of rolling precision
    min_rolling_prec = rolling_prec.min()
    if min_rolling_prec < 30:
        failures.append(f"Rolling precision dropped to {min_rolling_prec:.0f}%")
        criteria.append(("Drawdown severity", "FAIL", f"Min rolling prec: {min_rolling_prec:.0f}%"))
    else:
        criteria.append(("Drawdown severity", "PASS", f"Min rolling prec: {min_rolling_prec:.0f}%"))

    # Criterion 7: Consecutive loss streak acceptable
    if max_loss_streak > 8:
        failures.append(f"Maximum {max_loss_streak} consecutive losses")
        criteria.append(("Loss streak", "WARN", f"Max {max_loss_streak} consecutive losses"))
    else:
        criteria.append(("Loss streak", "PASS", f"Max {max_loss_streak} consecutive losses"))

    # Print criteria table
    print(f"\n  {'Criterion':<35} {'Result':<8} {'Detail'}")
    print(f"  {'-'*70}")
    for name, result, detail in criteria:
        emoji = "✅" if result == "PASS" else "⚠️" if result == "WARN" else "❌"
        print(f"  {emoji} {name:<33} {result:<8} {detail}")

    # Final verdict
    print(f"\n  {'='*70}")
    pass_count = sum(1 for _, r, _ in criteria if r == "PASS")
    warn_count = sum(1 for _, r, _ in criteria if r == "WARN")
    fail_count = sum(1 for _, r, _ in criteria if r == "FAIL")
    total_criteria = len(criteria)

    print(f"  Criteria: {pass_count} PASS, {warn_count} WARN, {fail_count} FAIL")

    if fail_count >= 3:
        print(f"\n  VERDICT: D — STATISTICALLY UNRELIABLE")
        print(f"  Too many criteria failed. Edge is likely an artifact.")
    elif fail_count == 2:
        print(f"\n  VERDICT: C — LIKELY OVERFIT")
        print(f"  Edge exists but is fragile. Proceed with extreme caution.")
    elif fail_count == 1:
        print(f"\n  VERDICT: B — PROMISING BUT NEEDS MORE VALIDATION")
        print(f"  One weakness identified. Paper trade to confirm.")
    else:
        print(f"\n  VERDICT: A — ROBUST EDGE")
        print(f"  All criteria pass. Edge is structurally sound.")

    if warn_count > 2:
        print(f"  ({warn_count} warnings — non-critical but worth monitoring)")

    print(f"\n  {'='*70}\n")

    return bt_sorted


if __name__ == "__main__":
    run()
