# VPS Deployment Runbook — IDX Stock Signal Research

This runbook walks you through deploying the IDX stock signal research system to
your OVH Cloud VPS. Every command is explained. No Linux expertise required.

---

## 1. VPS Requirements

### Recommended Spec (OVH VPS)

| Spec | Minimum | Recommended |
|------|---------|-------------|
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| vCPU | 1 | 2 |
| RAM | 1 GB | 2 GB |
| Disk | 20 GB | 40 GB |
| Bandwidth | Unmetered | Unmetered |

### Why These Requirements

- **1 GB RAM** is enough because the system only runs once per day and uses
  SQLite (not a heavy database). The most expensive operation (backtest) needs
  about 500 MB.
- **20 GB disk** holds 7+ years of daily stock data for 15 tickers
  (about 50 MB total), plus logs and database backups.
- **Ubuntu** is required because the deploy script uses `apt`.

### Required Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH (remote login) |
| 443 | TCP/UDP | Outbound HTTPS (Yahoo Finance API, Telegram API) |

The system does NOT need port 80 (HTTP) because there is no website. The VPS
only talks **outbound** to Yahoo Finance and Telegram. No inbound traffic is
needed (except SSH).

### Required Packages

These are installed automatically by the deploy script, but listed here for
reference:

- `python3` + `python3-pip` + `python3-venv` — Python runtime
- `git` — version control (optional, for future updates)
- `curl` — network diagnostics
- `cron` — task scheduler (usually pre-installed)
- `ufw` — firewall

---

## 2. Initial VPS Setup

### 2.1 Get Your VPS IP

When you order an OVH VPS, you receive an email with:

- **IP address** (e.g. `123.45.67.89`)
- **Root password** (a long random string)

Keep both handy.

### 2.2 SSH Login

Open **Terminal** on your Mac. Run:

```bash
ssh root@YOUR_VPS_IP
```

Replace `YOUR_VPS_IP` with the IP from the OVH email.

**First time connecting?** You'll see:

```
The authenticity of host '123.45.67.89 (123.45.67.89)' can't be established.
ED25519 key fingerprint is SHA256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.
Are you sure you want to continue connecting? (yes/no/[fingerprint])
```

Type `yes` and press Enter. This is normal — it saves the VPS fingerprint to
your Mac so future connections are secure.

**Enter password** when prompted. Paste the root password from your email.
The password field is hidden — you won't see dots or stars. Just type and
press Enter.

**Important:** You should see a prompt like:

```
root@vps-xxxxxx:~#
```

This means you are logged in as `root` on the VPS. Everything from now on runs
here unless the command says "On your Mac".

### 2.3 (Optional) Change Root Password

It is good security to change the default password:

```bash
passwd
```

You'll be asked for:
1. Current password (paste from email)
2. New password (type twice)

### 2.4 What the Deploy Script Handles

The deploy script runs all of these automatically. You do NOT need to run them
manually. They are listed here so you understand what happens:

```bash
# Update all installed software (security patches, bug fixes)
apt update && apt upgrade -y

# Install Python, tools, and scheduler
apt install -y python3 python3-pip python3-venv git curl cron
```

- `apt update` — refreshes the list of available packages
- `apt upgrade -y` — installs the latest versions
- `-y` — answers "yes" to all confirmation prompts automatically

---

## 3. Uploading the Project

### 3.1 On Your Mac: Copy Project to VPS

Open a **new** Terminal window on your Mac (keep the VPS SSH session open).
Run:

```bash
cd /Users/cahyo/Developer/Web/stock\ signal/research
scp -r . root@YOUR_VPS_IP:~/idx-research
```

**What this does:**
- `scp` = secure copy (uses SSH encryption)
- `-r` = recursive (copy folder + all contents)
- `.` = current folder (research/)
- `root@YOUR_VPS_IP:~/idx-research` = destination: the `root` user's home
  directory (`~`), into a folder called `idx-research`

**Enter the VPS root password** when prompted.

**Expected output:** A progress bar for each file:

```
.gitignore       100%  101     ...
analyze.py       100%   23KB   ...
audit.py         100%   24KB   ...
...
```

**Time:** 10-30 seconds depending on your internet.

### 3.2 Verify Upload

