"""
monitor.py — health tracking for unattended VPS operation.

Designed to run:
  - Daily (as part of the cron cycle)
  - On-demand via python run.py health
  - Weekly (full performance report to Telegram)

Every check produces a status: PASS, WARN, or FAIL.
The overall system health is the worst status across all checks.
"""
import logging
import sqlite3
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

from settings import DATA_DIR, DB_PATH, TICKERS, TOTAL_COST

DATA_DIR = Path(DATA_DIR)
DB_PATH = Path(DB_PATH)

log = logging.getLogger("monitor")

# ── Results collector ──────────────────────────────────────────

class HealthReport:
    """Collects check results and produces a summary."""

    def __init__(self):
        self.checks = []
        self.worst_status = "PASS"

    def add(self, name, status, detail=""):
        """Add a check result. Status: PASS, WARN, FAIL."""
        self.checks.append({"name": name, "status": status, "detail": detail})
        severity = {"PASS": 0, "WARN": 1, "FAIL": 2}
        if severity.get(status, 0) > severity.get(self.worst_status, 0):
            self.worst_status = status

    def summary(self):
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c["status"] == "PASS")
        warned = sum(1 for c in self.checks if c["status"] == "WARN")
        failed = sum(1 for c in self.checks if c["status"] == "FAIL")
        return {
            "total": total, "passed": passed,
            "warned": warned, "failed": failed,
            "status": self.worst_status,
        }

    def text(self, title="SYSTEM HEALTH"):
        """Format as plain text for Telegram/console."""
        lines = [f"🩺 {title}", ""]
        for c in self.checks:
            emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(c["status"], "❓")
            lines.append(f"{emoji} {c['name']}: {c['status']}")
            if c["detail"]:
                lines.append(f"   {c['detail']}")
        lines.append("")
        s = self.summary()
        lines.append(f"Result: {s['passed']}/{s['total']} passed, "
                     f"{s['warned']} warnings, {s['failed']} failures")
        lines.append(f"Overall: {self.worst_status}")
        return "\n".join(lines)


# ── Database health ──────────────────────────────────────────────

def check_db_integrity(report):
    """Run SQLite integrity_check and verify table structure."""
    if not DB_PATH.exists():
        report.add("Database file", "FAIL", f"File not found at {DB_PATH}")
        return

    if DB_PATH.stat().st_size == 0:
        report.add("Database file", "FAIL", "File is empty (0 bytes)")
        return

    # SQLite integrity check
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        if result != "ok":
            report.add("DB integrity", "FAIL", f"Integrity check: {result}")
        else:
            report.add("DB integrity", "PASS")

        # Check required tables exist
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        required = {"signals", "trades"}
        missing = required - tables
        if missing:
            report.add("DB tables", "FAIL", f"Missing tables: {missing}")
        else:
            report.add("DB tables", "PASS")

        # Check for NULLs in critical signal fields
        for table, fields in [("signals", ["ticker", "direction", "confidence"]),
                              ("trades", ["ticker", "direction"])]:
            for field in fields:
                try:
                    nulls = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {field} IS NULL"
                    ).fetchone()[0]
                    if nulls > 0:
                        report.add(f"DB nulls: {table}.{field}", "WARN",
                                   f"{nulls} rows with NULL {field}")
                except sqlite3.OperationalError:
                    report.add(f"DB nulls: {table}.{field}", "FAIL",
                               f"Column {field} does not exist")

        conn.close()

    except sqlite3.DatabaseError as e:
        report.add("DB integrity", "FAIL", f"Database error: {e}")


def check_db_size(report):
    """Warn if database is growing abnormally."""
    if not DB_PATH.exists():
        return
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    if size_mb > 100:
        report.add("DB size", "WARN", f"{size_mb:.1f} MB — consider vacuuming")
    else:
        report.add("DB size", "PASS", f"{size_mb:.1f} MB")


# ── Data health ──────────────────────────────────────────────────

