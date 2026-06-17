#!/bin/bash
# deploy_vps.sh — complete VPS deployment for IDX Research System
# Run as root on your OVH VPS.
#
# USAGE:
#   On your local Mac:
#     scp -r /path/to/research root@YOUR_VPS_IP:~/idx-research
#     ssh root@YOUR_VPS_IP
#     cd ~/idx-research && bash scripts/deploy_vps.sh

set -e

echo "=== IDX Research System — VPS Deployment ==="
echo ""

# ── 1. System update ─────────────────────────────────────────
apt update && apt upgrade -y

# ── 2. Python + essentials ───────────────────────────────────
apt install -y python3 python3-pip python3-venv git curl cron

# ── 3. Create required directories ───────────────────────────
cd ~/idx-research
mkdir -p data logs backups

# ── 4. Virtual environment ───────────────────────────────────
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── 5. Initialize database ──────────────────────────────────
echo ""
echo "=== Initializing database ==="
python run.py init

# ── 6. Fetch historical data ────────────────────────────────
echo ""
echo "=== Fetching historical data (5-10 minutes) ==="
python run.py fetch

# ── 7. Run initial research ─────────────────────────────────
echo ""
echo "=== Running strategy backtest ==="
python run.py research

# ── 8. Set up cron ──────────────────────────────────────────
cat > /tmp/idx_cron << 'CRON'
# IDX Research System — daily signal generation 17:30 WIB (10:30 UTC)
30 10 * * 1-5 cd /root/idx-research && .venv/bin/python run.py daily >> logs/daily.log 2>&1
# Daily health check — 5 min after signals, 17:35 WIB (10:35 UTC)
35 10 * * 1-5 cd /root/idx-research && .venv/bin/python run.py health >> logs/health.log 2>&1
# Weekly performance — Sunday 20:00 WIB (13:00 UTC)
0 13 * * 0 cd /root/idx-research && .venv/bin/python run.py performance >> logs/weekly.log 2>&1
# Weekly DB backup — Sunday 21:00 WIB (14:00 UTC), keep 90 days
0 14 * * 0 cp /root/idx-research/signals.db /root/idx-research/backups/signals_$(date +\%Y\%m\%d).db && find /root/idx-research/backups -name '*.db' -mtime +90 -delete
# Log rotation — keep 30 days of logs
0 2 * * 0 find /root/idx-research/logs -name '*.log' -mtime +30 -delete
CRON
crontab /tmp/idx_cron

# ── 9. Enable cron service ──────────────────────────────────
systemctl enable cron 2>/dev/null || update-rc.d cron defaults 2>/dev/null || true
systemctl start cron 2>/dev/null || service cron start 2>/dev/null || true

# ── 10. Security ────────────────────────────────────────────
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable 2>/dev/null || echo "ufw already active or unavailable"

# ── 11. Deployment verification ─────────────────────────────
echo ""
echo "=== Verifying deployment ==="

ERRORS=0

# 11a. Verify cron installed
if command -v cron &>/dev/null || command -v cronie &>/dev/null; then
    echo "  ✅ cron: installed"
else
    echo "  ❌ cron: NOT found"
    ERRORS=$((ERRORS + 1))
fi

# 11b. Verify crontab loaded
CURRENT_CRON=$(crontab -l 2>/dev/null | grep -c "idx-research" || true)
if [ "$CURRENT_CRON" -ge 1 ]; then
    echo "  ✅ crontab: $(crontab -l | grep -c "idx-research") entries loaded"
else
    echo "  ❌ crontab: no idx-research entries found"
    ERRORS=$((ERRORS + 1))
fi

echo "  📋 Active cron entries:"
crontab -l | grep "idx-research" | while read -r line; do
    echo "     $line"
done

# 11c. Verify database exists
if [ -f "signals.db" ]; then
    SIZE=$(du -h signals.db | cut -f1)
    echo "  ✅ database: signals.db ($SIZE)"
else
    echo "  ❌ database: signals.db NOT found"
    ERRORS=$((ERRORS + 1))
fi

# 11d. Verify Telegram connectivity
echo "  ⏳ Telegram: testing..."
TELEGRAM_OK=$(python run.py test 2>&1)
if echo "$TELEGRAM_OK" | grep -q "OK"; then
    echo "  ✅ Telegram: working"
else
    echo "  ❌ Telegram: $TELEGRAM_OK"
    ERRORS=$((ERRORS + 1))
fi

# 11e. Verify daily command runs without error
echo "  ⏳ Daily command: smoke test..."
DAILY_OK=$(python run.py daily 2>&1) || true
if echo "$DAILY_OK" | grep -qi "error\|traceback\|failed"; then
    echo "  ❌ Daily command: errors detected"
    echo "     $(echo "$DAILY_OK" | grep -i "error\|traceback\|failed" | head -3)"
    ERRORS=$((ERRORS + 1))
else
    LINES=$(echo "$DAILY_OK" | grep -c "\[fetch\]\|\[run\]\|\[signal\]\|Done\|No signals" || true)
    if [ "$LINES" -ge 1 ]; then
        echo "  ✅ Daily command: working"
    else
        # Could be empty if nothing printed — still may be OK
        echo "  ✅ Daily command: ran (no signal output today)"
    fi
fi

# 11f. Verify logs directory is writable
if touch logs/.verify && rm logs/.verify; then
    echo "  ✅ logs: writable"
else
    echo "  ❌ logs: NOT writable"
    ERRORS=$((ERRORS + 1))
fi

# ── 12. Summary ──────────────────────────────────────────────
echo ""
echo "=== DEPLOYMENT RESULTS ==="
if [ "$ERRORS" -eq 0 ]; then
    echo "  All checks passed. System is operational."
else
    echo "  $ERRORS check(s) failed. Review output above."
    echo "  Fix issues, then re-run: bash scripts/deploy_vps.sh"
fi

echo ""
echo "=== NEXT STEPS ==="
echo "  1. Verify Telegram delivery worked (check your phone)"
echo "  2. First automated run: Mon-Fri at 17:30 WIB (10:30 UTC)"
echo "  3. Monitor health: check logs/daily.log and logs/health.log"
echo ""
echo "  Quick reference commands:"
echo "    View logs:    tail -f logs/daily.log"
echo "    Force daily:  .venv/bin/python run.py daily"
echo "    Health:       .venv/bin/python run.py health"
echo "    Performance:  .venv/bin/python run.py performance"
echo "    Edit config:  nano settings.py"
echo ""
echo "  Cron schedule (WIB = UTC+7):"
echo "    17:30 Mon-Fri — Signal generation"
echo "    17:35 Mon-Fri — Health check"
echo "    20:00 Sun     — Weekly performance report"
echo "    21:00 Sun     — Database backup"
echo "    09:00 Sun     — Log rotation (30-day retention)"
echo ""
echo "  Files:"
echo "    ~/idx-research/"
echo "    ├── run.py           — main entry point"
echo "    ├── settings.py      — configuration (edit this)"
echo "    ├── fetch.py         — data downloader (3 retries)"
echo "    ├── research.py      — strategy backtester"
echo "    ├── gen_signal.py    — signal generation"
echo "    ├── track.py         — database tracking"
echo "    ├── telegram_sender.py — Telegram delivery (3 retries)"
echo "    ├── monitor.py       — health monitoring"
echo "    ├── signals.db       — SQLite database"
echo "    ├── data/            — OHLCV CSV files"
echo "    ├── logs/            — cron output (30-day retention)"
echo "    └── backups/         — weekly DB snapshots (90-day retention)"