Back in your VPS SSH session, run:

```bash
ls ~/idx-research/
```

You should see:

```
analyze.py        fetch.py       monitor.py     run.py          track.py
audit.py          filter.py      requirements.txt  scripts/
data/             gen_signal.py  research.py    settings.py     telegram_sender.py
```

### 3.3 If SCP Fails

**Permission denied:**
```bash
# Make sure the destination folder exists
ssh root@YOUR_VPS_IP "mkdir -p ~/idx-research"
# Then retry the scp command
```

**Connection timeout:**
Your VPS might have a firewall blocking SSH. Log into the OVH control panel
and check that the firewall allows port 22.

**Slow upload:**
Compress first to speed things up:

```bash
# On your Mac — compress
cd /Users/cahyo/Developer/Web/stock\ signal
tar czf research.tar.gz research/

# Upload the single archive file
scp research.tar.gz root@YOUR_VPS_IP:~/

# On the VPS — extract
ssh root@YOUR_VPS_IP "tar xzf ~/research.tar.gz -C ~/ && mv ~/research ~/idx-research"
```

---

## 4. Deployment

### 4.1 Run the Deploy Script

In your VPS SSH session:

```bash
cd ~/idx-research
bash scripts/deploy_vps.sh
```

### 4.2 What the Script Does (Step by Step)

**Step 1 — System update:**
Updates all installed software to the latest versions. This ensures you have
the latest security patches and compatible libraries.

**Step 2 — Install packages:**
Installs Python 3, pip, venv, git, curl, and cron (if not already present).

**Step 3 — Create directories:**
Creates three folders:
- `data/` — stock price CSV files
- `logs/` — daily/weekly output files
- `backups/` — weekly database snapshots

**Step 4 — Virtual environment:**
Creates a Python virtual environment in `.venv/`. This is an isolated Python
installation that contains only the packages this project needs. It prevents
conflicts with other Python projects on the VPS.

Then installs packages from `requirements.txt`:
```
pip install --upgrade pip -q
pip install -r requirements.txt -q
```

**Step 5 — Initialize database:**
Creates an empty SQLite database (`signals.db`) with the correct table
structure (signals table + trades table).

**Step 6 — Fetch historical data:**
Downloads 7+ years of daily OHLCV data for all 15 LQ45 stocks from Yahoo
Finance Indonesia. This takes 5-10 minutes.

**Step 7 — Run backtest:**
Runs the strategy backtester to compute baseline metrics for all 4 strategies.
This validates that the data is good and confirms the volume divergence edge.

**Step 8 — Set up cron:**
Installs 5 cron entries (see Section 6 for details).

**Step 9 — Enable cron service:**
Ensures cron starts automatically when the VPS reboots.

**Step 10 — Security (firewall):**
Enables UFW (Uncomplicated Firewall) with the default policy:
- Block all incoming connections (deny incoming)
- Allow all outgoing connections (allow outgoing)
- Only exception: SSH (port 22) is allowed in

This means nobody can access your VPS except you via SSH.

**Step 11 — Verification:**
Automatically checks:
- cron installed
- crontab entries loaded
- Database file exists
- Telegram connectivity
- Daily command runs without errors
- Logs directory is writable

### 4.3 Expected Output (Last Few Lines)

```
=== DEPLOYMENT RESULTS ===
  All checks passed. System is operational.
```

If you see failures, the script tells you which check failed. Fix the issue
and re-run:

```bash
bash scripts/deploy_vps.sh
```

---

## 5. Deployment Verification

Run each command below manually after deployment to confirm everything works.
Commands run from: `~/idx-research/` on the VPS.

### 5.1 Virtual Environment

```bash
.venv/bin/python --version
```

**Expected:**
```
Python 3.10.x
```

**Failure:**
```
-bash: .venv/bin/python: No such file or directory
```
→ The virtual environment was not created. Re-run the deploy script.

### 5.2 Database

```bash
ls -lh signals.db
```

**Expected:**
```
-rw-r--r-- 1 root root 32K Jun 17 21:30 signals.db
```

The file should exist and be non-zero. Size varies (32 KB is normal for a
new database).

**Failure:**
```
ls: cannot access 'signals.db': No such file or directory
```
→ Database was not initialized. Run: `python run.py init`

