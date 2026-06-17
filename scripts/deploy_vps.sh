#!/bin/bash
# deploy_vps.sh — complete VPS deployment for IDX Research System
# Run as root on your OVH VPS.
# This script transfers the project from your local machine.
#
# USAGE:
#   On your local Mac:
#     scp -r /path/to/research root@YOUR_VPS_IP:~/idx-research
#     ssh root@YOUR_VPS_IP
#     cd ~/idx-research && bash scripts/deploy_vps.sh

set -e

echo "=== IDX Research System — VPS Deployment ==="
echo ""

# 1. System update
apt update && apt upgrade -y

# 2. Python + essentials
apt install -y python3 python3-pip python3-venv git curl

# 3. Create required directories
cd ~/idx-research
mkdir -p data logs backups

# 4. Virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 5. Initialize database
python run.py init 2>/dev/null || python3 -c "from track import connect; connect(); print('DB initialized')"

# 6. Fetch historical data
echo ""
echo "=== Fetching historical data (5-10 minutes) ==="
python run.py fetch 2>/dev/null || python3 -c "from fetch import fetch_all; fetch_all()"

# 7. Run initial research
echo ""
echo "=== Running strategy backtest ==="
python run.py research

# 8. Set up cron
cat > /tmp/idx_cron << 'CRON'
# IDX Research System — daily at 17:30 WIB (10:30 UTC)
30 10 * * 1-5 cd /root/idx-research && .venv/bin/python run.py daily >> logs/daily.log 2>&1
# Weekly performance — Sunday 20:00 WIB
0 13 * * 0 cd /root/idx-research && .venv/bin/python run.py performance >> logs/weekly.log 2>&1
# Weekly DB backup — Sunday 21:00 WIB
0 14 * * 0 cp /root/idx-research/signals.db /root/idx-research/backups/signals_$(date +\%Y\%m\%d).db && find /root/idx-research/backups -name '*.db' -mtime +90 -delete
CRON
crontab /tmp/idx_cron

# 9. Security
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw --force enable 2>/dev/null || echo "ufw already active or unavailable"

# 10. Done
echo ""
echo "=== DEPLOYMENT COMPLETE ==="
echo ""
echo "Next steps (on your local machine, in a NEW terminal):"
echo "  1. Get Telegram token from @BotFather"
echo "  2. Get your Chat ID from @userinfobot"
echo "  3. Edit settings.py:"
echo "     ssh root@YOUR_VPS \"nano ~/idx-research/settings.py\""
echo "     Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
echo ""
echo "  4. Test Telegram:"
echo "     ssh root@YOUR_VPS \"cd ~/idx-research && .venv/bin/python telegram.py test\""
echo ""
echo "  5. Verify daily run tomorrow at 17:30 WIB"
echo "     Or test now:"
echo "     ssh root@YOUR_VPS \"cd ~/idx-research && .venv/bin/python run.py daily\""
echo ""
echo "  Files:"
echo "    ~/idx-research/"
echo "    ├── run.py           — main entry point"
echo "    ├── settings.py      — configuration (edit this)"
echo "    ├── fetch.py         — data downloader"
echo "    ├── research.py      — strategy backtester"
echo "    ├── gen_signal.py    — signal generation"
echo "    ├── track.py         — database tracking"
echo "    ├── telegram.py      — Telegram delivery"
echo "    ├── signals.db       — SQLite database"
echo "    ├── data/            — OHLCV CSV files"
echo "    ├── logs/            — cron output"
echo "    └── backups/         — weekly DB snapshots"
