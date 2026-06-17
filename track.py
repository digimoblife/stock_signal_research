"""
track.py — stores signals and trade outcomes in SQLite.
The database is the SOURCE OF TRUTH. Telegram is just notification.
"""
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from settings import DB_PATH

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
"""


def connect():
    """Get SQLite connection. Creates DB + tables if needed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def save_signal(signal: dict) -> str:
    """Save a signal to the database. Returns signal ID."""
    sig_id = f"SIG-{signal['date'].replace('-', '')}-{uuid.uuid4().hex[:4].upper()}"

    conn = connect()
    try:
        conn.execute(
            """INSERT INTO signals
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
        log.info(f"Signal saved: {sig_id} {signal['ticker']} {signal['direction']}")
        return sig_id
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