def check_data_files(report):
    """Verify CSV files exist, are non-empty, and have recent data."""
    tickers_with_data = 0
    missing = []
    stale = []
    empty = []

    for ticker in TICKERS:
        path = DATA_DIR / f"{ticker}.csv"
        if not path.exists():
            missing.append(ticker)
            continue
        if path.stat().st_size == 0:
            empty.append(ticker)
            continue

        tickers_with_data += 1

        # Check data recency: last date should be within 10 business days
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.empty:
            empty.append(ticker)
            continue

        last_date = df.index[-1]
        business_days_ago = len(pd.bdate_range(last_date, datetime.now()))
        if business_days_ago > 10:
            stale.append(f"{ticker} (last: {last_date.date()}, {business_days_ago} days ago)")

    total = len(TICKERS)
    if missing:
        report.add("Data files", "FAIL", f"Missing: {', '.join(missing)}")
    elif tickers_with_data < total * 0.8:
        report.add("Data files", "FAIL",
                   f"Only {tickers_with_data}/{total} tickers have data")
    else:
        report.add("Data files", "PASS",
                   f"{tickers_with_data}/{total} tickers have data")

    if stale:
        report.add("Data freshness", "WARN",
                   f"Stale tickers: {'; '.join(stale[:5])}")
    elif tickers_with_data > 0:
        report.add("Data freshness", "PASS")

    if empty:
        report.add("Data files", "WARN", f"Empty files: {', '.join(empty)}")


def check_daily_update(report):
    """Check if today's data download had issues."""
    log_path = Path("logs/daily.log")
    if not log_path.exists():
        report.add("Daily log", "WARN", "No daily.log found (may be first run)")
        return

    content = log_path.read_text()
    today = datetime.now().strftime("%Y-%m-%d")

    if today in content:
        if "ERROR" in content or "Traceback" in content or "failed" in content.lower():
            report.add("Daily run", "WARN", "Log contains errors — check logs/daily.log")
        else:
            report.add("Daily run", "PASS")
    else:
        # Check yesterday (in case today hasn't run yet)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if yesterday in content:
            report.add("Daily run", "PASS", "Last run: yesterday")
        else:
            report.add("Daily run", "FAIL", "No recent daily run found in logs")


# ── Signal health ────────────────────────────────────────────────

