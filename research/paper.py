"""
paper.py — T6_TREND_FILTERED paper trading engine.

Tracks positions, checks exits daily, records P&L.
"""
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from settings import (
    T6_STOP_ATR, T6_MAX_HOLD_DAYS, T6_COST_PER_TRADE,
    T6_STARTING_CAPITAL, T6_MAX_POSITIONS,
)
from research import load_ticker, volume_divergence_signals
from gen_signal import compute_atr
import filter as filter_module
from track import (
    get_open_paper_trades, save_paper_trade,
    update_paper_trade, get_closed_paper_trades,
    get_paper_performance,
)

log = logging.getLogger("paper")


def daily_paper_cycle(today: str = None) -> list[dict]:
    """
    Full daily paper cycle:
      1. Check open positions for stop/max_hold exit
      2. Generate T6 signals
      3. Record new paper positions
    Returns list of events (entries, exits) for reporting.
    """
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")
    today_dt = datetime.strptime(today, "%Y-%m-%d")

    events = []

    # 1. Check open positions
    exits = _check_exits(today_dt)
    events.extend(exits)

    # 2. Generate T6 signals
    signals = _t6_signals(today)

    # 3. Enter new positions
    for sig in signals:
        pt_id = _enter_paper_position(sig, today)
        if pt_id:
            sig["paper_trade_id"] = pt_id
            events.append({"type": "entry", "signal": sig})

    return events


