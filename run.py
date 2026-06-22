"""
run.py — orchestrates the daily research pipeline.

Usage:
  python run.py research        # run backtest, rank strategies
  python run.py daily           # full daily cycle: fetch → signal → telegram
  python run.py daily --paper   # daily cycle + T6 paper tracking
  python run.py signal          # generate signals, print to console
  python run.py paper           # T6 paper cycle: check exits + enter signals
  python run.py paper-status    # show open paper positions + performance
  python run.py weekly          # T6 paper weekly report
  python run.py performance     # print performance report
  python run.py open            # show open signals
  python run.py test            # test Telegram connection
  python run.py init            # initialize database
  python run.py fetch           # fetch all data (alias for daily data step)
  python run.py health          # run health checks
  python run.py export          # export signals.db to CSV files
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

    # Batch guard
    if ONE_DAILY_BATCH:
        conn = connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE date = ?", (today,)
        ).fetchone()[0]
        conn.close()
        if count > 0:
            log.info("Daily batch already generated today.")
            log.info("No new signals generated.")
            # Still run paper if --paper flag
            if "--paper" in sys.argv:
                _run_paper_cycle(today)
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
    else:
        # 3. Save and send each signal
        for sig in signals:
            signal_id = save_signal(sig)
            if signal_id:
                ok = send_signal(sig, signal_id)
                if ok:
                    log.info(f"Sent: {signal_id}")
                else:
                    log.error(f"Telegram failed for {signal_id}")

    # 4. Paper tracking (if --paper flag)
    if "--paper" in sys.argv:
        _run_paper_cycle(today)

    log.info(f"Done: {len(signals)} signals sent")


def cmd_paper():
    """Run T6 paper trading cycle only."""
    today = datetime.now().strftime("%Y-%m-%d")
    log.info(f"=== T6 Paper cycle: {today} ===")

    from settings import T6_ENABLED
    if not T6_ENABLED:
        log.info("T6 paper trading is disabled.")
        return

    _run_paper_cycle(today)


def _run_paper_cycle(today: str):
    """Shared paper cycle logic."""
    from paper import daily_paper_cycle, get_open_summary
    from telegram_sender import send_t6_signal, send

    events = daily_paper_cycle(today)

    entries = [e for e in events if e["type"] == "entry"]
    exits = [e for e in events if e["type"] == "exit"]

    if entries:
        log.info(f"New T6 signals: {len(entries)}")
        for e in entries:
            send_t6_signal(e["signal"])

    if exits:
        log.info(f"T6 exits: {len(exits)}")
        lines = [f"📋 T6 Paper — {today}", f""]
        for e in exits:
            emoji = "📉" if e.get("pnl_pct", 0) < 0 else "📗"
            lines.append(f"{emoji} {e['ticker']} {e['reason']}  P&L={e['pnl_pct']:+.2f}%")
        send("\n".join(lines))

    if not entries and not exits:
        log.info("No paper events today.")
        summary = get_open_summary()
        if "No open" not in summary:
            send(f"📋 T6 Paper — {today}\n\n{summary}")


def cmd_paper_status():
    """Show open paper positions and performance."""
    from paper import get_open_summary, format_paper_performance
    print()
    print(format_paper_performance())
    print()
    print(get_open_summary())


def cmd_weekly():
    """Weekly T6 paper performance report."""
    from paper import format_paper_performance, compare_with_backtest
    from telegram_sender import send

    today = datetime.now().strftime("%Y-%m-%d")
    report = f"📊 Weekly T6 Report — {today}\n\n"
    report += format_paper_performance()
    report += f"\n\n"
    report += compare_with_backtest()

    print(report)
    send(report)


def cmd_performance():
    """Print performance report."""
    from track import get_performance
    from telegram_sender import format_performance

    perf = get_performance()
    print(format_performance(perf))

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
        "paper": cmd_paper,
        "paper-status": cmd_paper_status,
        "weekly": cmd_weekly,
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