### 5.3 Telegram

```bash
.venv/bin/python run.py test
```

**Expected:**
```
Telegram OK.
```

You should also receive a Telegram message on your phone:
```
🤖 IDX Research System — ONLINE
Time: 2026-06-17 21:30:55
```

**Failure:**
```
Telegram FAILED.
```
→ Check settings.py has correct token and chat ID. See Troubleshooting.

### 5.4 Fetch

```bash
.venv/bin/python run.py fetch
```

**Expected:**
```
=== Fetching all tickers ===
[fetch] Fetching BBCA...
[fetch] BBCA: 2073 rows saved
...
[fetch] Done: 15 OK, 0 failed
```

All 15 tickers should succeed. Some may show warnings like:
```
[fetch] BBRI: price spikes >20% on: 2020-03-26
```
This is normal — historical data has occasional anomalies.

**Failure:**
```
[fetch] Done: 0 OK, 15 failed
```
→ Network issue or Yahoo Finance blocking the VPS IP.

**Partial failure (e.g., 14 OK, 1 failed):**
→ The failed ticker may be temporarily unavailable. Re-run `run.py fetch`.

### 5.5 Daily

```bash
.venv/bin/python run.py daily
```

**Expected output (with signals):**
```
21:42:54 [run] === Daily run: 2026-06-17 ===
21:42:54 [run] Fetching data...
...
21:43:30 [run] Generating signals...
21:43:31 [run] Done: 1 signals sent
```

**Expected output (no signals — also normal):**
```
21:42:54 [run] === Daily run: 2026-06-17 ===
21:42:54 [run] Fetching data...
...
21:43:30 [run] Generating signals...
21:43:31 [run] No signals generated today.
```

**Telegram message (with signals):**
```
📈 BUY $BBCA  |  Confidence: 72/100

Entry: 10,500 – 10,605
Stop:  10,200  (2.8%)
TP:    11,010  |  R:R: 2.5

Volume Divergence: bullish signal detected

ID: SIG-20260617-A3F2
```

**Telegram message (no signals):**
```
🤖 IDX Research — 2026-06-17

No signals generated today.
```

**Failure (error):**
```
Traceback (most recent call last):
  File "/root/idx-research/run.py", line ...
```
→ Debug the Python error. Run the full path: `.venv/bin/python run.py daily`
  (not just `python run.py daily`).

### 5.6 Health

```bash
.venv/bin/python run.py health
```

**Expected:**
```
🩺 SYSTEM HEALTH

✅ System time: PASS   2026-06-17 21:20
✅ DB integrity: PASS
✅ DB tables: PASS
✅ DB size: PASS   0.0 MB
✅ Data files: PASS   15/15 tickers have data
✅ Data freshness: PASS
⚠️ Daily log: WARN   No daily.log found (may be first run)
⚠️ Backup: WARN   No backups directory found

Result: 8/10 passed, 2 warnings, 0 failures
Overall: WARN
```

**Warnings are normal** for a fresh deployment — they indicate the system
hasn't had time to accumulate logs and backups yet.

**Telegram message:**
Same text sent to your phone.

**Failure (e.g., disk space or database corruption):**
```
❌ DB integrity: FAIL   Database error: ...
```

### 5.7 Performance

```bash
.venv/bin/python run.py performance
```

**Expected (no trades yet — first run):**
```
No closed trades yet.
```

**Expected (after some trades):**
```
📊 Performance Report

Trades: 25  (W: 14 / L: 11)
Win Rate: 56.0%
Avg Return: +1.23%
Total Return: +30.75%
Sharpe: 0.85
Max Cons Losses: 3
```

**Telegram message:**
Same text sent to your phone.

---

## 6. Cron Verification

### 6.1 Check Active Cron Jobs

```bash
crontab -l
```

**Expected output:**

```
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
```

### 6.2 Cron Schedule Table (All Times)