def _check_exits(today_dt: datetime) -> list[dict]:
    """Check all open positions for stop loss or max hold exit."""
    open_positions = get_open_paper_trades()
    exits = []

    for pos in open_positions:
        ticker = pos["ticker"]
        strategy = pos["strategy"]

        df = load_ticker(ticker)
        if df.empty:
            continue

        today_str = today_dt.strftime("%Y-%m-%d")
        if today_str not in df.index:
            # No trading data for today yet
            continue

        row = df.loc[today_str]
        low = row["low"]
        close = row["close"]
        high = row["high"]

        # Update high water mark
        hwm = pos.get("high_water_mark") or pos.get("entry_price", 0)
        new_hwm = max(hwm, high)
        if new_hwm > hwm:
            update_paper_trade(pos["id"], {"high_water_mark": new_hwm})

        entry_price = pos.get("entry_price")
        if not entry_price:
            continue

        # Stop loss check
        stop_price = pos.get("stop_loss")
        if stop_price and low <= stop_price:
            exit_price = stop_price * (1 - T6_COST_PER_TRADE / 2)
            pnl_pct = (exit_price / entry_price - 1) * 100
            pnl_abs = _compute_pnl_absolute(pos, exit_price)
            days_held = (today_dt - datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days

            update_paper_trade(pos["id"], {
                "exit_date": today_str, "exit_price": round(exit_price, 2),
                "exit_reason": "stop_loss", "status": "PAPER_CLOSED",
                "pnl_pct": round(pnl_pct, 2), "pnl_absolute": round(pnl_abs, 0),
                "days_held": days_held,
            })
            log.info(f"📉 Paper stop loss: {ticker} at {exit_price:,.0f} ({pnl_pct:+.2f}%)")
            exits.append({"type": "exit", "reason": "stop_loss", "ticker": ticker,
                         "pnl_pct": pnl_pct, "days_held": days_held})
            continue

        # Max hold check
        max_hold_end = pos.get("max_hold_end")
        if max_hold_end and today_str >= max_hold_end:
            exit_price = close * (1 - T6_COST_PER_TRADE / 2)
            pnl_pct = (exit_price / entry_price - 1) * 100
            pnl_abs = _compute_pnl_absolute(pos, exit_price)
            days_held = (today_dt - datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days

            update_paper_trade(pos["id"], {
                "exit_date": today_str, "exit_price": round(exit_price, 2),
                "exit_reason": "time_stop", "status": "PAPER_CLOSED",
                "pnl_pct": round(pnl_pct, 2), "pnl_absolute": round(pnl_abs, 0),
                "days_held": days_held,
            })
            log.info(f"⏰ Paper time stop: {ticker} at {exit_price:,.0f} ({pnl_pct:+.2f}%)")
            exits.append({"type": "exit", "reason": "time_stop", "ticker": ticker,
                         "pnl_pct": pnl_pct, "days_held": days_held})

    return exits


def _compute_pnl_absolute(pos, exit_price):
    """Compute absolute P&L for a position."""
    entry_price = pos.get("entry_price")
    shares = pos.get("shares")
    if not entry_price or not shares:
        return 0
    entry_cost = shares * entry_price
    exit_proceeds = shares * exit_price
    return exit_proceeds - entry_cost


def _t6_signals(today: str) -> list[dict]:
    """
    Generate T6_TREND_FILTERED signals for today.
    Uses: volume_divergence signal + stock close > MA50 + vol_ratio >= 2.0 + large/mid cap.
    Temporarily switches universe to IDX80 if not already set.
    Enforces max 10 concurrent positions.
    """
    import settings as _s
    old_univ = _s.STOCK_UNIVERSE
    _s.STOCK_UNIVERSE = _s.T6_STOCK_UNIVERSE

    try:
        from universe import get_eligible_tickers
        from settings import T6_MIN_VOL_RATIO, T6_STOCK_MA_PERIOD, T6_MAX_POSITIONS

        open_positions = get_open_paper_trades()
        open_tickers = {p["ticker"] for p in open_positions}
        slots_remaining = T6_MAX_POSITIONS - len(open_positions)

        if slots_remaining <= 0:
            log.info(f"T6: max {T6_MAX_POSITIONS} positions reached, no new entries")
            return []

        tickers, _ = get_eligible_tickers()
        log.info(f"T6 scan: {len(tickers)} eligible tickers, {slots_remaining} slots open")

        signals = []

        for ticker in tickers:
            if ticker in open_tickers:
                continue

            df = load_ticker(ticker)
            if df.empty or len(df) < T6_STOCK_MA_PERIOD + 10:
                continue

            sig = volume_divergence_signals(df)
            if sig.empty or "signal" not in sig.columns:
                continue

            # Find latest BUY signal in last 10 days
            cutoff = df.index[-1] - pd.Timedelta(days=10)
            recent = sig[(sig["signal"] == 1) & (sig.index >= cutoff)]
            if recent.empty:
                continue

            latest = recent.iloc[-1]
            signal_date = latest.name

            # Current values
            current_idx = -1
            close = df["close"].iloc[current_idx]
            atr = compute_atr(df).iloc[current_idx]
            if pd.isna(atr) or atr <= 0:
                continue

            # MA50 filter
            ma50 = df["close"].rolling(T6_STOCK_MA_PERIOD).mean().iloc[current_idx]
            if pd.isna(ma50) or close <= ma50:
                continue

            # Volume ratio filter
            idx_loc = df.index.get_loc(signal_date)
            prev_vol = df["volume"].iloc[max(0, idx_loc - 5):idx_loc].mean()
            vol_ratio = df["volume"].iloc[idx_loc] / prev_vol if prev_vol > 0 else 0
            if vol_ratio < T6_MIN_VOL_RATIO:
                continue

            # Liquidity filter
            liq = filter_module.classify_liquidity(ticker)
            if liq not in ("large", "mid"):
                continue

            # Stop loss
            stop_price = close - T6_STOP_ATR * atr
            if stop_price <= 0:
                continue

            # Max hold end (calendar days)
            max_hold_end = _add_trading_days(datetime.now(), T6_MAX_HOLD_DAYS)

            # Position sizing
            from settings import T6_STARTING_CAPITAL, T6_MAX_POSITIONS
            pos_size = T6_STARTING_CAPITAL / T6_MAX_POSITIONS
            shares = int(pos_size / close)
            if shares <= 0:
                continue

            # Entry price with transaction cost
            entry_price = close * (1 + T6_COST_PER_TRADE / 2)

            signals.append({
                "ticker": ticker,
                "signal_date": signal_date.strftime("%Y-%m-%d") if hasattr(signal_date, "strftime") else str(signal_date),
                "close_at_signal": round(close, 2),
                "ma50": round(ma50, 2),
                "vol_ratio": round(vol_ratio, 2),
                "atr": round(atr, 2),
                "stop_loss": round(stop_price, 2),
                "entry_price_plan": round(entry_price, 2),
                "max_hold_end": max_hold_end.strftime("%Y-%m-%d"),
                "shares": shares,
                "reason": "volume_divergence: 2-day bullish streak, close > MA50, vol_ratio >= 2.0",
            })

        # Sort by confidence proxy (vol_ratio * recent performance) and limit to slots
        for s in signals:
            s["_score"] = s["vol_ratio"] * (s["close_at_signal"] / s["ma50"])
        signals.sort(key=lambda s: s["_score"], reverse=True)
        signals = signals[:slots_remaining]
        for s in signals:
            del s["_score"]

        return signals
    finally:
        _s.STOCK_UNIVERSE = old_univ


def _enter_paper_position(sig: dict, today: str) -> str | None:
    """Record a new paper trade from a signal."""
    trade = {
        "strategy": "T6_TREND_FILTERED",
        "ticker": sig["ticker"],
        "signal_date": sig["signal_date"],
        "entry_date": today,
        "entry_price": sig["entry_price_plan"],
        "stop_loss": sig["stop_loss"],
        "atr_at_entry": sig["atr"],
        "ma50_at_entry": sig["ma50"],
        "vol_ratio_at": sig["vol_ratio"],
        "max_hold_end": sig["max_hold_end"],
        "shares": sig["shares"],
        "direction": "BUY",
        "status": "PAPER_OPEN",
        "high_water_mark": sig["close_at_signal"],
    }
    trade_id = save_paper_trade(trade)
    if trade_id:
        log.info(f"📗 Paper entry: {sig['ticker']} at {sig['entry_price_plan']:,.0f} "
                 f"stop={sig['stop_loss']:,.0f} hold_until={sig['max_hold_end']}")
    return trade_id


def _add_trading_days(date, n):
    """Add n trading days to a date (calendar days approximation)."""
    result = date
    added = 0
    while added < n:
        result += timedelta(days=1)
        if result.weekday() < 5:
            added += 1
    return result


def get_open_summary() -> str:
    """Human-readable summary of open paper positions."""
    open_pos = get_open_paper_trades()
    if not open_pos:
        return "No open paper positions."

    lines = [f"📋 Open T6 positions ({len(open_pos)}):"]
    today = datetime.now()
    for p in open_pos:
        entry_date = datetime.strptime(p["entry_date"], "%Y-%m-%d")
        days_open = (today - entry_date).days
        entry_price = p.get("entry_price", 0)
        current_price = _get_current_price(p["ticker"])
        if current_price and entry_price:
            pnl = (current_price / entry_price - 1) * 100
            line = f"  {p['ticker']:<6} entry={entry_price:>8,.0f} curr={current_price:>8,.0f} " \
                   f"P&L={pnl:>+6.1f}%  {days_open}d  stop={p.get('stop_loss', 0):,.0f}"
        else:
            line = f"  {p['ticker']:<6} entry={entry_price:>8,.0f}  {days_open}d"
        lines.append(line)
    return "\n".join(lines)


def _get_current_price(ticker):
    """Get latest close price for a ticker."""
    df = load_ticker(ticker)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])


