"""
run.py — orchestrates the daily research pipeline.

Usage:
  python run.py research    # run backtest, rank strategies
  python run.py daily       # full daily cycle: fetch → signal → telegram
  python run.py signal      # generate signals, print to console
  python run.py performance # print performance report
  python run.py open        # show open signals
  python run.py test        # test Telegram connection
  python run.py health      # run health checks
  python run.py init        # initialize database
  python run.py fetch       # fetch all data (alias for daily data step)
  python run.py export      # export signals.db to CSV files
"""
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")


def cmd_research():
    """Run strategy backtest and ranking."""
    from research import run
    run()


def cmd_signal():
    """Generate signals. Print to console."""
    from gen_signal import generate_signals
    signals = generate_signals()
    if signals:
        for s in signals:
            print(f"{s['direction']:>4} {s['ticker']:<6}  "
                  f"conf={s['confidence']}  "
                  f"entry={s['entry_low']:>8,.0f}–{s['entry_high']:>8,.0f}  "
                  f"stop={s['stop_loss']:>8,.0f}  "
                  f"tp={s['take_profit']:>8,.0f}  "
                  f"rr={s['risk_reward']:.1f}")
            print(f"     {s['reasoning']}")
    else:
        print("No signals generated today.")


def cmd_daily():
    """Full daily cycle: fetch new data, generate signals, send to Telegram."""
    from track import connect, save_signal
    from settings import ONE_DAILY_BATCH

    today = datetime.now().strftime("%Y-%m-%d")
    log.info(f"=== Daily run: {today} ===")

    # Batch guard: only one batch per calendar day
    if ONE_DAILY_BATCH:
        conn = connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE date = ?", (today,)
        ).fetchone()[0]
        conn.close()
        if count > 0:
            log.info("Daily batch already generated today.")
            log.info("No new signals generated.")
            return

    from fetch import fetch_all
    from gen_signal import generate_signals
    from telegram_sender import send_signal

    # 1. Update data
    log.info("Fetching data...")
    ok, failed = fetch_all()
    log.info(f"Data: {ok} OK, {failed} failed")

    # 2. Generate signals
    log.info("Generating signals...")
    signals = generate_signals()
    if not signals:
        msg = "No signals generated today."
        log.info(msg)
        from telegram_sender import send
        send(f"🤖 IDX Research — {today}\n\n{msg}")
        return

    # 3. Save and send each signal
    for sig in signals:
        signal_id = save_signal(sig)
        if signal_id:
            ok = send_signal(sig, signal_id)
            if ok:
                log.info(f"Sent: {signal_id}")
            else:
                log.error(f"Telegram failed for {signal_id}")

    log.info(f"Done: {len(signals)} signals sent")


def cmd_performance():
    """Print performance report."""
    from track import get_performance
    from telegram_sender import format_performance

    perf = get_performance()
    print(format_performance(perf))

    # Also send to Telegram
    from telegram_sender import send
    send(f"📊 Performance ({datetime.now().strftime('%Y-%m-%d')})\n\n"
         + format_performance(perf))


def cmd_open():
    """Show signals that haven't been resolved yet."""
    from track import get_open_signals

    signals = get_open_signals()
    if not signals:
        print("No open signals.")
        return

    print(f"\nOpen signals ({len(signals)}):")
    print("-" * 50)
    for s in signals:
        from datetime import datetime
        days_open = (datetime.now() - datetime.strptime(s["date"], "%Y-%m-%d")).days
        print(f"{s['id']}  {s['ticker']} {s['direction']}  "
              f"conf={s['confidence']}  {days_open}d open  "
              f"entry={s['entry_low']:,.0f}–{s['entry_high']:,.0f}")


def cmd_test():
    """Test Telegram connection."""
    from telegram_sender import send_test
    ok = send_test()
    print("Telegram OK." if ok else "Telegram FAILED.")


def cmd_init():
    """Initialize SQLite database and create tables."""
    from track import connect
    conn = connect()
    conn.close()
    print("Database initialized.")


def cmd_fetch():
    """Fetch/update all ticker data."""
    from fetch import fetch_all
    ok, failed = fetch_all()
    print(f"Fetch complete: {ok} OK, {failed} failed")


def cmd_health():
    """Run health checks and report to Telegram."""
    from monitor import run_health
    report = run_health(full=True)
    print(report.text())

    # Send to Telegram for remote visibility
    from telegram_sender import send
    send(report.text("🩺 Daily Health Check"))


def cmd_export():
    """Export signals.db to CSV files for spreadsheet analysis."""
    from export import run
    run()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    commands = {
        "research": cmd_research,
        "signal": cmd_signal,
        "daily": cmd_daily,
        "performance": cmd_performance,
        "open": cmd_open,
        "test": cmd_test,
        "init": cmd_init,
        "fetch": cmd_fetch,
        "health": cmd_health,
        "export": cmd_export,
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
