"""
telegram.py — delivers signals and reports via Telegram bot.
Simple polling bot. No webhooks needed for personal use.
"""
import logging
import asyncio
from datetime import datetime

from settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger("telegram")

try:
    from telegram import Bot
    from telegram.error import TelegramError as TGError
except ImportError:
    Bot = None
    TGError = Exception

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # seconds


def _get_bot() -> Bot:
    if Bot is None:
        raise ImportError("pip install python-telegram-bot")
    if TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        raise ValueError("Set TELEGRAM_BOT_TOKEN in settings.py")
    return Bot(token=TELEGRAM_BOT_TOKEN)


async def _send(text: str, attempt: int = 1) -> bool:
    """Async send with retry. Returns True on success."""
    try:
        bot = _get_bot()
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e} (attempt {attempt}/{MAX_RETRIES})")
        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            log.info(f"Retrying in {delay}s...")
            await asyncio.sleep(delay)
            return await _send(text, attempt + 1)
        return False


def send(text: str) -> bool:
    """Synchronous send (blocking). Use this for one-off messages."""
    try:
        return asyncio.run(_send(text))
    except RuntimeError:
        # Event loop already running (e.g., in Jupyter)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_send(text))
        finally:
            loop.close()


def format_signal(s: dict, signal_id: str = "") -> str:
    """Format a signal for Telegram. Designed for quick scanning."""
    emoji = "📈" if s["direction"] == "BUY" else "📉"
    risk_pct = abs(s["entry_high"] - s["stop_loss"]) / s["entry_high"] * 100

    lines = [
        f"{emoji} {s['direction']} ${s['ticker']}  |  Confidence: {s['confidence']}/100",
        f"",
        f"Entry: {s['entry_low']:,.0f} – {s['entry_high']:,.0f}",
        f"Stop:  {s['stop_loss']:,.0f}  ({risk_pct:.1f}%)",
        f"TP:    {s['take_profit']:,.0f}  |  R:R: {s['risk_reward']:.1f}",
        f"",
        f"{s['reasoning']}",
    ]

    # AI explanation
    ai = s.get("ai_explanation", "")
    if ai:
        lines.append(f"")
        lines.append(ai)

    # Market context
    regime = s.get("regime", "")
    liq = s.get("liquidity", "")
    if regime or liq:
        lines.append(f"")
        parts = []
        if regime:
            parts.append(f"Market: {regime}")
        if liq:
            parts.append(f"Cap: {liq}")
        lines.append("  |  ".join(parts))

    # Holding period statistics
    stats = s.get("holding_stats")
    if stats:
        lines.append(f"")
        p25 = stats["resolution_p25"]
        p75 = stats["resolution_p75"]
        lines.append(f"Expected Resolution Window:")
        lines.append(f"{p25} — {p75} trading days")
        lines.append(f"(P25–P75 of {stats['sample_size']} similar trades)")
        lines.append(f"")
        lines.append(f"Historical Outcomes:")

        tp_line = f"  TP hit:   {stats['tp_rate']}%"
        if stats.get("tp_median_days") is not None:
            tp_line += f"  (median {stats['tp_median_days']} days)"
        lines.append(tp_line)

        stop_line = f"  Stop hit: {stats['stop_rate']}%"
        if stats.get("stop_median_days") is not None:
            stop_line += f"  (median {stats['stop_median_days']} days)"
        lines.append(stop_line)

        lines.append(f"  Expired:  {stats['expired_rate']}%  (at 5-day limit)")

    if signal_id:
        lines.append(f"")
        lines.append(f"ID: {signal_id}")

    return "\n".join(lines)


def format_performance(perf: dict) -> str:
    """Format performance summary."""
    if perf.get("trades", 0) == 0:
        return "No closed trades yet."

    return (
        f"📊 Performance Report\n\n"
        f"Trades: {perf['trades']}  (W: {perf['wins']} / L: {perf['losses']})\n"
        f"Win Rate: {perf['win_rate']}%\n"
        f"Avg Return: {perf['avg_return']:+.2f}%\n"
        f"Total Return: {perf['total_return']:+.2f}%\n"
        f"Sharpe: {perf['sharpe']}\n"
        f"Max Cons Losses: {perf['max_cons_losses']}"
    )


def send_signal(signal: dict, signal_id: str) -> bool:
    """Format and send one signal."""
    msg = format_signal(signal, signal_id)
    return send(msg)


def send_test() -> bool:
    """Send a test message to verify connectivity."""
    return send(
        f"🤖 IDX Research System — ONLINE\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        ok = send_test()
        print("Test message sent." if ok else "FAILED.")
    else:
        print("Usage: python telegram.py test")