def format_paper_performance() -> str:
    """Format paper performance as human-readable string."""
    perf = get_paper_performance()
    lines = [
        f"📊 T6_TREND_FILTERED — Paper Performance",
        f"{'=' * 40}",
        f"Status: {perf.get('total_signals', 0)} total signals, "
        f"{perf['closed_trades']} closed, {perf['open_positions']} open",
    ]
    if perf.get("closed_trades", 0) > 0:
        lines.extend([
            f"Win rate:  {perf['win_rate']}%",
            f"Avg trade: {perf['avg_trade']:+.2f}%",
            f"Total ret: {perf['total_return_pct']:+.2f}%",
            f"Total abs: {perf['total_return_abs']:+,.0f} IDR",
            f"Profit factor: {perf['profit_factor']}",
            f"Max DD est: {perf['max_drawdown']:.2f}%",
            f"Avg days held: {perf.get('avg_days_held', 0)}",
        ])
    if perf.get("open_positions", 0) > 0:
        tickers = ", ".join(perf.get("open_tickers", []))
        lines.append(f"Open: {tickers}")
    return "\n".join(lines)


def compare_with_backtest() -> str:
    """Compare paper performance with backtest expectations."""
    perf = get_paper_performance()
    lines = [f"📈 T6 Backtest vs Paper Comparison", f"{'=' * 40}"]

    bt_cagr = 6.07
    bt_pf = 2.23
    bt_dd = -4.75
    bt_trades_per_year = 73 / 3.47  # ~21 trades/year

    closed = perf.get("closed_trades", 0)
    lines.append(f"Backtest expectation (T6):")
    lines.append(f"  CAGR: {bt_cagr:+.2f}%  PF: {bt_pf:.2f}  DD: {bt_dd:.2f}%  "
                 f"~{bt_trades_per_year:.0f} trades/year")
    lines.append(f"")
    lines.append(f"Paper actual:")
    if closed > 0:
        lines.append(f"  CAGR: N/A (est after ≥1yr)  PF: {perf['profit_factor']:.2f}  "
                     f"DD: {perf['max_drawdown']:.2f}%")
        lines.append(f"  Win rate: {perf['win_rate']}%  Avg trade: {perf['avg_trade']:+.2f}%")
        trades_per_year = closed / (max((datetime.now() - datetime(2023, 1, 1)).days / 365.25, 0.5))
        lines.append(f"  Trades/year: {trades_per_year:.0f}  (target: {bt_trades_per_year:.0f})")
    else:
        lines.append(f"  No closed trades yet.")

    return "\n".join(lines)