| WIB (Jakarta) | UTC (VPS) | Day | Frequency | Job |
|---|---|---|---|---|
| **17:30** | **10:30** | Mon–Fri | Daily | `run.py daily` — fetch data, generate signals, Telegram |
| **17:35** | **10:35** | Mon–Fri | Daily | `run.py health` — run 10 health checks, Telegram |
| **20:00** | **13:00** | Sunday | Weekly | `run.py performance` — trade stats, Telegram |
| **21:00** | **14:00** | Sunday | Weekly | Backup `signals.db`, delete backups older than 90 days |
| **09:00** | **02:00** | Sunday | Weekly | Delete log files older than 30 days |

### 6.3 What Each Entry Means

**Entry 1: `30 10 * * 1-5`**
- `30` = minute 30
- `10` = hour 10 (UTC)
- `*` = every day of month
- `*` = every month
- `1-5` = Monday (1) through Friday (5)
- Runs → 10:30 UTC = 17:30 WIB, weekdays only

**Entry 2: `35 10 * * 1-5`**
- Same days, 5 minutes after the daily run

**Entry 3: `0 13 * * 0`**
- `0` = minute 0
- `13` = hour 13 (UTC)
- `0` = Sunday
- Runs → 13:00 UTC = 20:00 WIB, Sundays

**Entry 4: `0 14 * * 0`**
- Runs → 14:00 UTC = 21:00 WIB, Sundays

**Entry 5: `0 2 * * 0`**
- Runs → 02:00 UTC = 09:00 WIB, Sundays

### 6.4 What 2>&1 Means

`2>&1` redirects error messages to the same file as normal output. Without
this, errors would not appear in the log files.

---

## 7. Timezone Verification

### 7.1 Check Current Timezone

```bash
timedatectl
```

**Expected output:**
```
               Local time: Wed 2026-06-17 14:30:00 UTC
           Universal time: Wed 2026-06-17 14:30:00 UTC
                 RTC time: Wed 2026-06-17 14:30:00
                Time zone: Etc/UTC (UTC, +0000)
```

### 7.2 Recommendation: Keep UTC

**Do not change the VPS timezone.** The cron entries use UTC intentionally:

| Cron Schedule | Meaning |
|---|---|
| `30 10 * * 1-5` | 10:30 UTC = 17:30 WIB (Indonesia, UTC+7) |
| `35 10 * * 1-5` | 10:35 UTC = 17:35 WIB |
| `0 13 * * 0` | 13:00 UTC = 20:00 WIB Sunday |
| `0 14 * * 0` | 14:00 UTC = 21:00 WIB Sunday |
| `0 2 * * 0` | 02:00 UTC = 09:00 WIB Sunday |

Time zones are confusing. If you change the VPS timezone, the cron entries
would fire at the wrong time. The cron entries are already set to UTC so
they fire at the correct WIB times.

**If you want to verify:**
```bash
# Current time in UTC
date -u
# Current time in WIB (Jakarta)
TZ='Asia/Jakarta' date
```

### 7.3 If You Insist on Changing to WIB

Not recommended, but if you do:

```bash
timedatectl set-timezone Asia/Jakarta
```

Then you would need to **edit the cron entries** to use WIB times:
- `30 17 * * 1-5` (was 30 10)
- `35 17 * * 1-5` (was 35 10)
- `0 20 * * 0` (was 0 13)
- `0 21 * * 0` (was 0 14)
- `0 9 * * 0` (was 0 2)

And reinstall the crontab:
```bash
crontab /tmp/idx_cron   # after editing the times
```

---

## 8. Telegram Monitoring Workflow

After deployment, Telegram is your primary way to monitor the system. Here is
what each message looks like and what it means.

### 8.1 Daily Signal (17:30 WIB, Mon–Fri)

**With signals:**
```
📈 BUY $BBCA  |  Confidence: 72/100

Entry: 10,500 – 10,605
Stop:  10,200  (2.8%)
TP:    11,010  |  R:R: 2.5

Volume Divergence: bullish signal detected

ID: SIG-20260617-A3F2
```

**Without signals:**
```
🤖 IDX Research — 2026-06-17

No signals generated today.
```

**What to check:**
- R:R (risk-reward) should be ≥ 1.5
- Confidence should be ≥ 50
- Did you receive the message around 17:30 WIB?

### 8.2 Health Check (17:35 WIB, Mon–Fri)

