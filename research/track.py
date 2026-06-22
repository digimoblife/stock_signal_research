"""
track.py — stores signals and trade outcomes in SQLite.
The database is the SOURCE OF TRUTH. Telegram is just notification.
"""
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from settings import DB_PATH, PAPER_DB_PATH

log = logging.getLogger("track")

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id          TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,
    direction   TEXT NOT NULL CHECK(direction IN ('BUY','SELL','HOLD')),
    confidence  INTEGER DEFAULT 50,
    entry_low   REAL,
    entry_high  REAL,
    stop_loss   REAL,
    take_profit REAL,
    risk_reward REAL,
    strategy    TEXT,
    reasoning   TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    signal_id   TEXT,
    ticker      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    entry_date  TEXT,
    entry_price REAL,
    exit_date   TEXT,
    exit_price  REAL,
    exit_reason TEXT CHECK(exit_reason IN ('stop_loss','take_profit','manual','time_stop','still_open')),
    pnl_pct     REAL,
    days_held   INTEGER,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date);
CREATE INDEX IF NOT EXISTS idx_trades_signal ON trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades(entry_date);

CREATE TABLE IF NOT EXISTS paper_trades (
    id              TEXT PRIMARY KEY,
    strategy        TEXT NOT NULL DEFAULT 'T6_TREND_FILTERED',
    ticker          TEXT NOT NULL,
    signal_date     TEXT,
    entry_date      TEXT,
    entry_price     REAL,
    exit_date       TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    direction       TEXT DEFAULT 'BUY',
    shares          INTEGER,
    stop_loss       REAL,
    atr_at_entry    REAL,
    ma50_at_entry   REAL,
    vol_ratio_at    REAL,
    max_hold_end    TEXT,
    status          TEXT DEFAULT 'PAPER_OPEN',
    pnl_pct         REAL,
    pnl_absolute    REAL,
    days_held       INTEGER,
    high_water_mark REAL,
    created_at      TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_ticker ON paper_trades(ticker);
"""


def connect():
    """Get SQLite connection. Creates DB + tables if needed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # Migrate: add columns for holding period tracking
    _migrate(conn)
    return conn


def _migrate(conn):
    """Add columns for holding period analysis and deduplication (idempotent)."""
    migrations = [
        "ALTER TABLE trades ADD COLUMN source TEXT DEFAULT 'paper'",
        "ALTER TABLE trades ADD COLUMN strategy TEXT",
        "ALTER TABLE trades ADD COLUMN confidence INTEGER",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_ticker_date ON signals(ticker, date)",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()


def save_signal(signal: dict) -> str:
    """Save a signal to the database. Returns signal ID."""
    sig_id = f"SIG-{signal['date'].replace('-', '')}-{uuid.uuid4().hex[:4].upper()}"

    conn = connect()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO signals
               (id, ticker, date, direction, confidence,
                entry_low, entry_high, stop_loss, take_profit,
                risk_reward, strategy, reasoning)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sig_id, signal["ticker"], signal["date"], signal["direction"],
                signal["confidence"], signal["entry_low"], signal["entry_high"],
                signal["stop_loss"], signal["take_profit"], signal["risk_reward"],
                signal.get("strategy", ""), signal.get("reasoning", ""),
            ),
        )
        conn.commit()
        if cur.rowcount > 0:
            log.info(f"Signal saved: {sig_id} {signal['ticker']} {signal['direction']}")
            return sig_id
        else:
            log.info(f"Signal skipped (duplicate): {signal['ticker']} {signal['date']}")
            return ""
    except Exception as e:
        log.error(f"Failed to save signal: {e}")
        return ""
    finally:
        conn.close()


