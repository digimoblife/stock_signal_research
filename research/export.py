"""
export.py — exports signals.db to CSV files for spreadsheet analysis.

Usage:
  python run.py export

Output in exports/:
  signals.csv         — all signals with status
  trades.csv          — all closed trades
  weekly_summary.csv  — weekly aggregate metrics
  monthly_summary.csv — monthly aggregate metrics
  dashboard.csv       — single-row summary snapshot
"""
import csv
import os
from pathlib import Path
from datetime import datetime

import numpy as np

from track import connect

EXPORT_DIR = Path("exports")


def _ensure_dir():
    EXPORT_DIR.mkdir(exist_ok=True)


def _write_csv(filename, headers, rows):
    path = EXPORT_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


def _fmt(val):
    """Format a value for CSV — None becomes empty string."""
    return "" if val is None else val


def export_signals():
    """Export all signals with their current status."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT s.id, s.date, s.ticker, s.direction, s.confidence,
                      s.strategy,
                      (s.entry_low + s.entry_high) / 2.0,
                      s.stop_loss, s.take_profit,
                      CASE WHEN t.id IS NULL THEN 'open'
                           WHEN t.exit_reason = 'still_open' THEN 'open'
                           ELSE 'closed'
                      END,
                      s.created_at
               FROM signals s
               LEFT JOIN trades t ON s.id = t.signal_id
               ORDER BY s.date DESC"""
        ).fetchall()
    finally:
        conn.close()

    headers = [
        "signal_id", "signal_date", "ticker", "direction", "confidence",
        "strategy", "market_regime", "entry_price", "stop_price",
        "target_price", "status", "created_at",
    ]
    data = [[_fmt(c) for c in r] for r in rows]
    return _write_csv("signals.csv", headers, data)


def export_trades():
    """Export all trades."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT signal_id, ticker, entry_date, exit_date,
                      entry_price, exit_price, pnl_pct, days_held,
                      exit_reason, source
               FROM trades
               ORDER BY entry_date DESC"""
        ).fetchall()
    finally:
        conn.close()

    headers = [
        "signal_id", "ticker", "entry_date", "exit_date",
        "entry_price", "exit_price", "pnl_pct", "holding_days",
        "exit_reason", "source",
    ]
    data = [[_fmt(c) for c in r] for r in rows]
    return _write_csv("trades.csv", headers, data)


def export_weekly_summary():
    """Aggregate closed trades by ISO week."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT strftime('%Y-W%W', entry_date) as week,
                      COUNT(*) as total,
                      SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN pnl_pct <= 0 AND pnl_pct IS NOT NULL
                           THEN 1 ELSE 0 END) as losses,
                      SUM(CASE WHEN exit_reason = 'time_stop' THEN 1 ELSE 0 END) as expired,
                      ROUND(AVG(pnl_pct), 2) as avg_return
               FROM trades
               WHERE exit_reason IS NOT NULL
                 AND exit_reason != 'still_open'
               GROUP BY week
               ORDER BY week DESC"""
        ).fetchall()
    finally:
        conn.close()

    headers = ["week", "signals", "wins", "losses", "expired",
               "precision_pct", "avg_return_pct"]
    data = []
    for r in rows:
        total = r["total"]
        wins = r["wins"] or 0
        precision = round(wins / total * 100, 1) if total > 0 else 0.0
        data.append([
            r["week"], total, wins, r["losses"] or 0, r["expired"] or 0,
            precision, r["avg_return"] or 0.0,
        ])
    return _write_csv("weekly_summary.csv", headers, data)


def export_monthly_summary():
    """Aggregate closed trades by month."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT strftime('%Y-%m', entry_date) as month,
                      COUNT(*) as total,
                      SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN pnl_pct <= 0 AND pnl_pct IS NOT NULL
                           THEN 1 ELSE 0 END) as losses,
                      SUM(CASE WHEN exit_reason = 'time_stop' THEN 1 ELSE 0 END) as expired,
                      ROUND(AVG(pnl_pct), 2) as avg_return,
                      ROUND(AVG(days_held), 1) as avg_days
               FROM trades
               WHERE exit_reason IS NOT NULL
                 AND exit_reason != 'still_open'
               GROUP BY month
               ORDER BY month DESC"""
        ).fetchall()
    finally:
        conn.close()

    headers = ["month", "signals", "wins", "losses", "expired",
               "precision_pct", "avg_return_pct", "avg_holding_days"]
    data = []
    for r in rows:
        total = r["total"]
        wins = r["wins"] or 0
        precision = round(wins / total * 100, 1) if total > 0 else 0.0
        data.append([
            r["month"], total, wins, r["losses"] or 0, r["expired"] or 0,
            precision, r["avg_return"] or 0.0, r["avg_days"] or 0.0,
        ])
    return _write_csv("monthly_summary.csv", headers, data)