```
🩺 Daily Health Check

✅ System time: PASS   2026-06-17 17:35
✅ DB integrity: PASS
✅ Data files: PASS   15/15 tickers have data
...

Result: 9/10 passed, 1 warning, 0 failures
Overall: PASS
```

**What to check:**
- Any ❌ FAIL items? (if yes, investigate)
- Is the overall status PASS or WARN?
- WARN items are usually expected (e.g., "no backups yet" on first run)

### 8.3 Weekly Performance (Sunday 20:00 WIB)

```
📊 Performance Report

Trades: 25  (W: 14 / L: 11)
Win Rate: 56.0%
Avg Return: +1.23%
Total Return: +30.75%
Sharpe: 0.85
Max Cons Losses: 3
```

**What to check:**
- Win rate trending above 50%?
- Sharpe ratio trending above 1.0?
- Max consecutive losses not too large (single digits OK)

### 8.4 Error Alert

There are **no automatic error alert messages**. If a cron job fails, the
error goes to the log file but no Telegram message is sent. This is why:

1. The health check at 17:35 WIB checks for errors in the log
2. If you see warnings, you investigate manually

**To manually check for errors:**
```bash
# Check today's daily run
tail -n 20 logs/daily.log

# Search for errors in all logs
grep -r "ERROR\|Traceback\|FAILED" logs/
```

---

## 9. Log Inspection

### 9.1 Reading Logs

Logs are plain text files. Use these commands:

```bash
# Show the last 20 lines (most recent entries)
tail -20 logs/daily.log

# Show new lines as they appear (live view)
tail -f logs/daily.log

# Show last 50 lines with line numbers
tail -50 -n logs/daily.log

# Search for errors
grep -i "error\|fail\|traceback" logs/daily.log
```

### 9.2 Normal Log (Healthy System)

```
17:30:01 [run] === Daily run: 2026-06-17 ===
17:30:01 [run] Fetching data...
17:30:02 [fetch] Fetching BBCA...
17:30:03 [fetch] BBCA: 1 new rows saved
17:30:03 [fetch] Fetching BBRI...
17:30:04 [fetch] BBRI: 1 new rows saved
...
17:30:15 [fetch] Done: 15 OK, 0 failed
17:30:15 [run] Generating signals...
17:30:16 [run] Done: 2 signals sent
```

**What to look for:**
- All 15 tickers OK
- "Done: X signals sent" or "No signals generated today"
- No ERROR lines

### 9.3 Warning Log (Normal Issues)

```
17:30:05 [fetch] BBRI: price spikes >20% on: 2020-03-26
```

This is **normal**. Yahoo Finance historical data has occasional anomalies
from corporate actions (stock splits, rights issues). These warnings can be
ignored — the system handles them.

### 9.4 Error Log (Requires Action)

```
17:30:02 [fetch] BBCA: HTTP 429 Too Many Requests (attempt 1/3)
17:30:03 [fetch] BBCA: retrying in 1s...
17:30:05 [fetch] BBCA: HTTP 429 Too Many Requests (attempt 2/3)
17:30:07 [fetch] BBCA: retrying in 2s...
17:30:11 [fetch] BBCA: HTTP 429 Too Many Requests (attempt 3/3)
17:30:11 [fetch] BBCA: download failed
17:30:11 [run] Telegram failed for SIG-20260617-...
17:30:11 [telegram] Telegram send failed: HTTP 429 Too Many Requests (attempt 1/3)
```

**What to look for:**
- `ERROR` in the log line
- `Traceback (most recent call last)` = Python crash
- Multiple retries that all fail

### 9.5 Log File Locations

| File | Contents | Created By |
|---|---|---|
| `logs/daily.log` | Daily fetch + signal generation | Cron (17:30 Mon–Fri) |
| `logs/health.log` | Health check results | Cron (17:35 Mon–Fri) |
| `logs/weekly.log` | Weekly performance report | Cron (20:00 Sun) |

### 9.6 Logs Are Automatically Cleaned

Log files older than 30 days are deleted every Sunday at 09:00 WIB. You don't
need to clean them manually.

---

## 10. Backup & Recovery

### 10.1 How Backups Work

Every Sunday at 21:00 WIB, the system:
1. Copies `signals.db` to `backups/signals_YYYYMMDD.db`
2. Deletes any backup older than 90 days

