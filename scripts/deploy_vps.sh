#!/bin/bash
# deploy_vps.sh — complete VPS deployment for IDX Research System & Telegram Command Bot
# Run as root on your VPS.

set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== IDX Research System — VPS Deployment ==="
echo "Target Directory: ${APP_DIR}"
echo ""

# ── 1. System update ─────────────────────────────────────────
apt update && apt upgrade -y

# ── 2. Python + essentials ───────────────────────────────────
apt install -y python3 python3-pip python3-venv git curl cron ufw

# ── 3. Create required directories ───────────────────────────
cd "${APP_DIR}"
mkdir -p data logs backups

# ── 4. Virtual environment ───────────────────────────────────
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ── 5. Initialize database ──────────────────────────────────
echo ""
echo "=== Initializing database ==="
python run.py init

# ── 6. Fetch historical data ────────────────────────────────
echo ""
echo "=== Fetching historical data ==="
python run.py fetch

# ── 7. Run initial research ─────────────────────────────────
echo ""
echo "=== Running strategy backtest ==="
python run.py research

# ── 8. Set up cron for daily automated scans ────────────────
cat > /tmp/idx_cron << CRON
# IDX Research System — daily signal generation 17:30 WIB (10:30 UTC)
30 10 * * 1-5 cd ${APP_DIR} && .venv/bin/python run.py daily --paper >> logs/daily.log 2>&1
# Daily health check — 5 min after signals, 17:35 WIB (10:35 UTC)
35 10 * * 1-5 cd ${APP_DIR} && .venv/bin/python run.py health >> logs/health.log 2>&1
# Weekly performance — Sunday 20:00 WIB (13:00 UTC)
0 13 * * 0 cd ${APP_DIR} && .venv/bin/python run.py weekly >> logs/weekly.log 2>&1
# Weekly DB backup — Sunday 21:00 WIB (14:00 UTC), keep 90 days
0 14 * * 0 cp ${APP_DIR}/signals.db ${APP_DIR}/backups/signals_\$(date +\%Y\%m\%d).db && find ${APP_DIR}/backups -name '*.db' -mtime +90 -delete
# Log rotation — keep 30 days of logs
0 2 * * 0 find ${APP_DIR}/logs -name '*.log' -mtime +30 -delete
CRON
crontab /tmp/idx_cron

# ── 9. Enable cron service ──────────────────────────────────
systemctl enable cron 2>/dev/null || update-rc.d cron defaults 2>/dev/null || true
systemctl start cron 2>/dev/null || service cron start 2>/dev/null || true

# ── 10. Register Telegram Bot listener (stock-bot.service) ───
echo ""
echo "=== Setting up Telegram Command Bot Service ==="
cat << EOF > /etc/systemd/system/stock-bot.service
[Unit]
Description=IDX Stock Signal Telegram Command Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stock-bot.service
systemctl restart stock-bot.service

# ── 11. Security ────────────────────────────────────────────
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable 2>/dev/null || echo "ufw already active or unavailable"

# ── 12. Deployment verification ─────────────────────────────
echo ""
echo "=== Verifying deployment ==="

ERRORS=0

# 12a. Verify cron installed
if command -v cron &>/dev/null || command -v cronie &>/dev/null; then
    echo "  ✅ cron: installed"
else
    echo "  ❌ cron: NOT found"
    ERRORS=$((ERRORS + 1))
fi

# 12b. Verify crontab loaded
CURRENT_CRON=$(crontab -l 2>/dev/null | grep -c "${APP_DIR}" || true)
if [ "$CURRENT_CRON" -ge 1 ]; then
    echo "  ✅ crontab: loaded"
else
    echo "  ❌ crontab: no entries found"
    ERRORS=$((ERRORS + 1))
fi

# 12c. Verify database exists
if [ -f "signals.db" ]; then
    SIZE=$(du -h signals.db | cut -f1)
    echo "  ✅ database: signals.db ($SIZE)"
else
    echo "  ❌ database: signals.db NOT found"
    ERRORS=$((ERRORS + 1))
fi

# 12d. Verify Telegram connectivity
echo "  ⏳ Telegram: testing outbound connection..."
TELEGRAM_OK=$(python run.py test 2>&1)
if echo "$TELEGRAM_OK" | grep -q "OK"; then
    echo "  ✅ Telegram test: working"
else
    echo "  ❌ Telegram test: $TELEGRAM_OK"
    ERRORS=$((ERRORS + 1))
fi

# 12e. Verify bot service is active
if systemctl is-active --quiet stock-bot.service; then
    echo "  ✅ Telegram Bot Service: running 24/7 (stock-bot.service)"
else
    echo "  ❌ Telegram Bot Service: failed to start"
    ERRORS=$((ERRORS + 1))
fi

# ── 13. Summary ──────────────────────────────────────────────
echo ""
echo "=== DEPLOYMENT RESULTS ==="
if [ "$ERRORS" -eq 0 ]; then
    echo "  🎉 All checks passed! System and Telegram bot are fully operational."
else
    echo "  ⚠️ $ERRORS check(s) failed. Review output above."
fi

echo ""
echo "=== QUICK COMMANDS ON VPS ==="
echo "  Check Bot status:  systemctl status stock-bot.service"
echo "  Restart Bot:       systemctl restart stock-bot.service"
echo "  View Bot logs:     journalctl -u stock-bot.service -f"
echo "  View Cron logs:    tail -f logs/daily.log"
echo "  Manual scan:       .venv/bin/python run.py daily --paper"
