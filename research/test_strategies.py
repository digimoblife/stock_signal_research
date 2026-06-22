"""
test_strategies.py — test 4 strategy variants on IDX80 only.

Usage:
    python test_strategies.py

Sets universe to IDX80 (temporarily), does NOT modify settings.py.
"""
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("strategies")

os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""

import settings
from universe import get_universe, get_eligible_tickers, filter_liquidity
from fetch import fetch_all, fetch_one, save_one
from research import load_ticker, volume_divergence_signals
import filter as filter_module

DATA_DIR = Path(settings.DATA_DIR)

IHSG_TICKER = "^JKSE"
IHSG_FILE = DATA_DIR / "IHSG.csv"
LQ45_TICKERS = None  # loaded on demand

TEST_UNIVERSE = "idx80"
TEST_START = "2023-01-01"

BASE_PARAMS = {
    "starting_capital": 100_000_000,
    "max_positions": 10,
    "cost_per_trade": 0.006,
    "test_start": TEST_START,
    "long_only": True,
}

STRATEGY_DEFS = [
    {
        "name": "C_BALANCED",
        "stop_atr": 3.0,
        "max_hold_days": 15,
        "trailing": None,
        "min_vol_ratio": 2.0,
    },
    {
        "name": "TRAILING_STOP",
        "stop_atr": 3.0,
        "max_hold_days": 20,
        "trailing": {"atr_mult": 2.0},
        "min_vol_ratio": 2.0,
    },
    {
        "name": "TREND_FILTERED",
        "stop_atr": 3.0,
        "max_hold_days": 15,
        "trailing": None,
        "min_vol_ratio": 2.0,
    },
    {
        "name": "PANIC_REBOUND",
        "stop_atr": 3.0,
        "max_hold_days": 15,
        "trailing": None,
        "min_vol_ratio": 2.0,
    },
]

# ── IHSG data ────────────────────────────────────────────────────

def load_ihsg():
    path = IHSG_FILE
    if not path.exists():
        log.info("Fetching IHSG (^JKSE) data...")
        try:
            stock = yf.Ticker(IHSG_TICKER)
            df = stock.history(period="max", auto_adjust=True)
        except Exception as e:
            log.warning(f"IHSG fetch failed: {e}")
            return pd.DataFrame()
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df.index.name = "date"
        df.index = pd.to_datetime(df.index.date)
        df.to_csv(path)
        log.info(f"IHSG: {len(df)} rows saved")
    else:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index.name = "date"
    return df


def load_lq45_data():
    global LQ45_TICKERS
    old_univ = settings.STOCK_UNIVERSE
    settings.STOCK_UNIVERSE = "lq45"
    eligible, _, _ = filter_liquidity(get_universe())
    settings.STOCK_UNIVERSE = old_univ
    LQ45_TICKERS = eligible
    data = {}
    for t in eligible:
        df = load_ticker(t)
        if not df.empty and len(df) > 100:
            data[t] = df
    return data


# ── Technical indicators ─────────────────────────────────────────

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def enrich_signals(sig, df):
    sig["rsi_14"] = compute_rsi(df["close"])
    sig["ma20"] = df["close"].rolling(20).mean()
    sig["low_20"] = df["low"].rolling(20).min()
    return sig


# ── Entry filters ────────────────────────────────────────────────

def common_entry_check(strat, ticker, row):
    vol_5 = row.get("vol_5", 1.0)
    if vol_5 < strat["min_vol_ratio"]:
        return False
    liq = filter_module.classify_liquidity(ticker)
    if liq not in ("large", "mid"):
        return False
    return True


def trend_filter_check(row, ihsg_df, current_date):
    close = row.get("close")
    ma20 = row.get("ma20")
    if pd.isna(close) or pd.isna(ma20) or close <= ma20:
        return False
    if ihsg_df is not None and not ihsg_df.empty:
        if current_date in ihsg_df.index:
            ihsg_close = ihsg_df.loc[current_date, "close"]
            ihsg_ma50 = ihsg_df["close"].rolling(50).mean()
            if current_date in ihsg_ma50.index:
                ma50 = ihsg_ma50.loc[current_date]
                if pd.notna(ihsg_close) and pd.notna(ma50) and ihsg_close <= ma50:
                    return False
    return True


