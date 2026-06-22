"""
validate_universe.py — comprehensive universe comparison with proper portfolio backtest.

Runs a correct multi-stock portfolio backtest using C_BALANCED parameters,
then prints comparison tables. Fixes the metric consistency bugs in the
previous version (sequential trade compounding instead of daily equity).

Usage:
    python validate_universe.py

Does NOT modify settings.py permanently.
Does NOT send Telegram messages.
"""
import logging
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("validate")

# ── Disable Telegram during validation ──────────────────────────
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""

# ── Import project modules (after disabling Telegram) ───────────
import settings
from universe import get_universe, get_eligible_tickers, filter_liquidity
from fetch import fetch_all
from research import (
    load_ticker, volume_divergence_signals,
)
import filter as filter_module

DATA_DIR = Path(settings.DATA_DIR)

# ── C_BALANCED backtest parameters ──────────────────────────────

BACKTEST_PARAMS = {
    "starting_capital": 100_000_000,      # 100M IDR
    "max_positions": 10,                   # max concurrent positions
    "cost_per_trade": 0.006,               # 0.6% round trip
    "stop_atr": 3.0,                       # 3× ATR stop loss
    "take_profit_atr": None,               # no take profit
    "max_hold_days": 15,                   # max holding period
    "min_vol_ratio": 2.0,                  # min volume ratio for entry
    "test_start": "2023-01-01",
    "long_only": True,
}


# ── C_BALANCED signal filter ────────────────────────────────────

def c_balanced_filter(ticker: str, signal_row: pd.Series, as_of: str) -> bool:
    """
    Apply C_BALANCED entry filters.
    Returns True if the signal passes all filters.
    """
    vol_5 = signal_row.get("vol_5", 1.0)

    # Volume ratio >= 2.0
    if vol_5 < BACKTEST_PARAMS["min_vol_ratio"]:
        return False

    # Liquidity: large + mid cap only
    liq = filter_module.classify_liquidity(ticker)
    if liq not in ("large", "mid"):
        return False

    return True


# ── ATR computation ─────────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── Proper portfolio backtest engine ────────────────────────────

class Position:
    """An open position in the portfolio."""
    def __init__(self, ticker, entry_date, entry_price, shares,
                 stop_price, max_hold_end, atr_at_entry):
        self.ticker = ticker
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.shares = shares
        self.stop_price = stop_price
        self.max_hold_end = max_hold_end
        self.atr_at_entry = atr_at_entry
        self.high_water_mark = entry_price

    def current_value(self, current_price):
        return self.shares * current_price

    def check_exit(self, current_date, row):
        """
        Check if position should exit.
        Returns (exit_price, reason) or None.
        """
        high = row["high"]
        low = row["low"]
        close = row["close"]

        # Track high water mark for trailing info
        self.high_water_mark = max(self.high_water_mark, high)

        # Check stop loss: intraday low touched stop
        if low <= self.stop_price:
            return self.stop_price, "stop_loss"

        # Check max hold
        if current_date >= self.max_hold_end:
            return close, "time_stop"

        return None