Backups are stored in: `~/idx-research/backups/`

### 10.2 List Available Backups

```bash
ls -lh ~/idx-research/backups/
```

**Expected:**
```
-rw-r--r-- 1 root root 32K Jun 17 21:00 signals_20260617.db
-rw-r--r-- 1 root root 28K Jun 10 21:00 signals_20260610.db
```

### 10.3 Restore from Backup

**If signals.db becomes corrupted:**

```bash
cd ~/idx-research

# Stop the system from generating new signals temporarily
# (just wait — cron will run again tomorrow)

# Find the latest backup
ls -lt backups/

# Restore it
cp backups/signals_20260617.db signals.db

# Verify
ls -lh signals.db
```

### 10.4 Verify Restore Success

```bash
# Check database integrity
.venv/bin/python -c "
from track import connect
conn = connect()
cursor = conn.execute('SELECT COUNT(*) FROM signals')
print(f'Signals in database: {cursor.fetchone()[0]}')
conn.close()
"
```

**Expected:**
```
Signals in database: 42
```
(Number will vary based on how many signals were generated.)

### 10.5 What Backups DO NOT Include

- **Stock data CSV files** in `data/` — these are re-downloaded from Yahoo
  Finance automatically on the next `run.py daily`
- **Settings** in `settings.py` — keep a copy of this file separately
- **Log files** — logs are regenerated

### 10.6 Full System Recovery (Worst Case)

If the VPS is completely destroyed (disk failure, reinstall):

```bash
# On your Mac — re-deploy from scratch
scp -r . root@NEW_VPS_IP:~/idx-research
ssh root@NEW_VPS_IP
cd ~/idx-research
bash scripts/deploy_vps.sh

# Then copy the latest backup from your Mac (if you saved one)
# Or accept that you start fresh
```

---

## 11. VPS Reboot Test

After deployment, test that the system survives a reboot.

### 11.1 Reboot the VPS

```bash
reboot
```

**What happens:**
1. Your SSH session disconnects (normal)
2. The VPS takes 30-90 seconds to shut down and start up
3. Cron automatically starts on boot (we enabled this in the deploy script)

### 11.2 Wait 60 Seconds

Do not try to reconnect immediately. Wait at least 60 seconds.

### 11.3 Reconnect

```bash
ssh root@YOUR_VPS_IP
```

### 11.4 Verify Cron Is Running

```bash
systemctl status cron
```

**Expected (excerpt):**
```
● cron.service - Regular background program processing daemon
     Loaded: loaded (/lib/systemd/system/cron.service; enabled; vendor preset: enabled)
     Active: active (running) since Wed 2026-06-17 14:35:00 UTC; 2min ago
```

Key checks:
- `Loaded: ... enabled` — cron is set to start on boot ✓
- `Active: active (running)` — cron is running now ✓

### 11.5 Verify Crontab Survived

```bash
crontab -l | grep idx-research | wc -l
```

**Expected:** `5` (five cron entries)

### 11.6 Verify System Works

```bash
cd ~/idx-research
.venv/bin/python run.py health
```

Should produce your normal health check output.

### 11.7 If Cron Is Not Running After Reboot

```bash
# Start cron
systemctl start cron

# Enable it for future boots (just in case)
systemctl enable cron
```

---

## 12. Troubleshooting Guide

### Telegram Not Sending

**Symptoms:**
- `run.py test` prints `Telegram FAILED.`
- No Telegram messages arrive on your phone

**Possible causes:**
- Wrong bot token in settings.py
- Wrong chat ID in settings.py
- Bot not started (did you message @BotFather?)
- VPS can't reach api.telegram.org (network issue)

**Diagnosis:**
```bash
# 1. Check network connectivity to Telegram
curl -s -o /dev/null -w "%{http_code}" https://api.telegram.org

# Expected: 200 (OK)
# If timeout or connection refused → VPS network issue

# 2. Check bot token validity
curl -s "https://api.telegram.org/botYOUR_TOKEN/getMe"

# Expected: {"ok":true,"result":{"id":...,"username":"..."}}
# If 401 Unauthorized → wrong token

# 3. Check settings.py
cat ~/idx-research/settings.py | grep -E "TELEGRAM"
```