def panic_rebound_check(row):
    rsi = row.get("rsi_14")
    close = row.get("close")
    low_20 = row.get("low_20")
    if pd.isna(rsi) or rsi >= 35:
        return False
    if pd.isna(close) or pd.isna(low_20) or close > low_20 * 1.02:
        return False
    return True


def entry_passes(strat, ticker, row, current_date, ihsg_df):
    if not common_entry_check(strat, ticker, row):
        return False
    if strat["name"] == "TREND_FILTERED":
        return trend_filter_check(row, ihsg_df, current_date)
    if strat["name"] == "PANIC_REBOUND":
        return panic_rebound_check(row)
    return True


# ── Pre-compute signals ──────────────────────────────────────────

def precompute(data_dict):
    signal_dict = {}
    atr_dict = {}
    for ticker, df in data_dict.items():
        sig = volume_divergence_signals(df)
        sig = enrich_signals(sig, df)
        signal_dict[ticker] = sig
        atr_dict[ticker] = compute_atr(df)
    return signal_dict, atr_dict


# ── Position ─────────────────────────────────────────────────────

class Position:
    def __init__(self, ticker, entry_date, entry_price, shares,
                 stop_price, max_hold_end, atr_at_entry, trailing=None):
        self.ticker = ticker
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.shares = shares
        self.stop_price = stop_price
        self.max_hold_end = max_hold_end
        self.atr_at_entry = atr_at_entry
        self.high_water_mark = entry_price
        self.trailing = trailing  # {"atr_mult": 2.0} or None
        self.trailing_stop_level = stop_price  # starts at initial stop

    def check_exit(self, current_date, row):
        high = row["high"]
        low = row["low"]
        close = row["close"]

        self.high_water_mark = max(self.high_water_mark, high)

        if self.trailing:
            new_trail = self.high_water_mark - self.trailing["atr_mult"] * self.atr_at_entry
            self.trailing_stop_level = max(self.trailing_stop_level, new_trail)
            effective_stop = self.trailing_stop_level
        else:
            effective_stop = self.stop_price

        if low <= effective_stop:
            return effective_stop, "trailing_stop" if self.trailing else "stop_loss"

        if current_date >= self.max_hold_end:
            return close, "time_stop"

        return None


# ── Portfolio Backtest ───────────────────────────────────────────

