"""
bot.py — Complete Interactive Telegram Command Listener Bot for IDX Stock Signal System.

Available Commands:
  /start, /help   - Show available commands
  /trade, /daily  - Trigger full daily analysis cycle (fetch, signals, paper trades)
  /signal         - Generate signals on-demand and report to chat
  /paper          - Run paper trading exit & entry check
  /status         - Show open paper positions & performance report
  /weekly         - Generate weekly T6 report and backtest comparison
  /performance    - Display overall historical signal performance
  /open           - Show open/unresolved signals
  /fetch          - Download/update latest market stock data
  /health         - Run comprehensive system health check
"""
import sys
import logging
import asyncio
import functools
from datetime import datetime

from settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("telegram_bot")

try:
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
    )
except ImportError:
    log.error("python-telegram-bot is required. Install it using: pip install python-telegram-bot")
    sys.exit(1)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:
    AsyncIOScheduler = None
    CronTrigger = None



def restricted(func):
    """Decorator to enforce TELEGRAM_CHAT_ID authorization check."""
    @functools.wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_chat or not update.effective_user:
            return
        
        user_id = str(update.effective_user.id)
        chat_id = str(update.effective_chat.id)
        allowed_id = str(TELEGRAM_CHAT_ID).strip()

        if allowed_id and chat_id != allowed_id and user_id != allowed_id:
            log.warning(f"Unauthorized access attempt: user_id={user_id}, chat_id={chat_id}")
            await update.message.reply_text(
                "⛔ *Unauthorized Access*\n"
                "You do not have permission to execute commands on this bot.",
                parse_mode="Markdown"
            )
            return
        return await func(update, context, *args, **kwargs)
    return wrapped