def save_trade(trade: dict) -> str:
    """Save a trade outcome."""
    trd_id = f"TRD-{uuid.uuid4().hex[:8].upper()}"

    conn = connect()
    try:
        conn.execute(
            """INSERT INTO trades
               (id, signal_id, ticker, direction, entry_date, entry_price,
                exit_date, exit_price, exit_reason, pnl_pct, days_held, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trd_id, trade.get("signal_id", ""), trade["ticker"],
                trade["direction"], trade["entry_date"], trade["entry_price"],
                trade.get("exit_date"), trade.get("exit_price"),
                trade.get("exit_reason", "still_open"),
                trade.get("pnl_pct"), trade.get("days_held"),
                trade.get("notes", ""),
            ),
        )
        conn.commit()
        return trd_id
    except Exception as e:
        log.error(f"Failed to save trade: {e}")
        return ""
    finally:
        conn.close()


def get_open_signals() -> list[dict]:
    """Get signals without a completed trade."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT s.* FROM signals s
               LEFT JOIN trades t ON s.id = t.signal_id
               WHERE t.id IS NULL
               ORDER BY s.date DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_signals() -> list[dict]:
    """Get all signals."""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY date DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_holding_stats(strategy: str, confidence: int,
                      min_samples: int = 30) -> dict | None:
    """
    Compute holding period statistics from similar historical trades.

    Two-tier matching:
      Tier 1: same strategy + confidence decile (n >= min_samples)
      Tier 2: same strategy, all confidences (n >= min_samples)
      Fallback: None if insufficient data.
    """
    import numpy as np

    conn = connect()
    try:
        def _stats_for_bucket(conf_lo, conf_hi):
            rows = conn.execute(
                """SELECT t.days_held, t.exit_reason, t.pnl_pct
                   FROM trades t
                   WHERE t.strategy = ?
                     AND t.source = 'backtest'
                     AND t.exit_reason IS NOT NULL
                     AND t.exit_reason != 'still_open'
                     AND t.days_held IS NOT NULL
                     AND t.confidence BETWEEN ? AND ?""",
                (strategy, conf_lo, conf_hi),
            ).fetchall()
            return [dict(r) for r in rows]

        decile = (confidence // 10) * 10
        tier1 = _stats_for_bucket(decile, decile + 9)
        if len(tier1) >= min_samples:
            trades = tier1
        else:
            tier2 = _stats_for_bucket(0, 100)
            if len(tier2) >= min_samples:
                trades = tier2
            else:
                return None

    finally:
        conn.close()

    days = np.array([t["days_held"] for t in trades if t["days_held"] is not None])
    if len(days) < min_samples:
        return None

    # Separate outcomes
    tp_days = np.array([
        t["days_held"] for t in trades
        if t["exit_reason"] == "take_profit" and t["days_held"] is not None
    ])
    stop_days = np.array([
        t["days_held"] for t in trades
        if t["exit_reason"] == "stop_loss" and t["days_held"] is not None
    ])
    expired = len([
        t for t in trades if t["exit_reason"] == "time_stop"
    ])
    total = len(trades)
    pnls = np.array([
        t["pnl_pct"] for t in trades if t["pnl_pct"] is not None
    ])

    result = {
        "sample_size": total,
        "resolution_p25": int(np.percentile(days, 25)),
        "resolution_p75": int(np.percentile(days, 75)),
        "tp_rate": round(len(tp_days) / total * 100, 1),
        "stop_rate": round(len(stop_days) / total * 100, 1),
        "expired_rate": round(expired / total * 100, 1),
        "avg_return": round(float(np.mean(pnls)), 2) if len(pnls) > 0 else 0.0,
        "win_rate": round(float(np.sum(pnls > 0) / len(pnls) * 100), 1) if len(pnls) > 0 else 0.0,
        "mean_days": round(float(np.mean(days)), 1),
    }

    if len(tp_days) >= 5:
        result["tp_median_days"] = int(np.median(tp_days))
    else:
        result["tp_median_days"] = None

    if len(stop_days) >= 5:
        result["stop_median_days"] = int(np.median(stop_days))
    else:
        result["stop_median_days"] = None

    return result


def get_performance() -> dict:
    """Compute basic performance metrics from closed trades."""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE exit_reason IS NOT NULL
               AND exit_reason != 'still_open'
               ORDER BY exit_date"""
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"trades": 0, "message": "No closed trades yet"}

    import numpy as np

    pnls = np.array([r["pnl_pct"] for r in rows if r["pnl_pct"] is not None])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    return {
        "trades": len(pnls),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if len(pnls) > 0 else 0,
        "avg_return": round(float(np.mean(pnls)), 2) if len(pnls) > 0 else 0,
        "total_return": round(float(np.sum(pnls)), 2) if len(pnls) > 0 else 0,
        "sharpe": round(float(np.mean(pnls) / np.std(pnls) * np.sqrt(252)), 2)
        if len(pnls) > 1 and np.std(pnls) > 0 else 0,
        "max_cons_losses": int(max(
            (pnls <= 0).astype(int).tolist(),
            default=0
        )),
    }


# ── Paper trade functions (separate DB) ─────────────────────────

def paper_connect():
    conn = sqlite3.connect(str(PAPER_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def save_paper_trade(trade: dict) -> str:
    trade_id = f"PT-{uuid.uuid4().hex[:8].upper()}"
    conn = paper_connect()
    try:
        conn.execute(
            """INSERT INTO paper_trades
               (id, strategy, ticker, signal_date, entry_date, entry_price,
                exit_date, exit_price, exit_reason, direction, shares,
                stop_loss, atr_at_entry, ma50_at_entry, vol_ratio_at,
                max_hold_end, status, pnl_pct, pnl_absolute, days_held,
                high_water_mark)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade_id, trade.get("strategy", "T6_TREND_FILTERED"),
                trade["ticker"], trade.get("signal_date"),
                trade.get("entry_date"), trade.get("entry_price"),
                trade.get("exit_date"), trade.get("exit_price"),
                trade.get("exit_reason"), trade.get("direction", "BUY"),
                trade.get("shares"),
                trade.get("stop_loss"), trade.get("atr_at_entry"),
                trade.get("ma50_at_entry"), trade.get("vol_ratio_at"),
                trade.get("max_hold_end"), trade.get("status", "PAPER_OPEN"),
                trade.get("pnl_pct"), trade.get("pnl_absolute"),
                trade.get("days_held"), trade.get("high_water_mark"),
            ),
        )
        conn.commit()
        return trade_id
    except Exception as e:
        log.error(f"Failed to save paper trade: {e}")
        return ""
    finally:
        conn.close()