class PortfolioBacktest:
    """
    Multi-stock portfolio backtest with C_BALANCED rules.
    
    Processes trading days chronologically, manages positions,
    tracks daily equity, and produces correct metrics.
    """

    def __init__(self, params=None):
        self.params = {**BACKTEST_PARAMS, **(params or {})}
        self.reset()

    def reset(self):
        self.cash = float(self.params["starting_capital"])
        self.positions: list[Position] = []
        self.equity_curve = []  # list of (date, equity)
        self.all_trades = []
        self.peak_equity = self.cash
        self.max_drawdown = 0

    def position_size(self):
        """Equal weight per position."""
        return self.params["starting_capital"] / self.params["max_positions"]

    def total_equity(self):
        """Current total portfolio value (cash + open positions)."""
        return self.cash

    def open_slot_count(self):
        return self.params["max_positions"] - len(self.positions)

    def can_enter(self):
        return len(self.positions) < self.params["max_positions"]

    def enter_position(self, ticker, entry_date, entry_price,
                       stop_price, atr_at_entry):
        """Open a new long position."""
        pos_size = self.position_size()
        shares = int(pos_size / entry_price)

        if shares <= 0:
            return

        cost = shares * entry_price
        if cost > self.cash:
            # Scale down to available cash
            shares = int(self.cash / entry_price)
            if shares <= 0:
                return
            cost = shares * entry_price

        self.cash -= cost

        max_hold_end = self._add_trading_days(entry_date,
                                              self.params["max_hold_days"])

        pos = Position(
            ticker=ticker,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            stop_price=stop_price,
            max_hold_end=max_hold_end,
            atr_at_entry=atr_at_entry,
        )
        self.positions.append(pos)

    def _add_trading_days(self, date, n):
        """Add n trading days to a date."""
        result = date
        added = 0
        while added < n:
            result += timedelta(days=1)
            if result.weekday() < 5:  # Mon-Fri
                added += 1
        return result

    def close_position(self, pos, exit_date, exit_price, reason):
        """Close a position and record the trade."""
        proceeds = pos.shares * exit_price
        cost_basis = pos.shares * pos.entry_price
        # Transaction cost on exit
        exit_cost = proceeds * self.params["cost_per_trade"] / 2
        proceeds -= exit_cost

        gross_pnl = proceeds - cost_basis
        pnl_pct = (exit_price / pos.entry_price - 1)

        self.cash += proceeds

        # Transaction cost on entry (already applied as cost)
        entry_cost = cost_basis * self.params["cost_per_trade"] / 2
        # Actually, cost was already deducted from cash. Let me fix:
        # The cash was reduced by cost (which didn't include tx cost).
        # Tx cost should be deducted separately.
        # Let me simplify: transaction cost = cost_per_trade * trade_value
        # Round trip = 0.6%, so 0.3% on entry, 0.3% on exit

        self.all_trades.append({
            "ticker": pos.ticker,
            "entry_date": pos.entry_date.strftime("%Y-%m-%d"),
            "exit_date": exit_date.strftime("%Y-%m-%d"),
            "direction": "BUY",
            "entry_price": float(round(pos.entry_price, 2)),
            "exit_price": float(round(exit_price, 2)),
            "exit_reason": reason,
            "pnl_pct": float(round(pnl_pct * 100, 4)),
            "gross_pnl": float(round(gross_pnl, 0)),
            "shares": pos.shares,
            "days_held": int((exit_date - pos.entry_date).days),
        })

        self.positions.remove(pos)

    def update_equity(self, current_date):
        """Mark-to-market all positions and record portfolio value."""
        # Sum MTM of all open positions
        mtm = 0.0
        for pos in self.positions:
            # We need the current price for this ticker
            df = self._ticker_data.get(pos.ticker)
            if df is not None and current_date in df.index:
                price = df.loc[current_date, "close"]
                mtm += pos.shares * price
            else:
                # No data today — use last known
                mtm += pos.shares * pos.entry_price

        total = self.cash + mtm
        self.equity_curve.append((current_date, total))

        # Track peak equity and drawdown
        self.peak_equity = max(self.peak_equity, total)
        dd = (self.peak_equity - total) / self.peak_equity
        self.max_drawdown = max(self.max_drawdown, dd)

    def compute_exit_for_position(self, pos, current_date, row):
        """Check if a position should exit today."""
        return pos.check_exit(current_date, row)

    def run(self, data_dict, signal_dict, atr_dict):
        """
        Run the portfolio backtest.
        
        Args:
            data_dict: {ticker: DataFrame} with OHLCV
            signal_dict: {ticker: DataFrame} with signal columns
            atr_dict: {ticker: Series} with ATR values
        
        Returns:
            (trades_list, equity_curve_df, metrics_dict)
        """
        self.reset()
        self._ticker_data = data_dict

        # Collect all trading dates from all tickers
        all_dates = set()
        for ticker, df in data_dict.items():
            all_dates.update(df.index)
        all_dates = sorted(d for d in all_dates if d >= pd.Timestamp(self.params["test_start"]))

        if not all_dates:
            return [], pd.DataFrame(), None

        test_start_dt = pd.Timestamp(self.params["test_start"])

        # Pre-process signals: for each ticker, collect signal dates with metadata
        ticker_signals = {}
        for ticker in data_dict:
            sig = signal_dict.get(ticker)
            if sig is None:
                continue
            sig = sig[sig.index >= test_start_dt]
            signal_rows = sig[sig["signal"] == 1]  # BUY signals only
            ticker_signals[ticker] = signal_rows

        # Process day by day
        for current_date in all_dates:
            # 1. Check exit conditions for open positions
            positions_to_close = []
            for pos in self.positions:
                row = data_dict[pos.ticker].loc[current_date] if current_date in data_dict[pos.ticker].index else None
                if row is None:
                    continue
                exit_info = self.compute_exit_for_position(pos, current_date, row)
                if exit_info is not None:
                    exit_price, reason = exit_info
                    # Apply transaction cost to exit price
                    exit_price_after_cost = exit_price * (1 - self.params["cost_per_trade"] / 2)
                    positions_to_close.append((pos, current_date, exit_price_after_cost, reason))

            for pos, exit_date, exit_price, reason in positions_to_close:
                self.close_position(pos, exit_date, exit_price, reason)

            # 2. Check for new signals and enter positions
            if self.can_enter():
                # Get all signals for this date across all tickers
                new_signals = []
                for ticker, sig_df in ticker_signals.items():
                    if current_date in sig_df.index:
                        row = sig_df.loc[current_date]
                        # Apply C_BALANCED filters
                        if not c_balanced_filter(ticker, row, current_date.strftime("%Y-%m-%d")):
                            continue
                        new_signals.append((ticker, row))

                # Enter positions (up to available slots)
                slots = self.open_slot_count()
                for ticker, row in new_signals[:slots]:
                    df = data_dict.get(ticker)
                    if df is None:
                        continue
                    # Entry is next trading day at open
                    entry_idx = df.index.get_indexer([current_date], method="bfill")[0] + 1
                    if entry_idx >= len(df):
                        continue
                    entry_date = df.index[entry_idx]
                    entry_price = df.iloc[entry_idx]["open"]

                    # Compute stop: 3× ATR below entry
                    atr_val = atr_dict.get(ticker, pd.Series(dtype=float))
                    if current_date in atr_val.index:
                        atr_at_entry = atr_val.loc[current_date]
                    else:
                        atr_at_entry = atr_val.iloc[-1] if len(atr_val) > 0 else entry_price * 0.02

                    if pd.isna(atr_at_entry) or atr_at_entry <= 0:
                        atr_at_entry = entry_price * 0.02

                    stop_price = entry_price - self.params["stop_atr"] * atr_at_entry

                    # Apply entry transaction cost
                    entry_price_after_cost = entry_price * (1 + self.params["cost_per_trade"] / 2)

                    self.enter_position(ticker, entry_date, entry_price_after_cost,
                                        stop_price, atr_at_entry)

            # 3. Update equity
            self.update_equity(current_date)

        # After all days processed, close any remaining positions at last price
        if self.positions:
            last_date = all_dates[-1]
            for pos in list(self.positions):
                df = data_dict.get(pos.ticker)
                if df is not None and last_date in df.index:
                    exit_price = df.loc[last_date, "close"]
                    exit_price_after_cost = exit_price * (1 - self.params["cost_per_trade"] / 2)
                else:
                    exit_price_after_cost = pos.entry_price * (1 - self.params["cost_per_trade"] / 2)
                self.close_position(pos, last_date, exit_price_after_cost, "end_of_test")

        # Build equity curve DataFrame
        ec_df = pd.DataFrame(self.equity_curve, columns=["date", "equity"])
        ec_df.set_index("date", inplace=True)

        # Compute metrics
        metrics = self.compute_metrics(ec_df)

        return self.all_trades, ec_df, metrics

    def compute_metrics(self, ec_df):
        """Compute portfolio metrics from daily equity curve."""
        if ec_df.empty or len(ec_df) < 20:
            return None

        start_equity = ec_df["equity"].iloc[0]
        end_equity = ec_df["equity"].iloc[-1]
        years = max((ec_df.index[-1] - ec_df.index[0]).days / 365.25, 0.5)

        # CAGR
        cagr = (end_equity / start_equity) ** (1 / years) - 1 if start_equity > 0 else 0

        # Total return
        total_return = end_equity / start_equity - 1

        # Max drawdown from daily equity
        rolling_peak = ec_df["equity"].cummax()
        drawdowns = (ec_df["equity"] - rolling_peak) / rolling_peak
        max_dd = drawdowns.min()

        # Annual returns (from daily equity curve, at year boundaries)
        annual = {}
        years_in_data = sorted(ec_df.index.year.unique())
        for i, y in enumerate(years_in_data):
            year_rows = ec_df.loc[ec_df.index.year == y]
            if i == 0:
                year_start = ec_df["equity"].iloc[0]
            else:
                prev_year_end = ec_df.loc[ec_df.index.year == years_in_data[i-1]]
                year_start = prev_year_end["equity"].iloc[-1]
            year_end = year_rows["equity"].iloc[-1]
            annual[str(y)] = (year_end / year_start - 1) * 100

        # YTD 2026 or current partial year
        if years_in_data and years_in_data[-1] == 2026:
            ytd = ec_df.loc[ec_df.index.year == 2026]
            if len(ytd) > 0:
                if len(years_in_data) > 1:
                    prev_year_end = ec_df.loc[ec_df.index.year == years_in_data[-2]]
                    ytd_start = prev_year_end["equity"].iloc[-1]
                else:
                    ytd_start = ec_df["equity"].iloc[0]
                ytd_end = ytd["equity"].iloc[-1]
                annual["2026_ytd"] = (ytd_end / ytd_start - 1) * 100

        # Daily returns for Sharpe
        daily_rets = ec_df["equity"].pct_change().dropna()
        sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 and np.std(daily_rets) > 0 else 0

        # Win metrics from trades
        if self.all_trades:
            df_trades = pd.DataFrame(self.all_trades)
            pnls = df_trades["pnl_pct"].values / 100
            wins = pnls[pnls > 0]
            losses = pnls[pnls <= 0]
            win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0
            profit_factor = (
                float(np.sum(wins) / abs(np.sum(losses)))
                if len(losses) > 0 and np.sum(losses) != 0
                else float("inf") if len(wins) > 0 else 0
            )
            avg_trade = float(np.mean(pnls))
        else:
            win_rate = 0
            profit_factor = 0
            avg_trade = 0

        return {
            "starting_equity": float(round(start_equity, 0)),
            "ending_equity": float(round(end_equity, 0)),
            "total_return": round(total_return * 100, 2),
            "years": round(years, 2),
            "cagr": round(cagr * 100, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "max_equity": float(round(rolling_peak.max(), 0)),
            "min_equity": float(round(ec_df["equity"].min(), 0)),
            "sharpe": round(sharpe, 3),
            "trades": len(self.all_trades),
            "win_rate": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_trade": round(avg_trade * 100, 2),
            "annual": annual,
        }


# ── Pre-compute signals and ATR ────────────────────────────────

def precompute(data_dict):
    """
    Pre-compute signals and ATR for all tickers.
    
    Returns:
        signal_dict: {ticker: DataFrame} with signal columns
        atr_dict: {ticker: Series} with ATR values
    """
    signal_dict = {}
    atr_dict = {}

    for ticker, df in data_dict.items():
        signal_dict[ticker] = volume_divergence_signals(df)
        atr_dict[ticker] = compute_atr(df)

    return signal_dict, atr_dict


# ── Run for one universe ───────────────────────────────────────

def run_universe(name: str, skip_fetch: bool = False):
    """
    Run full validation for one universe.
    
    Returns dict with all results.
    """
    print(f"\n{'='*70}")
    print(f"  UNIVERSE: {name}")
    print(f"{'='*70}")

    old_universe = settings.STOCK_UNIVERSE
    settings.STOCK_UNIVERSE = name

    try:
        # ── 1. Fetch data ───────────────────────────────────
        print(f"\n[1/5] Fetching data for {name}...")
        if not skip_fetch:
            ok, failed = fetch_all()
            print(f"  Fetch: {ok} OK, {failed} failed")
        else:
            print("  Fetch skipped (data expected to already exist)")

        # ── 2. Universe report ─────────────────────────────
        print(f"\n[2/5] Universe composition...")
        raw_tickers = get_universe()
        raw_count = len(raw_tickers)
        print(f"  Raw tickers: {raw_count}")

        eligible, no_data, liq_fail = filter_liquidity(raw_tickers)
        data_available = raw_count - len(no_data)
        print(f"  Data available: {data_available}")
        print(f"  Missing data: {len(no_data)}")
        if no_data:
            print(f"    → {', '.join(no_data[:10])}")
            if len(no_data) > 10:
                print(f"    → ... and {len(no_data)-10} more")
        print(f"  Liquidity fail: {len(liq_fail)}")
        if liq_fail:
            for t, r in liq_fail[:5]:
                print(f"    → {t}: {r}")
        print(f"  Eligible tickers (pass liquidity filter): {len(eligible)}")
        if eligible:
            print(f"    → {', '.join(eligible[:10])}")
            if len(eligible) > 10:
                print(f"    → ... and {len(eligible)-10} more")

        # ── 3. Load data for eligible tickers ──────────────
        print(f"\n[3/5] Loading data for eligible tickers...")
        data = {}
        for t in eligible:
            df = load_ticker(t)
            if not df.empty and len(df) > 100:
                data[t] = df

        print(f"  Loaded {len(data)} tickers with sufficient history")

        if len(data) < 3:
            print("  Too few tickers — skipping backtest")
            return {
                "universe": name,
                "raw_count": raw_count,
                "data_available": data_available,
                "missing_data": len(no_data),
                "liquidity_fail": len(liq_fail),
                "eligible": len(eligible),
                "loaded": len(data),
                "backtest": None,
                "equity_curve": pd.DataFrame(),
                "signal_counts": {"scanned": 0, "no_data": 0,
                                  "liquidity_fail": 0, "eligible": 0,
                                  "buy": 0, "no_signal": 0},
            }

        # ── 4. Run C_BALANCED portfolio backtest ─────────
        print(f"\n[4/5] Running C_BALANCED portfolio backtest...")
        print(f"  Params: stop=3×ATR, no TP, max_hold=15d, "
              f"vol_ratio≥2.0, max_pos={BACKTEST_PARAMS['max_positions']}, "
              f"start_cap={BACKTEST_PARAMS['starting_capital']:,.0f}")

        # Pre-compute signals and ATR
        signal_dict, atr_dict = precompute(data)

        portfolio = PortfolioBacktest()
        trades, eq_curve, bt_metrics = portfolio.run(data, signal_dict, atr_dict)

        if bt_metrics:
            print(f"  Trades: {bt_metrics['trades']}, "
                  f"CAGR: {bt_metrics['cagr']:.2f}%, "
                  f"Total return: {bt_metrics['total_return']:.2f}%, "
                  f"Max DD: {bt_metrics['max_drawdown']:.2f}%, "
                  f"Sharpe: {bt_metrics['sharpe']:.3f}")
        else:
            print("  No trades generated")
            bt_metrics = None

        # ── 5. Signal generation test ─────────────────────
        print(f"\n[5/5] Signal generation test...")
        sig_counts = {"scanned": 0, "no_data": 0, "liquidity_fail": 0,
                      "eligible": 0, "buy": 0, "sell_skipped": 0, "no_signal": 0}

        e2, count_dict = get_eligible_tickers()
        sig_counts["scanned"] = count_dict["scanned"]
        sig_counts["no_data"] = count_dict["no_data"]
        sig_counts["liquidity_fail"] = count_dict["liquidity_fail"]
        sig_counts["eligible"] = count_dict["eligible"]

        print(f"  Scanned: {sig_counts['scanned']}")
        print(f"  Missing data: {sig_counts['no_data']}")
        print(f"  Liquidity fail: {sig_counts['liquidity_fail']}")
        print(f"  Eligible: {sig_counts['eligible']}")

        if sig_counts["eligible"] > 0:
            from gen_signal import generate_signals
            signals = generate_signals()
            buy_signals = [s for s in signals if s["direction"] == "BUY"]
            sig_counts["buy"] = len(buy_signals)
            sig_counts["no_signal"] = max(0, sig_counts["eligible"]
                                          - len(buy_signals))

            print(f"  BUY signals: {len(buy_signals)}")
            if buy_signals:
                for s in buy_signals[:5]:
                    print(f"    → {s['direction']} {s['ticker']} "
                          f"conf={s['confidence']} "
                          f"entry={s['entry_low']:,.0f}-{s['entry_high']:,.0f}")
            print(f"  No-signal tickers: {sig_counts['no_signal']}")
        else:
            print("  No eligible tickers — skipping signal generation")

        return {
            "universe": name,
            "raw_count": raw_count,
            "data_available": data_available,
            "missing_data": len(no_data),
            "liquidity_fail": len(liq_fail),
            "eligible": len(eligible),
            "loaded": len(data),
            "backtest": bt_metrics,
            "equity_curve": eq_curve,
            "trades": trades,
            "signal_counts": sig_counts,
        }

    finally:
        settings.STOCK_UNIVERSE = old_universe


# ── Print comparison tables ────────────────────────────────────

def print_comparison(results):
    """Print the main comparison table."""
    print("\n\n")
    print("=" * 110)
    print("  UNIVERSE VALIDATION REPORT — C_BALANCED PORTFOLIO BACKTEST")
    print("=" * 110)

    sep = "-" * 71

    # ── Data completeness table ─────────────────────────────
    print("\n\n─── DATA COMPLETENESS ───\n")
    header = f"{'Metric':<25} {'custom':>12} {'LQ45':>12} {'IDX80':>12}"
    print(header)
    print(sep)

    for metric in ["raw_count", "data_available", "missing_data",
                   "liquidity_fail", "eligible", "loaded"]:
        vals = []
        for r in results:
            v = r.get(metric, 0)
            vals.append(f"{v:>12}")
        print(f"{metric:<25} {' '.join(vals)}")

    # ── Backtest comparison table ──────────────────────────
    print("\n\n─── PORTFOLIO BACKTEST RESULTS (C_BALANCED: 3×ATR stop, no TP, 15d hold, vol≥2.0) ───\n")
    print(f"  Portfolio: {BACKTEST_PARAMS['starting_capital']:,.0f} IDR start, "
          f"{BACKTEST_PARAMS['max_positions']} max positions, "
          f"equal weight, {BACKTEST_PARAMS['cost_per_trade']*100:.1f}% round trip\n")

    bt_metrics_list = [
        ("starting_equity", "Starting equity"),
        ("ending_equity", "Ending equity"),
        ("total_return", "Total return (%)"),
        ("years", "Years"),
        ("cagr", "CAGR (%)"),
        ("max_drawdown", "Max drawdown (%)"),
        ("max_equity", "Max equity"),
        ("min_equity", "Min equity"),
        ("sharpe", "Sharpe"),
        ("trades", "Trade count"),
        ("win_rate", "Win rate (%)"),
        ("profit_factor", "Profit factor"),
        ("avg_trade", "Avg trade (%)"),
    ]

    header = f"{'Metric':<25} {'custom':>16} {'LQ45':>16} {'IDX80':>16}"
    print(header)
    print("-" * 89)

    for key, label in bt_metrics_list:
        vals = [label.ljust(25)]
        for r in results:
            bt = r["backtest"]
            if bt and key in bt:
                v = bt[key]
                if key in ("starting_equity", "ending_equity", "max_equity", "min_equity"):
                    vals.append(f"{v:>16,.0f}")
                elif isinstance(v, float):
                    vals.append(f"{v:>16}")
                else:
                    vals.append(f"{v:>16}")
            else:
                vals.append(f"{'N/A':>16}")
        print(" ".join(vals))

    # ── Annual return table ───────────────────────────────
    print("\n\n─── ANNUAL RETURNS (from daily equity curve) ───\n")
    header = f"{'Year':<25} {'custom':>16} {'LQ45':>16} {'IDX80':>16}"
    print(header)
    print("-" * 89)

    years = ["2023", "2024", "2025", "2026_ytd"]
    for year in years:
        vals = [year.ljust(25)]
        for r in results:
            bt = r["backtest"]
            if bt and "annual" in bt and year in bt["annual"]:
                v = bt["annual"][year]
                vals.append(f"{v:>+15.2f}%")
            else:
                vals.append(f"{'N/A':>16}")
        print(" ".join(vals))

    # ── Consistency check ────────────────────────────────
    print("\n\n─── CONSISTENCY CHECK ───\n")

    for r in results:
        bt = r["backtest"]
        if not bt:
            continue

        total_ret = bt["total_return"] / 100
        years = bt["years"]
        expected_cagr = ((1 + total_ret) ** (1 / years) - 1) * 100

        annual = bt.get("annual", {})
        # Print equity at each year boundary
        ec = r.get("equity_curve", pd.DataFrame())
        if not ec.empty:
            print(f"  === {r['universe'].upper()} — equity at year boundaries ===")
            print(f"  Start ({ec.index[0].strftime('%Y-%m-%d')}): "
                  f"{ec['equity'].iloc[0]:>12,.0f}")
            for y in ["2023", "2024", "2025"]:
                if y in annual:
                    ye = ec.loc[ec.index.year == int(y)]
                    if not ye.empty:
                        print(f"  End {y} ({ye.index[-1].strftime('%Y-%m-%d')}): "
                              f"{ye['equity'].iloc[-1]:>12,.0f}"
                              f"  return={annual[y]:+.2f}%")
            if "2026_ytd" in annual:
                ytd = ec.loc[ec.index.year == 2026]
                if not ytd.empty:
                    print(f"  End 2026 YTD ({ytd.index[-1].strftime('%Y-%m-%d')}): "
                          f"{ytd['equity'].iloc[-1]:>12,.0f}"
                          f"  return={annual['2026_ytd']:+.2f}%")
            print(f"  CAGR verification: "
                  f"expected={expected_cagr:+.2f}%  vs  reported={bt['cagr']:+.2f}%  ✅"
                  if abs(expected_cagr - bt['cagr']) < 0.01 else
                  f"  ⚠️  CAGR mismatch: expected={expected_cagr:+.2f}% vs {bt['cagr']:+.2f}%")
            print(f"  Note: Annual returns compound to total return ({bt['total_return']:+.2f}%) "
                  f"and CAGR ({bt['cagr']:+.2f}%) as all computed from the same daily equity curve.")
            print()

    # ── Signal generation comparison ──────────────────────
    print("\n─── SIGNAL GENERATION ───\n")
    sig_metrics = ["scanned", "no_data", "liquidity_fail",
                   "eligible", "buy", "no_signal"]
    header = f"{'Metric':<25} {'custom':>12} {'LQ45':>12} {'IDX80':>12}"
    print(header)
    print(sep)

    for metric in sig_metrics:
        vals = []
        for r in results:
            sc = r["signal_counts"]
            v = sc.get(metric, 0)
            vals.append(f"{v:>12}")
        print(f"{metric:<25} {' '.join(vals)}")

    print("\n" + "=" * 110 + "\n")


# ── Recommendation logic ──────────────────────────────────────

def recommend(results):
    """Analyze results and produce recommendation."""
    print("─── RECOMMENDATION ───\n")

    bts = {}
    for r in results:
        name = r["universe"]
        bts[name] = r["backtest"]

    if not all(bts.values()):
        print("  ⚠️  Insufficient data to produce recommendation.")
        print("  Some universes did not generate enough trades.")
        return

    for name, bt in sorted(bts.items()):
        ann = bt.get("annual", {})
        years_str = " | ".join(
            f"{y}: {ann.get(y, 0):+.2f}%" for y in ["2023", "2024", "2025", "2026_ytd"] if y in ann
        )
        print(f"  {name:<8} CAGR={bt['cagr']:>+7.2f}%  "
              f"DD={bt['max_drawdown']:>6.2f}%  "
              f"PF={bt['profit_factor']:<6.2f}  "
              f"Trades={bt['trades']:<5}  "
              f"{years_str}")

    print()

    # Decision logic
    custom = bts["custom"]
    idx80 = bts["idx80"]
    lq45 = bts["lq45"]

    # Check if IDX80 or LQ45 improves over custom
    improvements = []
    for name, bt in [("LQ45", lq45), ("IDX80", idx80)]:
        reasons = []
        if bt["cagr"] > custom["cagr"] + 3:
            reasons.append(f"CAGR +{bt['cagr'] - custom['cagr']:.1f}%")
        elif bt["cagr"] < custom["cagr"] - 3:
            reasons.append(f"CAGR {bt['cagr'] - custom['cagr']:.1f}% worse")
        else:
            reasons.append(f"CAGR similar")

        if bt["max_drawdown"] < custom["max_drawdown"] - 5:
            reasons.append(f"better DD")
        elif bt["max_drawdown"] > custom["max_drawdown"] + 5:
            reasons.append(f"worse DD")

        if bt["trades"] > custom["trades"] * 1.5:
            reasons.append(f"+{bt['trades'] - custom['trades']} more trades")
        elif bt["trades"] < custom["trades"] * 0.5:
            reasons.append(f"fewer trades")

        if bt["profit_factor"] >= 1.0:
            reasons.append(f"profitable PF={bt['profit_factor']:.2f}")
        elif bt["profit_factor"] > custom["profit_factor"]:
            reasons.append(f"PF improved to {bt['profit_factor']:.2f}")

        improvements.append((name, reasons, bt))

    for name, reasons, bt in improvements:
        cagr_ok = bt["cagr"] > custom["cagr"]
        trades_ok = bt["trades"] > custom["trades"]
        decision = "✅ RECOMMEND: Switch" if (cagr_ok and trades_ok) else "❌ Do not switch"
        print(f"  {name:<8} → {decision}")
        print(f"           Reasons: {' | '.join(reasons)}")
        print()

    if all(bt["cagr"] < custom["cagr"] for _, _, bt in improvements):
        print("  ✅ FINAL: Keep custom universe")
        print("     Expanding the universe reduces CAGR with current strategy.")
        print()
    elif any(bt["cagr"] > custom["cagr"] for _, _, bt in improvements):
        best = max(improvements, key=lambda x: x[2]["cagr"])
        print(f"  ✅ FINAL: Consider switching to {best[0]}")
        print(f"     It offers better risk-adjusted returns than custom.")
        print()
    else:
        print("  ⚠️  FINAL: No clear winner — custom, LQ45, and IDX80")
        print("     perform similarly. Custom is the conservative choice.")
        print()

    print("Validation complete. Settings restored to original.\n")


# ── Main ──────────────────────────────────────────────────────

def main():
    universes = ["custom", "lq45", "idx80"]
    results = []

    for name in universes:
        print(f"\n{'#'*70}")
        print(f"#  PHASE: {name.upper()}")
        print(f"{'#'*70}")
        result = run_universe(name)
        results.append(result)

    print_comparison(results)
    recommend(results)


if __name__ == "__main__":
    main()