def check_recent_signals(report):
    """Check for signal anomalies: duplicates, too many, too few."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # Duplicate signals: same ticker + same day
        dups = conn.execute(
            """SELECT date, ticker, COUNT(*) as cnt
               FROM signals
               GROUP BY date, ticker
               HAVING cnt > 1
               ORDER BY cnt DESC
               LIMIT 5"""
        ).fetchall()
        if dups:
            for d in dups:
                report.add("Duplicate signals", "WARN",
                           f"{d['ticker']} on {d['date']}: {d['cnt']} signals")
        else:
            report.add("Duplicate signals", "PASS")

        # Signal frequency: average signals per trading day
        recent = conn.execute(
            """SELECT COUNT(*) as cnt,
                      MIN(date) as first_date,
                      MAX(date) as last_date
               FROM signals
               WHERE date >= date('now', '-30 days')"""
        ).fetchone()
        if recent and recent["cnt"] > 0:
            days_range = max(1, (
                datetime.strptime(recent["last_date"], "%Y-%m-%d") -
                datetime.strptime(recent["first_date"], "%Y-%m-%d")
            ).days)
            per_day = recent["cnt"] / days_range
            if per_day > 3:
                report.add("Signal rate", "WARN",
                           f"{recent['cnt']} signals in 30 days ({per_day:.1f}/day)")
            elif per_day > 0:
                report.add("Signal rate", "PASS",
                           f"{recent['cnt']} signals in 30 days ({per_day:.1f}/day)")
            else:
                report.add("Signal rate", "INFO", "No signals in 30 days")
        else:
            report.add("Signal rate", "INFO", "No signals yet")

        # Orphan signals: no matching trade
        orphaned = conn.execute(
            """SELECT COUNT(*) as cnt FROM signals s
               LEFT JOIN trades t ON s.id = t.signal_id
               WHERE t.id IS NULL
               AND s.date < date('now', '-20 days')"""
        ).fetchone()
        if orphaned and orphaned["cnt"] > 5:
            report.add("Orphaned signals", "WARN",
                       f"{orphaned['cnt']} signals >20 days old with no trade outcome")

        # Unresolved trades: still_open for too long
        stuck = conn.execute(
            """SELECT COUNT(*) as cnt FROM trades
               WHERE exit_reason = 'still_open'
               AND entry_date < date('now', '-30 days')"""
        ).fetchone()
        if stuck and stuck["cnt"] > 0:
            report.add("Stuck trades", "WARN",
                       f"{stuck['cnt']} trades open >30 days")

    finally:
        conn.close()


# ── Telegram health ──────────────────────────────────────────────

def check_telegram(report):
    """Test if Telegram bot is reachable."""
    try:
        from telegram_sender import send
        ok = send("🩺 Health check")
        if ok:
            report.add("Telegram", "PASS")
        else:
            report.add("Telegram", "FAIL", "Bot did not send message")
    except Exception as e:
        report.add("Telegram", "FAIL", str(e)[:80])


# ── Rolling metrics ─────────────────────────────────────────────

def compute_rolling_metrics(window=30):
    """Compute rolling performance over last N trades."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT t.pnl_pct, s.strategy as signal_type, t.entry_date
               FROM trades t
               JOIN signals s ON t.signal_id = s.id
               WHERE t.exit_reason IS NOT NULL
               AND t.exit_reason != 'still_open'
               ORDER BY t.entry_date DESC
               LIMIT ?""",
            (window,)
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 5:
        return {"error": f"Only {len(rows)} closed trades (need 5+)", "count": len(rows)}

    pnls = np.array([r["pnl_pct"] for r in rows if r["pnl_pct"] is not None])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    avg_days = 7.9  # from audit
    ann_factor = np.sqrt(252 / avg_days)

    return {
        "trades": len(pnls),
        "window": window,
        "precision": round(len(wins) / len(pnls) * 100, 1),
        "sharpe": round(
            (np.mean(pnls) / np.std(pnls) * ann_factor)
            if len(pnls) > 1 and np.std(pnls) > 0 else 0, 3
        ),
        "profit_factor": round(
            abs(sum(wins) / sum(losses))
            if len(losses) > 0 and sum(losses) != 0 else float("inf"), 2
        ),
        "avg_return": round(float(np.mean(pnls)), 3),
        "total_return": round(float(np.sum(pnls)), 3),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
    }


def check_rolling_metrics(report):
    """Check rolling 30 and 60 trade metrics against thresholds."""
    for window in [30, 60]:
        metrics = compute_rolling_metrics(window=window)
        if "error" in metrics:
            report.add(f"Rolling {window}t metrics", "INFO", metrics["error"])
            continue

        status = "PASS"
        issues = []

        if metrics["precision"] < 50:
            status = "FAIL"
            issues.append(f"precision {metrics['precision']}% < 50%")
        elif metrics["precision"] < 55:
            status = "WARN"
            issues.append(f"precision {metrics['precision']}% below target (55%)")

        if metrics["sharpe"] < 0:
            status = "FAIL"
            issues.append(f"Sharpe {metrics['sharpe']} < 0")
        elif metrics["sharpe"] < 0.5:
            status = "WARN" if status != "FAIL" else status
            issues.append(f"Sharpe {metrics['sharpe']} below target (0.5)")

        if metrics["profit_factor"] < 1.0 and metrics["trades"] > 10:
            status = "FAIL"
            issues.append(f"PF {metrics['profit_factor']} < 1.0")

        detail = f"{metrics['trades']} trades, prec={metrics['precision']}%, Sharpe={metrics['sharpe']}"
        if issues:
            detail += f" | {'; '.join(issues)}"

        report.add(f"Rolling {window}t", status, detail)


# ── Disk and system health ──────────────────────────────────────

def check_disk_space(report):
    """Check available disk space."""
    import shutil
    total, used, free = shutil.disk_usage(str(DATA_DIR))
    free_gb = free / (1024**3)
    if free_gb < 1:
        report.add("Disk space", "FAIL", f"Only {free_gb:.1f} GB free")
    elif free_gb < 5:
        report.add("Disk space", "WARN", f"{free_gb:.1f} GB free")
    else:
        report.add("Disk space", "PASS", f"{free_gb:.1f} GB free")


def check_backup(report):
    """Check if recent backup exists."""
    backup_dir = Path("backups")
    if not backup_dir.exists():
        report.add("Backup", "WARN", "No backups directory found")
        return

    backups = sorted(backup_dir.glob("signals_*.db"))
    if not backups:
        report.add("Backup", "WARN", "No database backups found")
        return

    latest = backups[-1]
    days_old = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).days
    if days_old > 14:
        report.add("Backup", "WARN", f"Last backup {days_old} days ago: {latest.name}")
    else:
        report.add("Backup", "PASS", f"Latest: {latest.name}")


# ── Main health check ───────────────────────────────────────────

def run_health(full=True):
    """Run all health checks. Returns HealthReport."""
    report = HealthReport()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    report.add("System time", "PASS", today)

    # Database
    check_db_integrity(report)
    check_db_size(report)

    # Data
    check_data_files(report)
    check_daily_update(report)

    # Signals
    check_recent_signals(report)

    # Rolling performance (only if we have trades)
    check_rolling_metrics(report)

    # System
    check_disk_space(report)
    check_backup(report)

    # Telegram (skip if not full check to avoid rate limits)
    if full:
        check_telegram(report)

    return report


def run_health_silent():
    """Run quick health check without Telegram. Returns True if healthy."""
    report = run_health(full=False)
    return report.worst_status in ("PASS", "WARN")


# ── Performance report (weekly/daily) ──────────────────────────

def weekly_performance_text():
    """Format a weekly performance summary for Telegram delivery."""
    lines = [
        "📊 WEEKLY PERFORMANCE REPORT",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # Current period metrics
    for window, label in [(30, "30 days"), (60, "60 days"), (999, "all time")]:
        metrics = compute_rolling_metrics(window=window)
        if "error" in metrics:
            lines.append(f"{label}: {metrics['error']}")
            continue
        lines.append(
            f"{label}: {metrics['trades']} trades, "
            f"{metrics['precision']:.1f}% precision, "
            f"Sharpe {metrics['sharpe']}, "
            f"PF {metrics['profit_factor']}"
        )

    # Open signals
    from track import get_open_signals
    open_sigs = get_open_signals()
    if open_sigs:
        lines.append("")
        lines.append(f"Open signals ({len(open_sigs)}):")
        for s in open_sigs:
            days = (datetime.now() - datetime.strptime(s["date"], "%Y-%m-%d")).days
            lines.append(f"  {s['ticker']} {s['direction']} | "
                         f"conf={s['confidence']} | {days}d open")

    return "\n".join(lines)


def monthly_review_text():
    """Format a monthly review with deeper analysis."""
    lines = [
        "📈 MONTHLY STRATEGY REVIEW",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        # Monthly breakdown
        rows = conn.execute(
            """SELECT strftime('%Y-%m', t.entry_date) as month,
                      COUNT(*) as trades,
                      SUM(CASE WHEN t.pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                      ROUND(AVG(t.pnl_pct), 2) as avg_pnl,
                      ROUND(SUM(t.pnl_pct), 2) as total_pnl
               FROM trades t
               JOIN signals s ON t.signal_id = s.id
               WHERE t.exit_reason IS NOT NULL
               AND t.exit_reason != 'still_open'
               GROUP BY month
               ORDER BY month DESC
               LIMIT 12"""
        ).fetchall()

        if rows:
            lines.append(f"{'Month':<10} {'Trades':<8} {'Wins':<6} "
                         f"{'Prec':<8} {'AvgRet':<10} {'TotalRet':<10}")
            lines.append("-" * 52)
            for r in rows:
                prec = r["wins"] / r["trades"] * 100 if r["trades"] > 0 else 0
                lines.append(
                    f"{r['month']:<10} {r['trades']:<8} {r['wins']:<6} "
                    f"{prec:<7.1f}% {r['avg_pnl']:<+9.2f}% {r['total_pnl']:<+9.2f}%"
                )

        # Best and worst stocks
        stock_rows = conn.execute(
            """SELECT s.ticker,
                      COUNT(*) as trades,
                      ROUND(AVG(t.pnl_pct), 2) as avg_pnl,
                      SUM(CASE WHEN t.pnl_pct > 0 THEN 1 ELSE 0 END) as wins
               FROM trades t
               JOIN signals s ON t.signal_id = s.id
               WHERE t.exit_reason IS NOT NULL
               AND t.exit_reason != 'still_open'
               GROUP BY s.ticker
               ORDER BY avg_pnl DESC"""
        ).fetchall()

        if stock_rows:
            lines.append("")
            lines.append("Stock performance:")
            for r in stock_rows:
                prec = r["wins"] / r["trades"] * 100 if r["trades"] > 0 else 0
                lines.append(f"  {r['ticker']:<6} {r['trades']:<4} trades, "
                             f"{prec:.0f}% precision, {r['avg_pnl']:+.2f}% avg")

    finally:
        conn.close()

    return "\n".join(lines)


# ── Signal outcome tracking automation ─────────────────────────

def auto_resolve_signals():
    """
    Check open signals against current prices.
    If stop or take-profit was hit, auto-close the trade.
    Returns list of resolved (signal_id, exit_reason, pnl).
    """
    from track import get_open_signals

    open_sigs = get_open_signals()
    if not open_sigs:
        return []

    resolved = []
    for sig in open_sigs:
        ticker = sig["ticker"]
        path = DATA_DIR / f"{ticker}.csv"
        if not path.exists():
            continue

        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.empty:
            continue

        current_price = df["close"].iloc[-1]
        entry_price = (sig["entry_low"] + sig["entry_high"]) / 2

        if sig["direction"] == "BUY":
            pnl = (current_price - entry_price) / entry_price
            hit_stop = current_price <= sig["stop_loss"]
            hit_tp = current_price >= sig["take_profit"]
        else:
            pnl = (entry_price - current_price) / entry_price
            hit_stop = current_price >= sig["stop_loss"]
            hit_tp = current_price <= sig["take_profit"]

        if hit_stop:
            reason = "stop_loss"
            exit_price = sig["stop_loss"]
        elif hit_tp:
            reason = "take_profit"
            exit_price = sig["take_profit"]
        else:
            continue

        # Record the trade in database
        trade_id = f"TRD-{datetime.now().strftime('%Y%m%d')}-{ticker}"
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute(
                """INSERT OR REPLACE INTO trades
                   (id, signal_id, ticker, direction, entry_date, entry_price,
                     exit_date, exit_price, exit_reason, pnl_pct, days_held)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade_id, sig["id"], ticker, sig["direction"],
                    sig["date"], entry_price,
                    datetime.now().strftime("%Y-%m-%d"), exit_price,
                    reason, round(pnl * 100, 2),
                    (datetime.now() - datetime.strptime(sig["date"], "%Y-%m-%d")).days,
                ),
            )
            conn.commit()
            resolved.append((sig["id"], reason, round(pnl * 100, 2)))
            log.info(f"Auto-resolved {sig['id']}: {reason} ({pnl*100:+.2f}%)")
        except Exception as e:
            log.error(f"Failed to resolve {sig['id']}: {e}")
        finally:
            conn.close()

    return resolved
