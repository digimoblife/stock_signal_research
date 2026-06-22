"""
test_trend_filtered.py — expand TREND_FILTERED to increase trade count
while preserving positive edge.

Tests A/B/C/D/E parameter grids on IDX80 eligible universe.
"""
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("trend_filtered")
logging.getLogger("yfinance").setLevel(logging.WARNING)

os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""

import settings
from universe import get_universe, get_eligible_tickers, filter_liquidity
from research import load_ticker, volume_divergence_signals
import filter as filter_module

DATA_DIR = Path(settings.DATA_DIR)
TEST_START = "2023-01-01"

# ── Base backtest params ────────────────────────────────────────

BASE = {
    "starting_capital": 100_000_000,
    "max_positions": 10,
    "cost_per_trade": 0.006,
    "test_start": TEST_START,
    "long_only": True,
}

BASELINE = {
    "vol_ratio": 2.0,
    "ihsg_filter": "ma50",
    "stock_filter": "ma20",
    "momentum": None,
    "stop_atr": 3.0,
    "max_hold_days": 15,
    "trailing": None,
    "name": "baseline",
}

# ── Parameter grids ─────────────────────────────────────────────

A_IHSG = [
    {"ihsg_filter": "ma20", "name": "A1: IHSG>MA20"},
    {"ihsg_filter": "ma50", "name": "A2: IHSG>MA50"},
    {"ihsg_filter": "ma100", "name": "A3: IHSG>MA100"},
    {"ihsg_filter": "ma200", "name": "A4: IHSG>MA200"},
    {"ihsg_filter": "ma50_or_10d_up", "name": "A5: IHSG>MA50|10d>0"},
    {"ihsg_filter": None, "name": "A6: no IHSG filter"},
]

B_STOCK = [
    {"stock_filter": "ma10", "name": "B1: stock>MA10"},
    {"stock_filter": "ma20", "name": "B2: stock>MA20"},
    {"stock_filter": "ma50", "name": "B3: stock>MA50"},
    {"stock_filter": "ma20_or_5d_high", "name": "B4: >MA20|5d_high"},
]

C_VOL = [
    {"vol_ratio": 1.5, "name": "C1: vol>=1.5"},
    {"vol_ratio": 1.75, "name": "C2: vol>=1.75"},
    {"vol_ratio": 2.0, "name": "C3: vol>=2.0"},
    {"vol_ratio": 2.5, "name": "C4: vol>=2.5"},
]

D_MOM = [
    {"momentum": None, "name": "D0: no momentum"},
    {"momentum": "close_up", "name": "D1: close>prev"},
    {"momentum": "5d_high", "name": "D2: close>5d_high"},
    {"momentum": "rsi_50", "name": "D3: RSI>50"},
    {"momentum": "macd_pos", "name": "D4: MACD>0"},
]

E_EXIT = [
    {"stop_atr": 2.0, "max_hold_days": 10, "name": "E1: stop2_hold10"},
    {"stop_atr": 2.0, "max_hold_days": 15, "name": "E2: stop2_hold15"},
    {"stop_atr": 2.0, "max_hold_days": 20, "name": "E3: stop2_hold20"},
    {"stop_atr": 3.0, "max_hold_days": 10, "name": "E4: stop3_hold10"},
    {"stop_atr": 3.0, "max_hold_days": 15, "name": "E5: stop3_hold15"},
    {"stop_atr": 3.0, "max_hold_days": 20, "name": "E6: stop3_hold20"},
    {"stop_atr": 4.0, "max_hold_days": 10, "name": "E7: stop4_hold10"},
    {"stop_atr": 4.0, "max_hold_days": 15, "name": "E8: stop4_hold15"},
    {"stop_atr": 4.0, "max_hold_days": 20, "name": "E9: stop4_hold20"},
]

# All variants to test (merged with baseline for unspecified params)
ALL_DIMENSIONS = [
    ("A: IHSG filter", A_IHSG, ["ihsg_filter"]),
    ("B: Stock filter", B_STOCK, ["stock_filter"]),
    ("C: Volume ratio", C_VOL, ["vol_ratio"]),
    ("D: Momentum", D_MOM, ["momentum"]),
    ("E: Exit params", E_EXIT, ["stop_atr", "max_hold_days"]),
]

RANK_WEIGHTS = {"cagr": 0.30, "max_dd": 0.25, "pf": 0.20, "trades": 0.15, "annual_stability": 0.10}