class PortfolioBacktest:
    def __init__(self, strat, ihsg_df):
        self.strat = strat
        self.ihsg_df = ihsg_df
        self.params = {**BASE_PARAMS}
        self.params["stop_atr"] = strat["stop_atr"]
        self.params["max_hold_days"] = strat["max_hold_days"]
        self.reset()

    def reset(self):
        self.cash = float(self.params["starting_capital"])
        self.positions: list[Position] = []
        self.equity_curve = []
        self.all_trades = []
        self.peak_equity = self.cash
        self.max_drawdown = 0
        self.daily_position_counts = []

    def position_size(self):
        return self.params["starting_capital"] / self.params["max_positions"]

    def open_slot_count(self):
        return self.params["max_positions"] - len(self.positions)

    def can_enter(self):
        return len(self.positions) < self.params["max_positions"]

    def _add_trading_days(self, date, n):
        result = date
        added = 0
        while added < n:
            result += timedelta(days=1)
            if result.weekday() < 5:
                added += 1
        return result

    def enter_position(self, ticker, entry_date, entry_price,
                       stop_price, atr_at_entry):
        pos_size = self.position_size()
        shares = int(pos_size / entry_price)
        if shares <= 0:
            return
        cost = shares * entry_price
        if cost > self.cash:
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
            trailing=self.strat.get("trailing"),
        )
        self.positions.append(pos)

    def close_position(self, pos, exit_date, exit_price, reason):
        proceeds = pos.shares * exit_price
        exit_cost = proceeds * self.params["cost_per_trade"] / 2
        proceeds -= exit_cost
        cost_basis = pos.shares * pos.entry_price
        gross_pnl = proceeds - cost_basis
        pnl_pct = exit_price / pos.entry_price - 1

        self.cash += proceeds

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
        mtm = 0.0
        for pos in self.positions:
            df = self._ticker_data.get(pos.ticker)
            if df is not None and current_date in df.index:
                price = df.loc[current_date, "close"]
                mtm += pos.shares * price
            else:
                mtm += pos.shares * pos.entry_price
        total = self.cash + mtm
        self.equity_curve.append((current_date, total))
        self.peak_equity = max(self.peak_equity, total)
        dd = (self.peak_equity - total) / self.peak_equity
        self.max_drawdown = max(self.max_drawdown, dd)

    def compute_exit_for_position(self, pos, current_date, row):
        return pos.check_exit(current_date, row)

    def run(self, data_dict, signal_dict, atr_dict):
        self.reset()
        self._ticker_data = data_dict

        all_dates = set()
        for ticker, df in data_dict.items():
            all_dates.update(df.index)
        all_dates = sorted(d for d in all_dates
                          if d >= pd.Timestamp(self.params["test_start"]))
        if not all_dates:
            return [], pd.DataFrame(), None

        test_start_dt = pd.Timestamp(self.params["test_start"])

        ticker_signals = {}
        for ticker in data_dict:
            sig = signal_dict.get(ticker)
            if sig is None:
                continue
            sig = sig[sig.index >= test_start_dt]
            signal_rows = sig[sig["signal"] == 1]
            ticker_signals[ticker] = signal_rows

        days_with_positions = 0
        total_trading_days = len(all_dates)

        for current_date in all_dates:
            # 1. Check exits
            positions_to_close = []
            for pos in self.positions:
                row = data_dict[pos.ticker].loc[current_date] \
                    if current_date in data_dict[pos.ticker].index else None
                if row is None:
                    continue
                exit_info = self.compute_exit_for_position(pos, current_date, row)
                if exit_info is not None:
                    exit_price, reason = exit_info
                    exit_price_after_cost = exit_price * (1 - self.params["cost_per_trade"] / 2)
                    positions_to_close.append((pos, current_date, exit_price_after_cost, reason))

            for pos, exit_date, exit_price, reason in positions_to_close:
                self.close_position(pos, exit_date, exit_price, reason)

            # 2. Check entries
            if self.can_enter():
                new_signals = []
                for ticker, sig_df in ticker_signals.items():
                    if current_date in sig_df.index:
                        row = sig_df.loc[current_date]
                        if not entry_passes(self.strat, ticker, row,
                                            current_date, self.ihsg_df):
                            continue
                        new_signals.append((ticker, row))

                slots = self.open_slot_count()
                for ticker, row in new_signals[:slots]:
                    df = data_dict.get(ticker)
                    if df is None:
                        continue
                    entry_idx = df.index.get_indexer([current_date], method="bfill")[0] + 1
                    if entry_idx >= len(df):
                        continue
                    entry_date = df.index[entry_idx]
                    entry_price = df.iloc[entry_idx]["open"]

                    atr_val = atr_dict.get(ticker, pd.Series(dtype=float))
                    if current_date in atr_val.index:
                        atr_at_entry = atr_val.loc[current_date]
                    else:
                        atr_at_entry = atr_val.iloc[-1] if len(atr_val) > 0 else entry_price * 0.02
                    if pd.isna(atr_at_entry) or atr_at_entry <= 0:
                        atr_at_entry = entry_price * 0.02

                    stop_price = entry_price - self.params["stop_atr"] * atr_at_entry
                    entry_price_after_cost = entry_price * (1 + self.params["cost_per_trade"] / 2)

                    self.enter_position(ticker, entry_date, entry_price_after_cost,
                                        stop_price, atr_at_entry)

            # 3. Update equity
            self.update_equity(current_date)
            self.daily_position_counts.append(len(self.positions))

            # Track exposure
            if len(self.positions) > 0:
                days_with_positions += 1

        # Close remaining positions
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

        ec_df = pd.DataFrame(self.equity_curve, columns=["date", "equity"])
        ec_df.set_index("date", inplace=True)

        metrics = self.compute_metrics(ec_df, days_with_positions, total_trading_days)
        return self.all_trades, ec_df, metrics

    def compute_metrics(self, ec_df, days_in_market, total_days):
        if ec_df.empty or len(ec_df) < 20:
            return None

        start_equity = ec_df["equity"].iloc[0]
        end_equity = ec_df["equity"].iloc[-1]
        years = max((ec_df.index[-1] - ec_df.index[0]).days / 365.25, 0.5)

        cagr = (end_equity / start_equity) ** (1 / years) - 1 if start_equity > 0 else 0
        total_return = end_equity / start_equity - 1

        rolling_peak = ec_df["equity"].cummax()
        drawdowns = (ec_df["equity"] - rolling_peak) / rolling_peak
        max_dd = drawdowns.min()

        # Annual returns
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

        daily_rets = ec_df["equity"].pct_change().dropna()
        sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)) \
            if len(daily_rets) > 1 and np.std(daily_rets) > 0 else 0

        # Trade metrics
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

        # Average concurrent positions
        avg_concurrent = 0
        if hasattr(self, "daily_position_counts") and self.daily_position_counts:
            avg_concurrent = float(np.mean(self.daily_position_counts))

        return {
            "starting_equity": float(round(start_equity, 0)),
            "ending_equity": float(round(end_equity, 0)),
            "total_return": round(total_return * 100, 2),
            "years": round(years, 2),
            "cagr": round(cagr * 100, 2),
            "max_drawdown": round(max_dd * 100, 2),
            "sharpe": round(sharpe, 3),
            "trades": len(self.all_trades),
            "win_rate": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_trade": round(avg_trade * 100, 2),
            "annual": annual,
            "exposure": round(days_in_market / total_days * 100, 1) if total_days > 0 else 0,
            "avg_concurrent": round(avg_concurrent, 2),
        }