def update_paper_trade(trade_id: str, updates: dict):
    allowed = {"entry_date", "entry_price", "exit_date", "exit_price",
               "exit_reason", "status", "pnl_pct", "pnl_absolute",
               "days_held", "high_water_mark", "shares"}
    sets = []
    vals = []
    for k, v in updates.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(trade_id)
    sql = f"UPDATE paper_trades SET {', '.join(sets)} WHERE id = ?"
    conn = paper_connect()
    try:
        conn.execute(sql, vals)
        conn.commit()
    except Exception as e:
        log.error(f"Failed to update paper trade {trade_id}: {e}")
    finally:
        conn.close()


def get_open_paper_trades(strategy="T6_TREND_FILTERED"):
    conn = paper_connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE strategy = ? AND status = 'PAPER_OPEN' ORDER BY entry_date",
            (strategy,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_closed_paper_trades(strategy="T6_TREND_FILTERED", limit=200):
    conn = paper_connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE strategy = ? AND status = 'PAPER_CLOSED' ORDER BY exit_date DESC LIMIT ?",
            (strategy, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_paper_trades(strategy="T6_TREND_FILTERED"):
    conn = paper_connect()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE strategy = ? ORDER BY created_at DESC",
            (strategy,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_paper_performance(strategy="T6_TREND_FILTERED"):
    closed = get_closed_paper_trades(strategy)
    open_ = get_open_paper_trades(strategy)

    import numpy as np

    result = {
        "strategy": strategy,
        "open_positions": len(open_),
        "closed_trades": len(closed),
        "total_signals": len(closed) + len(open_),
    }

    if closed:
        pnls = np.array([t["pnl_pct"] for t in closed if t["pnl_pct"] is not None])
        abs_pnls = np.array([t["pnl_absolute"] for t in closed if t["pnl_absolute"] is not None])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        days = [t["days_held"] for t in closed if t["days_held"] is not None]

        result.update({
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if len(pnls) > 0 else 0,
            "avg_return": round(float(np.mean(pnls)), 2) if len(pnls) > 0 else 0,
            "total_return_pct": round(float(np.sum(pnls)), 2) if len(pnls) > 0 else 0,
            "total_return_abs": round(float(np.sum(abs_pnls)), 0) if len(abs_pnls) > 0 else 0,
            "profit_factor": (
                round(float(np.sum(wins) / abs(np.sum(losses))), 2)
                if len(losses) > 0 and np.sum(losses) != 0
                else float("inf") if len(wins) > 0 else 0
            ),
            "avg_trade": round(float(np.mean(pnls)), 2) if len(pnls) > 0 else 0,
            "max_drawdown": _compute_paper_drawdown(closed),
            "avg_days_held": round(float(np.mean(days)), 1) if days else 0,
        })

    if open_:
        result["open_tickers"] = [t["ticker"] for t in open_]
        result["open_entry_dates"] = [t.get("entry_date", "?") for t in open_]

    return result


def _compute_paper_drawdown(closed_trades):
    """Estimate max drawdown from closed trade P&L sequence."""
    import numpy as np
    if not closed_trades:
        return 0
    sorted_trades = sorted(closed_trades, key=lambda t: t.get("exit_date") or "")
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in sorted_trades:
        pnl = t.get("pnl_pct") or 0
        cumulative += pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
    return round(max_dd, 2)