**Fixes:**
```bash
# Fix token: get a new one from @BotFather
nano ~/idx-research/settings.py
# Update TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, save, exit

# Retry
.venv/bin/python run.py test
```

### Yahoo Finance Download Fails

**Symptoms:**
- `run.py fetch` shows `0 OK, 15 failed`
- `run.py daily` shows errors in log

**Possible causes:**
- VPS IP blocked by Yahoo (rate limiting)
- Network outage
- Yahoo API changes

**Diagnosis:**
```bash
# 1. Test network connectivity
curl -s -o /dev/null -w "%{http_code}" https://query1.finance.yahoo.com

# Expected: 200, 301, or 404 (any means reachable)
# If timeout → VPS cannot reach Yahoo

# 2. Check if Yahoo is blocking the IP
curl -s "https://query1.finance.yahoo.com/v8/finance/chart/BBCA.JK" | head -c 200

# 3. Test on a single ticker
.venv/bin/python -c "
from fetch import fetch_one
df = fetch_one('BBCA')
print('OK' if df is not None else 'FAIL')
"
```

**Fixes:**
```bash
# Wait 15 minutes and retry (temporary rate limit)
# The system already retries 3 times automatically (1s, 2s, 4s delays)

# If Yahoo permanently blocks the VPS IP:
# 1. Try a different Yahoo Finance endpoint
# 2. Or get a new VPS IP from OVH control panel
```

### Cron Not Running

**Symptoms:**
- No log files in `logs/`
- `logs/daily.log` is empty
- No Telegram messages at scheduled times

**Diagnosis:**
```bash
# 1. Check if cron service is running
systemctl status cron

# 2. Check crontab contents
crontab -l

# 3. Check cron logs (Ubuntu)
grep -i "idx-research\|daily\|health" /var/log/syslog | tail -20

# 4. Test cron execution manually
cd ~/idx-research && .venv/bin/python run.py daily >> logs/daily.log 2>&1
cat logs/daily.log
```

**Fixes:**
```bash
# Cron not running
systemctl start cron
systemctl enable cron

# Crontab empty — reinstall
bash ~/idx-research/scripts/deploy_vps.sh
# (it's safe to re-run — steps are idempotent)

# Wrong Python path — check venv
ls -la ~/idx-research/.venv/bin/python
```

### Database Missing

**Symptoms:**
- `ls signals.db` → "No such file"
- Python errors: `OperationalError: unable to open database file`

**Diagnosis:**
```bash
ls ~/idx-research/signals.db
```

**Fixes:**
```bash
cd ~/idx-research
.venv/bin/python run.py init
ls -lh signals.db
```

If a backup exists, restore instead (see Section 10).

### Health Check Warning

**Symptoms:**
```
⚠️ Daily log: WARN   No daily.log found (may be first run)
⚠️ Backup: WARN   No backups directory found
⚠️ Disk space: WARN   4.2 GB free
```

**These are normal warnings** that don't require action:
- "No daily.log" — the first daily run hasn't happened yet
- "No backups" — it's before the first Sunday
- "Disk space" — you decide if you need to clean up

**When to be concerned:**
```
❌ DB integrity: FAIL
❌ Telegram: FAIL
❌ Data files: FAIL   Missing: BBCA
❌ Rolling 30t: FAIL   precision 42% < 50%
```

Any ❌ FAIL requires investigation.

### Disk Full

**Symptoms:**
- `run.py fetch` fails with "No space left on device"
- Cannot SSH into VPS (disk full prevents login)

**Diagnosis:**
```bash
# Check disk usage
df -h

# Check large files in idx-research
du -sh ~/idx-research/*/
du -sh ~/idx-research/logs/*.log
```

**Fixes:**
```bash
# Force log rotation
find ~/idx-research/logs -name '*.log' -delete

# Remove old backups (keep newest 2)
cd ~/idx-research/backups
ls -t | tail -n +3 | xargs rm -f

# Remove old data and re-fetch (extreme)
rm -rf ~/idx-research/data/
.venv/bin/python run.py fetch

# Clean apt cache
apt clean
```

### VPS Rebooted Unexpectedly

**Symptoms:**
- No Telegram messages for multiple days
- Cannot SSH in (if still booting)