# ── Run one strategy ─────────────────────────────────────────────

def run_strategy(strat, data, signal_dict, atr_dict, ihsg_df):
    print(f"\n  ── {strat['name']} ──")
    bt = PortfolioBacktest(strat, ihsg_df)
    trades, eq_curve, metrics = bt.run(data, signal_dict, atr_dict)
    return trades, eq_curve, metrics


# ── Buy & Hold benchmarks ────────────────────────────────────────

def benchmark_bh(data_dict):
    """Equal-weight buy & hold of all eligible IDX80 stocks."""
    first_date = None
    last_date = None
    for ticker, df in data_dict.items():
        test = df[df.index >= TEST_START]
        if len(test) > 0:
            fd = test.index[0]
            ld = test.index[-1]
            first_date = fd if first_date is None or fd < first_date else first_date
            last_date = ld if last_date is None or ld > last_date else last_date

    if first_date is None or last_date is None:
        return None

    total_ret = 0
    count = 0
    for ticker, df in data_dict.items():
        test = df[df.index >= TEST_START]
        if len(test) > 0:
            ret = test["close"].iloc[-1] / test["close"].iloc[0] - 1
            total_ret += ret
            count += 1

    if count == 0:
        return None

    avg_ret = total_ret / count
    days = (last_date - first_date).days
    years = max(days / 365.25, 0.5)
    cagr = (1 + avg_ret) ** (1 / years) - 1

    return {
        "name": "IDX80 equal-weight BH",
        "total_return": round(avg_ret * 100, 2),
        "cagr": round(cagr * 100, 2),
        "years": round(years, 2),
    }


def benchmark_ihsg(ihsg_df):
    """IHSG buy & hold."""
    if ihsg_df.empty:
        return None
    test = ihsg_df[ihsg_df.index >= TEST_START]
    if len(test) < 20:
        return None
    ret = test["close"].iloc[-1] / test["close"].iloc[0] - 1
    days = (test.index[-1] - test.index[0]).days
    years = max(days / 365.25, 0.5)
    cagr = (1 + ret) ** (1 / years) - 1

    return {
        "name": "IHSG buy & hold",
        "total_return": round(ret * 100, 2),
        "cagr": round(cagr * 100, 2),
        "years": round(years, 2),
    }


def benchmark_lq45(lq45_data):
    """Equal-weight LQ45 buy & hold."""
    total_ret = 0
    count = 0
    for ticker, df in lq45_data.items():
        test = df[df.index >= TEST_START]
        if len(test) > 0:
            ret = test["close"].iloc[-1] / test["close"].iloc[0] - 1
            total_ret += ret
            count += 1
    if count == 0:
        return None
    avg_ret = total_ret / count
    years = (pd.Timestamp.now() - pd.Timestamp(TEST_START)).days / 365.25
    years = max(years, 0.5)
    cagr = (1 + avg_ret) ** (1 / years) - 1
    return {
        "name": "LQ45 equal-weight BH",
        "total_return": round(avg_ret * 100, 2),
        "cagr": round(cagr * 100, 2),
        "years": round(years, 2),
    }


# ── Print results ────────────────────────────────────────────────