MIN_PASS = {"cagr": 5.0, "pf": 1.15, "max_dd": -25.0, "min_trades": 50, "max_trades": 500}


# ── IHSG data ───────────────────────────────────────────────────

def load_ihsg():
    path = DATA_DIR / "IHSG.csv"
    if not path.exists():
        log.info("Fetching IHSG (^JKSE) data...")
        stock = yf.Ticker("^JKSE")
        df = stock.history(period="max", auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        df.index.name = "date"
        df.index = pd.to_datetime(df.index.date)
        df.to_csv(path)
        log.info(f"IHSG: {len(df)} rows saved")
    else:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.index.name = "date"
    return df


def precompute_ihsg(ihsg_df):
    if ihsg_df.empty:
        return None
    df = ihsg_df.copy()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma100"] = df["close"].rolling(100).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["ret_10"] = df["close"].pct_change(10)
    return df


# ── Technical indicators ────────────────────────────────────────

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


# ── Pre-compute signals with all needed columns ─────────────────

def precompute_signals(data_dict):
    signal_dict = {}
    atr_dict = {}
    for ticker, df in data_dict.items():
        sig = volume_divergence_signals(df)
        sig["ma10"] = df["close"].rolling(10).mean()
        sig["ma20"] = df["close"].rolling(20).mean()
        sig["ma50"] = df["close"].rolling(50).mean()
        sig["high_5"] = df["high"].rolling(5).max().shift(1)
        sig["prev_close"] = df["close"].shift(1)
        sig["rsi_14"] = compute_rsi(df["close"])
        ema12 = df["close"].ewm(span=12).mean()
        ema26 = df["close"].ewm(span=26).mean()
        macd_line = ema12 - ema26
        sig["macd_hist"] = macd_line - macd_line.ewm(span=9).mean()
        signal_dict[ticker] = sig
        atr_dict[ticker] = compute_atr(df)
    return signal_dict, atr_dict


# ── Entry filter ────────────────────────────────────────────────

def entry_passes(params, ticker, row, current_date, ihsg_df):
    if row.get("signal", 0) != 1:
        return False
    vol_5 = row.get("vol_5", 1.0)
    if vol_5 < params["vol_ratio"]:
        return False

    liq = filter_module.classify_liquidity(ticker)
    if liq not in ("large", "mid"):
        return False

    close = row.get("close")
    if pd.isna(close):
        return False

    # Stock filter
    sf = params["stock_filter"]
    if sf == "ma10":
        v = row.get("ma10")
        if pd.isna(v) or close <= v:
            return False
    elif sf == "ma20":
        v = row.get("ma20")
        if pd.isna(v) or close <= v:
            return False
    elif sf == "ma50":
        v = row.get("ma50")
        if pd.isna(v) or close <= v:
            return False
    elif sf == "ma20_or_5d_high":
        ok1 = not (pd.isna(row.get("ma20")) or close <= row["ma20"])
        ok2 = not (pd.isna(row.get("high_5")) or close <= row["high_5"])
        if not (ok1 or ok2):
            return False

    # IHSG filter
    ihsg_f = params["ihsg_filter"]
    if ihsg_f is not None and ihsg_df is not None and not ihsg_df.empty:
        if current_date not in ihsg_df.index:
            return False
        ir = ihsg_df.loc[current_date]
        ihsg_close = ir["close"]
        if pd.isna(ihsg_close):
            return False
        if ihsg_f == "ma20":
            if pd.isna(ir.get("ma20")) or ihsg_close <= ir["ma20"]:
                return False
        elif ihsg_f == "ma50":
            if pd.isna(ir.get("ma50")) or ihsg_close <= ir["ma50"]:
                return False
        elif ihsg_f == "ma100":
            if pd.isna(ir.get("ma100")) or ihsg_close <= ir["ma100"]:
                return False
        elif ihsg_f == "ma200":
            if pd.isna(ir.get("ma200")) or ihsg_close <= ir["ma200"]:
                return False
        elif ihsg_f == "ma50_or_10d_up":
            ok1 = not (pd.isna(ir.get("ma50")) or ihsg_close <= ir["ma50"])
            ok2 = not (pd.isna(ir.get("ret_10")) or ir["ret_10"] <= 0)
            if not (ok1 or ok2):
                return False

    # Momentum
    mom = params.get("momentum")
    if mom == "close_up":
        v = row.get("prev_close")
        if pd.isna(v) or close <= v:
            return False
    elif mom == "5d_high":
        v = row.get("high_5")
        if pd.isna(v) or close <= v:
            return False
    elif mom == "rsi_50":
        v = row.get("rsi_14")
        if pd.isna(v) or v <= 50:
            return False
    elif mom == "macd_pos":
        v = row.get("macd_hist")
        if pd.isna(v) or v <= 0:
            return False

    return True


# ── Position & Backtest (same engine as test_strategies) ────────

class Position:
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

    def check_exit(self, current_date, row):
        high, low, close = row["high"], row["low"], row["close"]
        self.high_water_mark = max(self.high_water_mark, high)
        if low <= self.stop_price:
            return self.stop_price, "stop_loss"
        if current_date >= self.max_hold_end:
            return close, "time_stop"
        return None


class PortfolioBacktest:
    def __init__(self, params, ihsg_df):
        self.params = {**BASE}
        self.params["stop_atr"] = params["stop_atr"]
        self.params["max_hold_days"] = params["max_hold_days"]
        self.variant = params
        self.ihsg_df = ihsg_df
        self.reset()

    def reset(self):
        self.cash = float(self.params["starting_capital"])
        self.positions = []
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

    def enter_position(self, ticker, entry_date, entry_price, stop_price, atr_at_entry):
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
        max_hold_end = self._add_trading_days(entry_date, self.params["max_hold_days"])
        pos = Position(ticker, entry_date, entry_price, shares, stop_price, max_hold_end, atr_at_entry)
        self.positions.append(pos)

    def close_position(self, pos, exit_date, exit_price, reason):
        proceeds = pos.shares * exit_price
        exit_cost = proceeds * self.params["cost_per_trade"] / 2
        proceeds -= exit_cost
        cost_basis = pos.shares * pos.entry_price
        pnl_pct = exit_price / pos.entry_price - 1
        self.cash += proceeds
        self.all_trades.append({
            "ticker": pos.ticker, "entry_date": pos.entry_date.strftime("%Y-%m-%d"),
            "exit_date": exit_date.strftime("%Y-%m-%d"), "direction": "BUY",
            "entry_price": float(round(pos.entry_price, 2)),
            "exit_price": float(round(exit_price, 2)),
            "exit_reason": reason, "pnl_pct": float(round(pnl_pct * 100, 4)),
            "gross_pnl": float(round(proceeds - cost_basis, 0)),
            "shares": pos.shares, "days_held": int((exit_date - pos.entry_date).days),
        })
        self.positions.remove(pos)

    def update_equity(self, current_date):
        mtm = 0.0
        for pos in self.positions:
            df = self._ticker_data.get(pos.ticker)
            if df is not None and current_date in df.index:
                mtm += pos.shares * df.loc[current_date, "close"]
            else:
                mtm += pos.shares * pos.entry_price
        total = self.cash + mtm
        self.equity_curve.append((current_date, total))
        self.peak_equity = max(self.peak_equity, total)

    def run(self, data_dict, signal_dict, atr_dict):
        self.reset()
        self._ticker_data = data_dict

        all_dates = set()
        for df in data_dict.values():
            all_dates.update(df.index)
        all_dates = sorted(d for d in all_dates if d >= pd.Timestamp(self.params["test_start"]))
        if not all_dates:
            return [], pd.DataFrame(), None

        test_start_dt = pd.Timestamp(self.params["test_start"])

        ticker_signals = {}
        for ticker, sig in signal_dict.items():
            sig = sig[sig.index >= test_start_dt]
            sig = sig[sig["signal"] == 1]
            if len(sig) > 0:
                ticker_signals[ticker] = sig

        days_in_market = 0
        total_days = len(all_dates)
        ihsg_df = self.ihsg_df

        for i, current_date in enumerate(all_dates):
            # Exits
            to_close = []
            for pos in self.positions:
                row = data_dict[pos.ticker].loc[current_date] if current_date in data_dict[pos.ticker].index else None
                if row is None:
                    continue
                r = pos.check_exit(current_date, row)
                if r:
                    ep, reason = r
                    ep2 = ep * (1 - self.params["cost_per_trade"] / 2)
                    to_close.append((pos, current_date, ep2, reason))
            for pos, dt, ep, rsn in to_close:
                self.close_position(pos, dt, ep, rsn)

            # Entries
            if self.can_enter():
                entries = []
                for ticker, sig_df in ticker_signals.items():
                    if current_date in sig_df.index:
                        row = sig_df.loc[current_date]
                        if entry_passes(self.variant, ticker, row, current_date, ihsg_df):
                            entries.append((ticker, row))
                slots = self.open_slot_count()
                for ticker, row in entries[:slots]:
                    df = data_dict.get(ticker)
                    if df is None:
                        continue
                    ei = df.index.get_indexer([current_date], method="bfill")[0] + 1
                    if ei >= len(df):
                        continue
                    entry_date = df.index[ei]
                    entry_price = df.iloc[ei]["open"]
                    atr_val = atr_dict.get(ticker, pd.Series(dtype=float))
                    if current_date in atr_val.index:
                        aae = atr_val.loc[current_date]
                    else:
                        aae = atr_val.iloc[-1] if len(atr_val) > 0 else entry_price * 0.02
                    if pd.isna(aae) or aae <= 0:
                        aae = entry_price * 0.02
                    stop_price = entry_price - self.params["stop_atr"] * aae
                    entry_price_after = entry_price * (1 + self.params["cost_per_trade"] / 2)
                    self.enter_position(ticker, entry_date, entry_price_after, stop_price, aae)

            self.update_equity(current_date)
            self.daily_position_counts.append(len(self.positions))
            if len(self.positions) > 0:
                days_in_market += 1

            # Progress indicator every 200 days
            if i > 0 and i % 200 == 0:
                pass  # silent

        # Close remaining at last price
        if self.positions:
            ld = all_dates[-1]
            for pos in list(self.positions):
                df = data_dict.get(pos.ticker)
                if df is not None and ld in df.index:
                    ep = df.loc[ld, "close"]
                    ep2 = ep * (1 - self.params["cost_per_trade"] / 2)
                else:
                    ep2 = pos.entry_price * (1 - self.params["cost_per_trade"] / 2)
                self.close_position(pos, ld, ep2, "end_of_test")

        ec_df = pd.DataFrame(self.equity_curve, columns=["date", "equity"])
        ec_df.set_index("date", inplace=True)
        metrics = self.compute_metrics(ec_df, days_in_market, total_days)
        return self.all_trades, ec_df, metrics

    def compute_metrics(self, ec_df, days_in_market, total_days):
        if ec_df.empty or len(ec_df) < 20:
            return None
        start = ec_df["equity"].iloc[0]
        end = ec_df["equity"].iloc[-1]
        years = max((ec_df.index[-1] - ec_df.index[0]).days / 365.25, 0.5)
        cagr = (end / start) ** (1 / years) - 1 if start > 0 else 0
        total_ret = end / start - 1
        rp = ec_df["equity"].cummax()
        dd = ((ec_df["equity"] - rp) / rp).min()

        annual = {}
        yrs = sorted(ec_df.index.year.unique())
        for i, y in enumerate(yrs):
            yr = ec_df.loc[ec_df.index.year == y]
            ys = ec_df["equity"].iloc[0] if i == 0 else \
                ec_df.loc[ec_df.index.year == yrs[i-1]]["equity"].iloc[-1]
            ye = yr["equity"].iloc[-1]
            annual[str(y)] = (ye / ys - 1) * 100

        if yrs and yrs[-1] == 2026:
            ytd = ec_df.loc[ec_df.index.year == 2026]
            if len(ytd) > 0:
                ys = ec_df["equity"].iloc[0] if len(yrs) == 1 else \
                    ec_df.loc[ec_df.index.year == yrs[-2]]["equity"].iloc[-1]
                annual["2026_ytd"] = (ytd["equity"].iloc[-1] / ys - 1) * 100

        dr = ec_df["equity"].pct_change().dropna()
        sharpe = float(np.mean(dr) / np.std(dr) * np.sqrt(252)) if len(dr) > 1 and np.std(dr) > 0 else 0

        if self.all_trades:
            pnls = pd.DataFrame(self.all_trades)["pnl_pct"].values / 100
            wins = pnls[pnls > 0]
            losses = pnls[pnls <= 0]
            wr = len(wins) / len(pnls)
            pf = float(np.sum(wins) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 \
                else (float("inf") if len(wins) > 0 else 0)
            avg_t = float(np.mean(pnls))
        else:
            wr = pf = avg_t = 0

        avg_conc = float(np.mean(self.daily_position_counts)) if self.daily_position_counts else 0
        exposure = round(days_in_market / total_days * 100, 1) if total_days > 0 else 0

        return {"cagr": round(cagr * 100, 2), "total_return": round(total_ret * 100, 2),
                "max_dd": round(dd * 100, 2), "sharpe": round(sharpe, 3),
                "trades": len(self.all_trades), "win_rate": round(wr * 100, 1),
                "profit_factor": round(pf, 2), "avg_trade": round(avg_t * 100, 2),
                "annual": annual, "exposure": exposure, "avg_concurrent": round(avg_conc, 2)}


# ── Run single variant ─────────────────────────────────────────

def run_variant(params, data, signal_dict, atr_dict, ihsg_df):
    bt = PortfolioBacktest(params, ihsg_df)
    _, _, m = bt.run(data, signal_dict, atr_dict)
    return m


# ── Scoring ─────────────────────────────────────────────────────

def score_variant(m):
    """Composite score from ranking weights. Higher = better."""
    if m is None:
        return -999
    s = 0
    s += RANK_WEIGHTS["cagr"] * max(m["cagr"], -50) / 50
    s += RANK_WEIGHTS["max_dd"] * max(m["max_dd"], -100) / (-100)
    s += RANK_WEIGHTS["pf"] * min(m["profit_factor"], 5) / 5
    t = min(m["trades"], 500)
    s += RANK_WEIGHTS["trades"] * t / 500
    ann = m.get("annual", {})
    yearly = [ann.get(y, 0) for y in ["2023", "2024", "2025"] if y in ann]
    if len(yearly) >= 2:
        stability = 1 - min(np.std(yearly) / 30, 1)
    else:
        stability = 0
    s += RANK_WEIGHTS["annual_stability"] * stability
    return round(s * 100, 1)


def passes_minimum(m):
    if m is None:
        return False, "no result"
    reasons = []
    if m["cagr"] <= MIN_PASS["cagr"]:
        reasons.append(f"CAGR {m['cagr']:+.2f}% ≤ {MIN_PASS['cagr']}%")
    if m["profit_factor"] <= MIN_PASS["pf"]:
        reasons.append(f"PF {m['profit_factor']:.2f} ≤ {MIN_PASS['pf']}")
    if m["max_dd"] <= MIN_PASS["max_dd"]:
        reasons.append(f"DD {m['max_dd']:.2f}% ≤ {MIN_PASS['max_dd']}%")
    if m["trades"] < MIN_PASS["min_trades"]:
        reasons.append(f"trades {m['trades']} < {MIN_PASS['min_trades']}")
    if m["trades"] > MIN_PASS["max_trades"]:
        reasons.append(f"trades {m['trades']} > {MIN_PASS['max_trades']}")

    ann = m.get("annual", {})
    pos_years = [y for y in ["2023", "2024", "2025"] if y in ann and ann[y] >= -5]
    if len(pos_years) < 3:
        controlled_years = [y for y in ["2023", "2024", "2025"] if y in ann and ann[y] >= -10]
        if len(controlled_years) < 3:
            reasons.append(f"<3 years controlled ({len(controlled_years)}/3)")

    yearly_2025_only = all(
        (ann.get(y, 0) <= 0 or abs(ann.get(y, 0)) < 1) for y in ["2023", "2024"]
        if y in ann
    ) and ann.get("2025", 0) > 10
    if yearly_2025_only:
        reasons.append("2025-dependent")

    passed = len(reasons) == 0
    return passed, "; ".join(reasons) if reasons else "pass"


# ── Print helpers ──────────────────────────────────────────────

HEADER_M = f"{'Variant':<30} {'CAGR':>8} {'TotRet':>8} {'DD':>8} {'PF':>7} {'WR':>6} {'Trades':>8} {'AvgTrd':>8} {'Expo':>6} {'Conc':>6} {'Score':>6}"
SEP = "-" * 110


def print_results(results, title):
    print(f"\n\n{'='*110}")
    print(f"  {title}")
    print(f"{'='*110}")
    print(HEADER_M)
    print(SEP)
    for name, m in results:
        if m is None:
            print(f"{name:<30} {'No trades':>20}")
            continue
        print(f"{name:<30} {m['cagr']:>+7.2f}% {m['total_return']:>+7.2f}% "
              f"{m['max_dd']:>7.2f}% {m['profit_factor']:>6.2f} {m['win_rate']:>5.1f}% "
              f"{m['trades']:>8} {m['avg_trade']:>+7.2f}% {m['exposure']:>5.1f}% "
              f"{m['avg_concurrent']:>5.1f} {score_variant(m):>5.1f}")


def print_annual(ann):
    for y in ["2023", "2024", "2025", "2026_ytd"]:
        if y in ann:
            print(f"    {y}: {ann[y]:+.2f}%", end="")
    print()


def print_pass_fail(all_results):
    print(f"\n\n{'='*110}")
    print("  MINIMUM PASSING CRITERIA (CAGR>5%, PF>1.15, DD<25%, trades 50-500, 3y controlled)")
    print(f"{'='*110}")
    passed = 0
    for name, m in all_results:
        ok, reason = passes_minimum(m)
        status = "✅" if ok else "❌"
        print(f"  {status} {name:<30} {reason}")
        if ok:
            passed += 1
            print(f"    CAGR={m['cagr']:+.2f}%  DD={m['max_dd']:.2f}%  PF={m['profit_factor']:.2f}  "
                  f"trades={m['trades']}  ", end="")
            print_annual(m.get("annual", {}))
    print(f"\n  Passed: {passed}/{len(all_results)}")


# ── Main ─────────────────────────────────────────────────────────

def make_params(overrides):
    p = dict(BASELINE)
    p.update(overrides)
    return p


def load_data():
    """Load IDX80 eligible tickers' data."""
    r = get_universe()
    e, nd, lf = filter_liquidity(r)
    data = {}
    for t in e:
        df = load_ticker(t)
        if not df.empty and len(df) > 100:
            data[t] = df
    return data


def main():
    old = settings.STOCK_UNIVERSE
    settings.STOCK_UNIVERSE = "idx80"

    try:
        print("=" * 70)
        print("  LOADING IHSG DATA")
        print("=" * 70)
        ihsg_df = precompute_ihsg(load_ihsg())

        print("\n" + "=" * 70)
        print("  LOADING IDX80 DATA")
        print("=" * 70)
        data = load_data()
        print(f"  Eligible tickers loaded: {len(data)}")
        if len(data) < 3:
            print("  Too few tickers.")
            return

        print("\n  Pre-computing signals...")
        signal_dict, atr_dict = precompute_signals(data)
        print(f"  Signal dict: {len(signal_dict)} tickers")

        all_results = []

        # ── A: IHSG filter ──────────────────────────────────
        print(f"\n\n{'#'*110}")
        print("#  SECTION A: IHSG FILTER VARIANTS")
        print(f"{'#'*110}")
        a_results = []
        for v in A_IHSG:
            p = make_params(v)
            t0 = time.time()
            m = run_variant(p, data, signal_dict, atr_dict, ihsg_df)
            dt = time.time() - t0
            status = f"{m['trades']}t {m['cagr']:+.2f}% CAGR" if m else "no trades"
            log.info(f"  {p['name']:<25} → {status} ({dt:.1f}s)")
            a_results.append((p["name"], m))
            all_results.append((f"A | {p['name']}", m))
        print_results(a_results, "A: IHSG FILTER (stock>MA20, vol>=2.0, stop3_hold15)")

        # ── B: Stock filter ─────────────────────────────────
        # Fix baseline: best A variant is used as baseline for A params
        best_a_name = max(a_results, key=lambda x: score_variant(x[1]) if x[1] else -999)
        best_a_params = next(v for v in A_IHSG if v["name"] == best_a_name[0])

        print(f"\n\n{'#'*110}")
        print(f"#  SECTION B: STOCK FILTER VARIANTS  (IHSG filter={best_a_params['ihsg_filter']})")
        print(f"{'#'*110}")
        b_results = []
        for v in B_STOCK:
            p = make_params({**best_a_params, **v})
            t0 = time.time()
            m = run_variant(p, data, signal_dict, atr_dict, ihsg_df)
            dt = time.time() - t0
            status = f"{m['trades']}t {m['cagr']:+.2f}% CAGR" if m else "no trades"
            log.info(f"  {p['name']:<25} → {status} ({dt:.1f}s)")
            b_results.append((p["name"], m))
            all_results.append((f"B | {p['name']}", m))
        print_results(b_results, f"B: STOCK FILTER (IHSG={best_a_params['ihsg_filter']}, vol>=2.0, stop3_hold15)")

        # ── C: Volume ratio sensitivity ─────────────────────
        best_b_name = max(b_results, key=lambda x: score_variant(x[1]) if x[1] else -999)
        best_b_params = next(v for v in B_STOCK if v["name"] == best_b_name[0])

        print(f"\n\n{'#'*110}")
        print(f"#  SECTION C: VOLUME RATIO  (IHSG={best_a_params['ihsg_filter']}, stock={best_b_params['stock_filter']})")
        print(f"{'#'*110}")
        c_results = []
        for v in C_VOL:
            p = make_params({**best_a_params, **best_b_params, **v})
            t0 = time.time()
            m = run_variant(p, data, signal_dict, atr_dict, ihsg_df)
            dt = time.time() - t0
            status = f"{m['trades']}t {m['cagr']:+.2f}% CAGR" if m else "no trades"
            log.info(f"  {p['name']:<25} → {status} ({dt:.1f}s)")
            c_results.append((p["name"], m))
            all_results.append((f"C | {p['name']}", m))
        print_results(c_results, f"C: VOLUME RATIO (IHSG={best_a_params['ihsg_filter']}, stock={best_b_params['stock_filter']}, stop3_hold15)")

        # ── D: Momentum confirmation ────────────────────────
        best_c_name = max(c_results, key=lambda x: score_variant(x[1]) if x[1] else -999)
        best_c_params = next(v for v in C_VOL if v["name"] == best_c_name[0])

        print(f"\n\n{'#'*110}")
        print(f"#  SECTION D: MOMENTUM CONFIRMATION  (IHSG={best_a_params['ihsg_filter']}, stock={best_b_params['stock_filter']}, vol={best_c_params['vol_ratio']})")
        print(f"{'#'*110}")
        d_results = []
        for v in D_MOM:
            p = make_params({**best_a_params, **best_b_params, **best_c_params, **v})
            t0 = time.time()
            m = run_variant(p, data, signal_dict, atr_dict, ihsg_df)
            dt = time.time() - t0
            status = f"{m['trades']}t {m['cagr']:+.2f}% CAGR" if m else "no trades"
            log.info(f"  {p['name']:<25} → {status} ({dt:.1f}s)")
            d_results.append((p["name"], m))
            all_results.append((f"D | {p['name']}", m))
        print_results(d_results, f"D: MOMENTUM (IHSG={best_a_params['ihsg_filter']}, stock={best_b_params['stock_filter']}, vol={best_c_params['vol_ratio']}, stop3_hold15)")

        # ── E: Exit sensitivity ─────────────────────────────
        best_d_name = max(d_results, key=lambda x: score_variant(x[1]) if x[1] else -999)
        best_d_params = next(v for v in D_MOM if v["name"] == best_d_name[0])

        print(f"\n\n{'#'*110}")
        print(f"#  SECTION E: EXIT PARAMS  (IHSG={best_a_params['ihsg_filter']}, stock={best_b_params['stock_filter']}, vol={best_c_params['vol_ratio']}, mom={best_d_params['momentum']})")
        print(f"{'#'*110}")
        e_results = []
        for v in E_EXIT:
            p = make_params({**best_a_params, **best_b_params, **best_c_params, **best_d_params, **v})
            t0 = time.time()
            m = run_variant(p, data, signal_dict, atr_dict, ihsg_df)
            dt = time.time() - t0
            status = f"{m['trades']}t {m['cagr']:+.2f}% CAGR" if m else "no trades"
            log.info(f"  {p['name']:<25} → {status} ({dt:.1f}s)")
            e_results.append((p["name"], m))
            all_results.append((f"E | {p['name']}", m))
        print_results(e_results, f"E: EXIT PARAMS (IHSG={best_a_params['ihsg_filter']}, stock={best_b_params['stock_filter']}, vol={best_c_params['vol_ratio']}, mom={best_d_params['momentum']})")

        # ── Top N ───────────────────────────────────────────
        ranked = sorted(all_results, key=lambda x: score_variant(x[1]) if x[1] else -999, reverse=True)
        top = ranked[:10]

        print(f"\n\n{'='*110}")
        print("  TOP 10 COMBINED VARIANTS (all sections)")
        print(f"{'='*110}")
        print(HEADER_M)
        print(SEP)
        for name, m in top:
            if m is None:
                continue
            print(f"{name:<30} {m['cagr']:>+7.2f}% {m['total_return']:>+7.2f}% "
                  f"{m['max_dd']:>7.2f}% {m['profit_factor']:>6.2f} {m['win_rate']:>5.1f}% "
                  f"{m['trades']:>8} {m['avg_trade']:>+7.2f}% {m['exposure']:>5.1f}% "
                  f"{m['avg_concurrent']:>5.1f} {score_variant(m):>5.1f}")

        # ── Targeted best combinations ─────────────────────
        print(f"\n\n{'#'*110}")
        print("#  TARGETED BEST COMBINATIONS (from B3: stock>MA50 yielded 46t)")
        print(f"{'#'*110}")
        print(f"\n  Baseline: IHSG>MA50|10d>0, stock>MA50, vol>=2.0, stop3_hold15 → 46t, +5.90% CAGR")
        print(f"  Testing volume ratio + exit improvements to push past 50 trades.\n")
        targeted = []

        base_params = {"ihsg_filter": "ma50_or_10d_up", "stock_filter": "ma50", "vol_ratio": 2.0,
                       "momentum": None, "stop_atr": 3.0, "max_hold_days": 15}

        targets = [
            {"name": "T1: stock>MA50, vol>=1.75"},
            {"name": "T2: stock>MA50, vol>=1.5"},
            {"name": "T3: stock>MA50, stop3_hold20"},
            {"name": "T4: stock>MA50, vol>=1.75, stop3_hold20"},
            {"name": "T5: stock>MA50, vol>=1.5, stop3_hold20"},
            {"name": "T6: stock>MA50, no IHSG, stop3_hold20"},
            {"name": "T7: stock>MA50, no IHSG, vol>=1.5, stop3_hold20"},
            {"name": "T8: stock>MA50, no IHSG, vol>=1.5, stop2_hold20"},
        ]
        target_vals = [
            {"vol_ratio": 1.75},
            {"vol_ratio": 1.5},
            {"max_hold_days": 20},
            {"vol_ratio": 1.75, "max_hold_days": 20},
            {"vol_ratio": 1.5, "max_hold_days": 20},
            {"ihsg_filter": None, "max_hold_days": 20},
            {"ihsg_filter": None, "vol_ratio": 1.5, "max_hold_days": 20},
            {"ihsg_filter": None, "vol_ratio": 1.5, "stop_atr": 2.0, "max_hold_days": 20},
        ]

        for cfg, vals in zip(targets, target_vals):
            p = make_params({**base_params, **vals, **cfg})
            t0 = time.time()
            m = run_variant(p, data, signal_dict, atr_dict, ihsg_df)
            dt = time.time() - t0
            status = f"{m['trades']}t {m['cagr']:+.2f}% CAGR" if m else "no trades"
            log.info(f"  {cfg['name']:<30} → {status} ({dt:.1f}s)")
            targeted.append((cfg["name"], m))
            all_results.append((f"T | {cfg['name']}", m))

        print_results(targeted, "TARGETED: stock>MA50 combos")

        # ── Pass/Fail ───────────────────────────────────────
        print_pass_fail(all_results)

        # ── Final recommendation ────────────────────────────
        passing = [(n, m) for n, m in all_results if passes_minimum(m)[0]]
        print(f"\n\n{'='*110}")
        print("  FINAL RECOMMENDATION")
        print(f"{'='*110}")
        if passing:
            best = max(passing, key=lambda x: score_variant(x[1]) if x[1] else -999)
            print(f"\n  ✅ Best passing variant: {best[0]}")
            print(f"     CAGR={best[1]['cagr']:+.2f}%  DD={best[1]['max_dd']:.2f}%  "
                  f"PF={best[1]['profit_factor']:.2f}  trades={best[1]['trades']}")
            ann = best[1].get("annual", {})
            print("     Annual:", end="")
            print_annual(ann)
            print(f"\n  → Use relaxed TREND_FILTERED as main strategy on IDX80")
        else:
            print(f"\n  ❌ No variant passes all minimum criteria on IDX80.")
            print(f"     {len(all_results)} variants tested, 0 pass.")
            print(f"\n  → Volume divergence on IDX80 cannot generate enough")
            print(f"     quality trades even with relaxed filters.")
            print(f"  → Keep TREND_FILTERED as a high-quality but rare signal.")
            print(f"  → Add a second signal family (e.g., momentum or RSI)")
            print(f"    to generate additional trade opportunities.")
            print(f"  → Consider evaluating on the custom 15-ticker universe")
            print(f"    where liquidity is higher, before abandoning entirely.")

    finally:
        settings.STOCK_UNIVERSE = old
        print(f"\nSettings restored to: {settings.STOCK_UNIVERSE}")


if __name__ == "__main__":
    main()