**Diagnosis:**
```bash
# Check system uptime
uptime

# Check cron status
systemctl status cron

# Check last log entries
tail -20 ~/idx-research/logs/daily.log

# Check if processes ran after reboot
grep "=== Daily run:" ~/idx-research/logs/daily.log | tail -5
```

**Fixes:**
```bash
# If cron didn't start
systemctl start cron
systemctl enable cron

# If a daily run was missed, run it manually
cd ~/idx-research
.venv/bin/python run.py daily

# Check health
.venv/bin/python run.py health
```

---

## 13. Final Go-Live Checklist

Run through each item after deployment and before considering the system live.

### Pre-Deployment
```
[ ] VPS ordered from OVH
[ ] Ubuntu 22.04+ installed
[ ] Root password received from OVH email
[ ] Telegram bot created via @BotFather
[ ] Bot token and chat ID set in settings.py
[ ] Local project folder: ~/Developer/Web/stock signal/research/
```

### Deployment
```
[ ] SCP upload completed successfully
[ ] Deploy script ran without errors (bash scripts/deploy_vps.sh)
[ ] All verification checks passed
```

### Verification

Run each command and confirm the output:

```bash
# 1. Virtual environment
[ ] .venv/bin/python --version
```
Confirmed output: `Python 3.10.x`

```bash
# 2. Database exists
[ ] ls -lh signals.db
```
Confirmed output: `signals.db` exists and is non-zero

```bash
# 3. Telegram working
[ ] .venv/bin/python run.py test
```
Confirmed output: `Telegram OK.` + message on phone

```bash
# 4. Data download works
[ ] .venv/bin/python run.py fetch
```
Confirmed output: `Done: 15 OK, 0 failed`

```bash
# 5. Daily cycle works
[ ] .venv/bin/python run.py daily
```
Confirmed output: `Done: X signals sent` (or `No signals generated today`)

```bash
# 6. Health check works
[ ] .venv/bin/python run.py health
```
Confirmed output: Health report with no ❌ FAIL items

```bash
# 7. Performance report works
[ ] .venv/bin/python run.py performance
```
Confirmed output: Trade stats (or "No closed trades yet")

### Cron
```
[ ] crontab -l shows 5 idx-research entries
[ ] cron service is running (systemctl status cron)
[ ] cron is enabled for auto-start (systemctl is-enabled cron)
```

### Backup
```
[ ] Backup directory exists: ls ~/idx-research/backups/
[ ] Manual backup works: cp signals.db backups/test.db && rm backups/test.db
```

### Logs
```
[ ] logs/ directory exists
[ ] logs/daily.log has content after running run.py daily
```

### Reboot Test
```
[ ] VPS rebooted: reboot
[ ] Reconnected via SSH after 60 seconds
[ ] cron still running: systemctl status cron
[ ] crontab still intact: crontab -l
[ ] System works: .venv/bin/python run.py health
```

### Go-Live
```
[ ] All checks above passed
[ ] Cron will fire at 17:30 WIB tomorrow (or next weekday)
[ ] Phone ready to receive Telegram message at 17:30 WIB
[ ] Email notification set for VPS billing (auto-renew)
```

**Total check marks: 24**

---

## Appendix: Quick Reference Card

```bash
# === Daily operations ===
.venv/bin/python run.py daily       # Run the daily cycle
.venv/bin/python run.py health      # Run health check
.venv/bin/python run.py performance # View trade performance

# === Logs ===
tail -f logs/daily.log              # Watch daily run in real-time
tail -f logs/health.log             # Watch health check in real-time
grep ERROR logs/*.log               # Find errors in all logs

# === Database ===
.venv/bin/python run.py init        # Recreate database (if lost)
ls -lh signals.db                   # Check database file size
ls -lh backups/                     # List backups

# === Cron ===
crontab -l                          # View scheduled jobs
systemctl status cron               # Check if cron is running

# === Configuration ===
nano settings.py                    # Edit Telegram tokens and settings

# === System ===
df -h                               # Check disk space
uptime                              # How long since last reboot
reboot                              # Restart VPS
```

---

*Deployment runbook v1.0 — IDX Stock Signal Research System*
*Questions? Report issues at https://github.com/anomalyco/opencode/issues*