def print_results(results, benchmarks):
    print("\n" + "=" * 110)
    print("  STRATEGY COMPARISON — IDX80 Universe")
    print("=" * 110)

    sep = "-" * 89

    header = f"{'Metric':<25} {'C_BALANCED':>14} {'TRAILING':>14} {'TREND_FIL':>14} {'PANIC_REB':>14}"
    print()
    print(header)
    print(sep)

    metrics_order = [
        ("total_return", "Total return (%)", "{:>+13.2f}%"),
        ("cagr", "CAGR (%)", "{:>+13.2f}%"),
        ("max_drawdown", "Max drawdown (%)", "{:>13.2f}%"),
        ("sharpe", "Sharpe", "{:>13.3f}"),
        ("trades", "Trade count", "{:>13}"),
        ("win_rate", "Win rate (%)", "{:>13.1f}%"),
        ("profit_factor", "Profit factor", "{:>13.2f}"),
        ("avg_trade", "Avg trade (%)", "{:>+13.2f}%"),
        ("exposure", "Exposure (%)", "{:>13.1f}%"),
        ("avg_concurrent", "Avg concurrent", "{:>13.2f}"),
    ]

    for key, label, fmt in metrics_order:
        vals = [label.ljust(25)]
        for r in results:
            m = r["metrics"]
            if m and key in m:
                if key in ("total_return", "cagr", "avg_trade"):
                    vals.append(fmt.format(m[key]))
                elif key in ("max_drawdown", "win_rate"):
                    vals.append(fmt.format(m[key]))
                elif key == "sharpe":
                    vals.append(fmt.format(m[key]))
                elif key == "trades":
                    vals.append(f"{m[key]:>13}")
                elif key == "profit_factor":
                    vals.append(f"{m[key]:>13.2f}")
                elif key == "exposure":
                    vals.append(f"{m[key]:>13.1f}%")
                elif key == "avg_concurrent":
                    vals.append(f"{m[key]:>13.2f}")
                else:
                    vals.append(f"{m[key]:>13}")
            else:
                vals.append(f"{'N/A':>14}")
        print(" ".join(vals))

    # Annual returns
    print("\n\n─── ANNUAL RETURNS ───\n")
    print(header)
    print(sep)
    for year in ["2023", "2024", "2025", "2026_ytd"]:
        vals = [year.ljust(25)]
        for r in results:
            m = r["metrics"]
            if m and "annual" in m and year in m["annual"]:
                vals.append(f"{m['annual'][year]:>+13.2f}%")
            else:
                vals.append(f"{'N/A':>14}")
        print(" ".join(vals))

    # Benchmarks
    print("\n\n─── BENCHMARKS ───\n")
    bh_header = f"{'Benchmark':<25} {'Total Ret':>14} {'CAGR':>14} {'Years':>14}"
    print(bh_header)
    print("-" * 69)
    for b in benchmarks:
        if b:
            print(f"{b['name']:<25} {b['total_return']:>+13.2f}% "
                  f"{b['cagr']:>+13.2f}% {b['years']:>13.2f}")

    print()


# ── Decision rules ───────────────────────────────────────────────

def evaluate_decisions(results, benchmarks):
    print("\n\n─── DECISION EVALUATION ───\n")
    print("Decision rules: CAGR > 0, PF > 1.10, Max DD < 25%,\n"
          "  not dependent on single year, sufficient but not excessive trades.\n")

    passed_any = False
    for r in results:
        m = r["metrics"]
        name = r["name"]
        if m is None:
            print(f"  {name:<20} ❌ No backtest result")
            continue

        ann = m.get("annual", {})
        years_active = [y for y in ["2023", "2024", "2025"] if y in ann and abs(ann[y]) > 0.01]
        single_year_dependent = len(years_active) <= 1 if len(years_active) >= 2 else False

        reasons = []
        if m["cagr"] <= 0:
            reasons.append(f"CAGR {m['cagr']:+.2f}% ≤ 0")
        if m["profit_factor"] <= 1.10:
            reasons.append(f"PF {m['profit_factor']:.2f} ≤ 1.10")
        if m["max_drawdown"] >= -25:
            reasons.append(f"DD {m['max_drawdown']:.2f}% (OK)")
        else:
            reasons.append(f"DD {m['max_drawdown']:.2f}% (< -25%)")
        if single_year_dependent:
            reasons.append("single-year dependent")
        if m["trades"] < 20:
            reasons.append(f"only {m['trades']} trades")
        if m["trades"] > 500:
            reasons.append(f"excessive {m['trades']} trades")

        passed = m["cagr"] > 0 and m["profit_factor"] > 1.10 \
                 and m["max_drawdown"] >= -25 \
                 and not single_year_dependent \
                 and 20 <= m["trades"] <= 500

        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name:<20} {status}")
        print(f"  {'':20} {' | '.join(reasons)}")
        print()
        if passed:
            passed_any = True

    if not passed_any:
        print("  " + "=" * 60)
        print("  CONCLUSION: No strategy variant passes all decision rules.")
        print("  Volume divergence alone is not sufficient on IDX80.")
        print("  Recommended next step: add a second signal family")
        print("  (e.g., momentum or breakout) and re-run.")
        print("  " + "=" * 60)
    else:
        print("  " + "=" * 60)
        best = max(results, key=lambda r: r["metrics"]["cagr"] if r["metrics"] else -999)
        print(f"  Best strategy: {best['name']}")
        print(f"  CAGR: {best['metrics']['cagr']:+.2f}%, "
              f"PF: {best['metrics']['profit_factor']:.2f}")
        print("  " + "=" * 60)

    print()