@restricted
async def cmd_start_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start and /help commands."""
    help_text = (
        "🤖 *IDX Stock Signal Bot — Command Menu*\n\n"
        "📈 *Trading & Signals*\n"
        "• `/trade` or `/daily` — Full daily cycle (fetch, signal, paper trades)\n"
        "• `/signal` — Generate & print signals on-demand\n"
        "• `/open` — View active, unresolved signals\n\n"
        "📋 *Paper Trading & Reports*\n"
        "• `/paper` — Run paper trading exit & entry scan\n"
        "• `/status` — Open paper positions & performance summary\n"
        "• `/weekly` — Weekly paper report & backtest comparison\n"
        "• `/performance` — Overall historical signal win rate & stats\n\n"
        "⚙️ *System & Diagnostics*\n"
        "• `/fetch` — Force download latest stock market data\n"
        "• `/health` — Run full system & database health check\n"
        "• `/help` — Display this command menu\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


@restricted
async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /trade and /daily commands."""
    await update.message.reply_text("⏳ *Starting full daily market analysis...*\nFetching market data, evaluating strategies, and updating paper trades.", parse_mode="Markdown")
    
    def run_daily_job():
        if "--paper" not in sys.argv:
            sys.argv.append("--paper")
        from run import cmd_daily
        cmd_daily()

    try:
        await asyncio.to_thread(run_daily_job)
        await update.message.reply_text("✅ *Daily analysis complete!* Signals and reports have been generated.", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error executing daily job: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error executing analysis:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_signal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /signal command."""
    await update.message.reply_text("⏳ *Generating stock signals on demand...*", parse_mode="Markdown")

    def run_signal_job():
        from gen_signal import generate_signals
        return generate_signals()

    try:
        signals = await asyncio.to_thread(run_signal_job)
        if not signals:
            await update.message.reply_text("ℹ️ No signals generated today.")
            return

        lines = [f"📊 *Generated Signals ({datetime.now().strftime('%Y-%m-%d')})*", ""]
        for s in signals:
            tp = f"{s['take_profit']:,.0f}" if s.get('take_profit') else "None"
            lines.append(
                f"*{s['direction']} {s['ticker']}* | Conf: `{s['confidence']}/100`\n"
                f"Entry: `{s['entry_low']:,.0f} – {s['entry_high']:,.0f}`\n"
                f"Stop: `{s['stop_loss']:,.0f}` | TP: `{tp}` | R:R: `{s['risk_reward']:.1f}`\n"
                f"Reason: _{s['reasoning']}_\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error in signal handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error generating signals:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_paper_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /paper command."""
    await update.message.reply_text("⏳ *Running paper trading scan...*", parse_mode="Markdown")

    def run_paper_job():
        from run import cmd_paper
        cmd_paper()

    try:
        await asyncio.to_thread(run_paper_job)
        await update.message.reply_text("✅ *Paper trading cycle completed.*", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error in paper handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error running paper trade cycle:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /status command."""
    def run_status_job():
        from paper import get_open_summary, format_paper_performance
        perf = format_paper_performance()
        summary = get_open_summary()
        return perf, summary

    try:
        perf, summary = await asyncio.to_thread(run_status_job)
        msg = f"📊 *T6 Paper Trading Performance*\n```\n{perf}\n```\n\n📋 *Open Positions*\n```\n{summary}\n```"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error fetching status: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error getting status:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_weekly_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /weekly command."""
    await update.message.reply_text("⏳ *Generating weekly paper trading report...*", parse_mode="Markdown")

    def run_weekly_job():
        from run import cmd_weekly
        cmd_weekly()

    try:
        await asyncio.to_thread(run_weekly_job)
        await update.message.reply_text("✅ *Weekly report sent to chat.*", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error generating weekly report: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error generating report:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_performance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /performance command."""
    def run_performance_job():
        from track import get_performance
        from telegram_sender import format_performance
        perf = get_performance()
        return format_performance(perf)

    try:
        text = await asyncio.to_thread(run_performance_job)
        await update.message.reply_text(f"📊 *Historical Signals Performance*\n\n```\n{text}\n```", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error fetching performance: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error getting performance:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_open_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /open command."""
    def run_open_job():
        from track import get_open_signals
        return get_open_signals()

    try:
        signals = await asyncio.to_thread(run_open_job)
        if not signals:
            await update.message.reply_text("ℹ️ No open/unresolved signals.")
            return

        lines = [f"📂 *Open Signals ({len(signals)})*", ""]
        for s in signals:
            days_open = (datetime.now() - datetime.strptime(s["date"], "%Y-%m-%d")).days
            lines.append(
                f"• *{s['ticker']} {s['direction']}* | Conf: `{s['confidence']}` | `{days_open}d open`\n"
                f"  Entry: `{s['entry_low']:,.0f}–{s['entry_high']:,.0f}` | Stop: `{s['stop_loss']:,.0f}`\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error fetching open signals: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error getting open signals:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_fetch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /fetch command."""
    await update.message.reply_text("⏳ *Fetching latest stock market data from Yahoo Finance...*", parse_mode="Markdown")

    def run_fetch_job():
        from fetch import fetch_all
        return fetch_all()

    try:
        ok, failed = await asyncio.to_thread(run_fetch_job)
        await update.message.reply_text(f"✅ *Data Fetch Completed!*\n• Success: `{ok}` tickers\n• Failed: `{failed}` tickers", parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error fetching data: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Error fetching data:* `{e}`", parse_mode="Markdown")


@restricted
async def cmd_health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /health command."""
    await update.message.reply_text("⏳ *Running system health diagnostic...*", parse_mode="Markdown")

    def run_health_job():
        from monitor import run_health
        report = run_health(full=True)
        return report.text("🩺 Daily Health Check")

    try:
        report_text = await asyncio.to_thread(run_health_job)
        await update.message.reply_text(report_text)
    except Exception as e:
        log.error(f"Error in health handler: {e}", exc_info=True)
        await update.message.reply_text(f"❌ *Health check failed:* `{e}`", parse_mode="Markdown")


def scheduled_daily_job():
    log.info("Starting automated daily scan job (Mon-Fri 17:30 WIB)...")
    if "--paper" not in sys.argv:
        sys.argv.append("--paper")
    from run import cmd_daily
    cmd_daily()


async def post_init_scheduler(application):
    """Start APScheduler inside the running asyncio event loop."""
    if AsyncIOScheduler:
        scheduler = AsyncIOScheduler(timezone="Asia/Jakarta")
        scheduler.add_job(
            lambda: asyncio.to_thread(scheduled_daily_job),
            CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone="Asia/Jakarta")
        )
        scheduler.start()
        log.info("Automated background scheduler active: Mon-Fri at 17:30 WIB.")
    else:
        log.warning("APScheduler not installed. Automatic 17:30 WIB background scans disabled.")


def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.error("TELEGRAM_BOT_TOKEN is not set in environment or .env file.")
        sys.exit(1)

    log.info("Initializing Telegram Command Listener Bot...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init_scheduler).build()

    # Register command handlers
    app.add_handler(CommandHandler(["start", "help"], cmd_start_help))
    app.add_handler(CommandHandler(["trade", "daily"], cmd_trade))
    app.add_handler(CommandHandler("signal", cmd_signal_handler))
    app.add_handler(CommandHandler("paper", cmd_paper_handler))
    app.add_handler(CommandHandler("status", cmd_status_handler))
    app.add_handler(CommandHandler("weekly", cmd_weekly_handler))
    app.add_handler(CommandHandler(["performance", "perf"], cmd_performance_handler))
    app.add_handler(CommandHandler("open", cmd_open_handler))
    app.add_handler(CommandHandler("fetch", cmd_fetch_handler))
    app.add_handler(CommandHandler("health", cmd_health_handler))

    log.info("Bot listener running. Listening for Telegram chat commands...")
    app.run_polling()


if __name__ == "__main__":
    main()