def _compute_streaks(pnls):
    """Compute longest win and loss streaks from an ordered list of pnls."""
    longest_win = 0
    longest_loss = 0
    current_win = 0
    current_loss = 0
    for p in pnls:
        if p > 0:
            current_win += 1
            current_loss = 0
            longest_win = max(longest_win, current_win)
        elif p < 0:
            current_loss += 1
            current_win = 0
            longest_loss = max(longest_loss, current_loss)
        else:
            current_win = 0
            current_loss = 0
    return longest_win, longest_loss


def export_dashboard():
    """Export single-row dashboard with aggregate metrics."""
    conn = connect()
    try:
        # Total signals
        total_signals = conn.execute(
            "SELECT COUNT(*) FROM signals"
        ).fetchone()[0]

        # Closed trades ordered by exit_date for streak computation
        trade_rows = conn.execute(
            """SELECT pnl_pct, days_held
               FROM trades
               WHERE exit_reason IS NOT NULL
                 AND exit_reason != 'still_open'
               ORDER BY exit_date ASC"""
        ).fetchall()

        # Win/loss counts
        closed = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                      SUM(CASE WHEN pnl_pct <= 0 AND pnl_pct IS NOT NULL
                           THEN 1 ELSE 0 END) as losses
               FROM trades
               WHERE exit_reason IS NOT NULL
                 AND exit_reason != 'still_open'"""
        ).fetchone()
    finally:
        conn.close()

    total_closed = closed["total"] or 0
    wins = closed["wins"] or 0
    losses = closed["losses"] or 0

    if total_closed == 0:
        row = [
            total_signals, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0,
        ]
        headers = [
            "total_signals", "total_closed_trades", "win_rate_pct",
            "profit_factor", "avg_return_pct", "avg_holding_days",
            "longest_win_streak", "longest_loss_streak",
            "current_drawdown_pct",
        ]
        return _write_csv("dashboard.csv", headers, [row])

    pnls = np.array([r["pnl_pct"] for r in trade_rows if r["pnl_pct"] is not None])
    days = np.array([r["days_held"] for r in trade_rows if r["days_held"] is not None])

    win_pnls = pnls[pnls > 0]
    loss_pnls = pnls[pnls <= 0]

    win_rate = round(wins / total_closed * 100, 1)
    sum_wins = float(np.sum(win_pnls))
    sum_losses = float(abs(np.sum(loss_pnls)))
    profit_factor = round(sum_wins / sum_losses, 2) if sum_losses > 0 else float("inf")
    avg_return = round(float(np.mean(pnls)), 2) if len(pnls) > 0 else 0.0
    avg_holding = round(float(np.mean(days)), 1) if len(days) > 0 else 0.0

    longest_win, longest_loss = _compute_streaks(pnls)

    # Drawdown: cumulative returns from peak
    cumulative = np.cumprod(1 + pnls / 100)
    peak = np.maximum.accumulate(cumulative)
    drawdown_pct = float(np.min((cumulative - peak) / peak * 100))

    row = [
        total_signals, total_closed, win_rate,
        profit_factor, avg_return, avg_holding,
        longest_win, longest_loss,
        round(drawdown_pct, 1),
    ]
    headers = [
        "total_signals", "total_closed_trades", "win_rate_pct",
        "profit_factor", "avg_return_pct", "avg_holding_days",
        "longest_win_streak", "longest_loss_streak",
        "current_drawdown_pct",
    ]
    return _write_csv("dashboard.csv", headers, [row])


def run():
    """Run all exports and print summary."""
    _ensure_dir()

    counts = {
        "signals.csv": export_signals(),
        "trades.csv": export_trades(),
        "weekly_summary.csv": export_weekly_summary(),
        "monthly_summary.csv": export_monthly_summary(),
        "dashboard.csv": export_dashboard(),
    }

    print("\nExport completed.\n")
    for name, count in counts.items():
        label = name.ljust(25)
        if name == "dashboard.csv":
            print(f"{label} {count} row")
        else:
            print(f"{label} {count} rows")
    print(f"\nFiles in: {EXPORT_DIR.resolve()}")