# ── Main ─────────────────────────────────────────────────────────

def main():
    old_universe = settings.STOCK_UNIVERSE
    settings.STOCK_UNIVERSE = TEST_UNIVERSE

    try:
        # 1. IHSG data
        print("=" * 70)
        print("  LOADING IHSG DATA")
        print("=" * 70)
        ihsg_df = load_ihsg()

        # 2. Fetch IDX80 data
        print("\n" + "=" * 70)
        print("  FETCHING IDX80 DATA")
        print("=" * 70)
        fetch_all()

        # 3. Universe composition
        print("\n" + "=" * 70)
        print("  UNIVERSE COMPOSITION")
        print("=" * 70)
        raw_tickers = get_universe()
        eligible, no_data, liq_fail = filter_liquidity(raw_tickers)
        print(f"  Raw tickers: {len(raw_tickers)}")
        print(f"  Missing data: {len(no_data)}")
        print(f"  Liquidity fail: {len(liq_fail)}")
        print(f"  Eligible: {len(eligible)}")

        # 4. Load data
        data = {}
        for t in eligible:
            df = load_ticker(t)
            if not df.empty and len(df) > 100:
                data[t] = df
        print(f"  Loaded: {len(data)} tickers")

        if len(data) < 3:
            print("  Too few tickers. Exiting.")
            return

        # 5. Pre-compute signals
        print("\n  Pre-computing signals for all tickers...")
        signal_dict, atr_dict = precompute(data)

        # 6. Run each strategy
        results = []
        for strat in STRATEGY_DEFS:
            print(f"\n{'─' * 60}")
            print(f"  STRATEGY: {strat['name']}")
            print(f"{'─' * 60}")
            trades, eq_curve, metrics = run_strategy(strat, data,
                                                      signal_dict, atr_dict,
                                                      ihsg_df)
            if metrics:
                print(f"    Trades: {metrics['trades']}, "
                      f"CAGR: {metrics['cagr']:+.2f}%, "
                      f"Total: {metrics['total_return']:+.2f}%, "
                      f"DD: {metrics['max_drawdown']:.2f}%, "
                      f"PF: {metrics['profit_factor']:.2f}")
            else:
                print("    No trades generated")

            results.append({
                "name": strat["name"],
                "trades": trades,
                "equity_curve": eq_curve,
                "metrics": metrics,
            })

            # Brief delay between strategies
            time.sleep(0.5)

        # 7. Benchmarks
        print(f"\n{'─' * 60}")
        print("  COMPUTING BENCHMARKS")
        print(f"{'─' * 60}")
        b1 = benchmark_bh(data)
        b2 = benchmark_ihsg(ihsg_df)
        print(f"  Loading LQ45 data for benchmark...")
        lq45_data = load_lq45_data()
        b3 = benchmark_lq45(lq45_data)
        benchmarks = [b for b in [b1, b2, b3] if b is not None]

        # 8. Print results
        print_results(results, benchmarks)

        # 9. Decision evaluation
        evaluate_decisions(results, benchmarks)

    finally:
        settings.STOCK_UNIVERSE = old_universe
        print(f"\nSettings restored to: {settings.STOCK_UNIVERSE}")


if __name__ == "__main__":
    main()
