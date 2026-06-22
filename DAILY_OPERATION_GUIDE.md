# Daily Operating Guide — Stock Signal System

## Project Location

```
~/Developer/Web/stock signal
```

**Important:** Do not use the nested `research/` folder. It has been archived.

## Virtual Environment

```
.venv/
```

Activate with:

```bash
source .venv/bin/activate
```

If you see `(.venv)` in your terminal prompt, it worked.

---

## Daily Routine

Run this once per trading day, ideally after market close (16:00–17:00 WIB).

This system is currently for **signal observation and paper trading only** — not automatic real-money execution.

### Step by Step

```bash
cd ~/Developer/Web/stock\ signal
source .venv/bin/activate
```

### 1. Run Paper Trading Cycle

```bash
python run.py paper
```

This:
- Checks open T6 paper positions for stop loss hits or max hold expiry
- Scans IDX80 for new volume divergence signals that pass the T6 filters
- Sends Telegram notification if there are entries or exits
- Does **not** fetch new data — assumes data is already up to date

### 2. Check Paper Status

```bash
python run.py paper-status
```

This shows:
- Open positions (ticker, entry date, entry price, current P&L)
- Closed trades (exit reason, P&L)
- Overall performance (win rate, profit factor, avg return, drawdown)

### 3. Read Output

Check Telegram for any notifications. If there are no signals, that is normal (see below).

### Optional: Full Daily Cycle (Production + Paper)

```bash
python run.py daily --paper
```

This runs the old production signal generation plus T6 paper in one go. For now, prefer `python run.py paper` to avoid mixing old production signals with T6 paper validation.

---

## Weekly Routine

Run once per week, e.g. Sunday evening.

### Command

```bash
python run.py weekly
```

This sends a Telegram report comparing paper performance against the T6 backtest expectation (CAGR +6.07%, PF 2.23, DD -4.75%).

### Weekly Review Checklist

| Item | What to Check |
|------|---------------|
| New signals this week | Count and tickers |
| Open paper positions | How many, how long open |
| Closed paper trades | Exit reason (stop / time) |
| Win rate | Above 50%? |
| Profit factor | Above 1.5? |
| Avg trade return | Positive? |
| Total paper return | Cumulative since start |
| Current drawdown | Within -5%? |
| Backtest alignment | Paper results roughly tracking T6 expectation? |
| Unusual ticker behavior | Any ticker consistently losing? |
| Duplicate reminders | How many this week? |
| System health | Telegram + DB working? |

**Do not change the strategy based on one bad week.** Weekly review is for monitoring, not over-optimization.

---

## Monthly Routine

Run at the end of each calendar month.

### Commands

```bash
python run.py health
python run.py weekly
python run.py paper-status
python run.py export
```

`python run.py export` generates CSV files under `exports/`:
- `signals.csv` — all signals with status
- `trades.csv` — all closed trades
- `weekly_summary.csv` — weekly aggregate metrics
- `monthly_summary.csv` — monthly aggregate metrics
- `dashboard.csv` — single-row summary snapshot

### Monthly Metrics to Review

- Total signals generated
- Paper trades opened / closed
- Monthly realized return
- Cumulative paper return
- Win rate
- Profit factor
- Average win / average loss
- Max drawdown
- Largest losing / winning trade
- Duplicate signal reminder count
- Comparison vs T6 backtest expectation

### Strategy Review Checklist

| Question | Guideline |
|----------|-----------|
| CAGR/paper return aligned with expectation? | Broadly track T6 (+6% CAGR) |
| Max drawdown acceptable? | Should be within -10% |
| PF still above 1.15? | Below 1.15 signals concern |
| Losses from one ticker or broad? | Check if a single ticker is causing most losses |
| Enough trades to judge? | Need 10+ closed trades for meaningful stats |
| Market regime changed? | Check if market conditions shifted significantly |
| Filters still reasonable? | Are MA50 and vol ratio filters still appropriate? |
| Data quality good? | Run `python run.py health` to verify |

### Decision Rules

| Situation | Action |
|-----------|--------|
| Fewer than 10 closed paper trades | **Do not change strategy.** Continue observing. |
| Only one bad week | **Do not optimize.** |
| After 1 month still too few trades | Continue observation — T6 averages ~21 trades/year |
| After 3 months or 20+ trades, paper result is much worse than backtest | **Strategy review needed.** |

---

## Health Check

```bash
python run.py health
```

Checks: data integrity, database health, required files/config, duplicate signals, signal rate, disk space, Telegram connectivity.

Run this:
- After code changes
- When data seems wrong
- Before weekly review (optional)
- **Not needed every day**

---

## Fetch Data

```bash
python run.py fetch
```

Downloads latest OHLCV data from Yahoo Finance for all tickers in the configured universe.

The `paper` command does **not** fetch data automatically — it uses whatever CSV files are already in `data/`. Run `python run.py fetch` before `python run.py paper` if you haven't updated data recently.

The `daily --paper` command fetches data automatically.

Recommended approach:
```bash
python run.py fetch
python run.py paper
python run.py paper-status
```

---

## When There Is No Signal

No signal is normal. T6 is designed to be selective — it generated ~73 trades over 3.5 years (~21 per year). On most days, no ticker will pass all filters (MA50, vol ratio ≥ 2.0, volume divergence, large/mid cap).

No signal does **not** mean the system is broken.

---

## When There Is a Signal

1. Read the Telegram message
2. Check: ticker, close price, MA50 status, volume ratio, ATR, stop loss, max hold date
3. **Do not automatically buy with real money**
4. Treat it as paper trading / observation unless explicitly decided otherwise

---

## When There Is a Duplicate Signal Reminder

A duplicate reminder means the same ticker generated another BUY signal while a previous signal is still open.

- This is **confirmation/reminder**, not a new entry recommendation
- Do not create a duplicate position manually unless planned
- The system does not enter a new paper position for duplicates

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `python: command not found` | Try `python3 run.py paper` |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| `venv not found` | Create it: `python3 -m venv .venv` then `source .venv/bin/activate && pip install -r requirements.txt` |
| `Research folder does not exist` | The `research/` folder was archived. All files are now at the project root. Use `cd ~/Developer/Web/stock\ signal` directly. |
| No Telegram messages | Check `.env` has `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set. |

---

## Recommended SOP Summary

### Daily

```
1. Open terminal
2. cd ~/Developer/Web/stock\ signal
3. source .venv/bin/activate
4. python run.py fetch          (only if data may be stale)
5. python run.py paper
6. python run.py paper-status
7. Read Telegram / terminal output
```

### Weekly (Sunday)

```
1. cd ~/Developer/Web/stock\ signal
2. source .venv/bin/activate
3. python run.py weekly
4. python run.py paper-status
5. Review performance and open positions
6. Do not change strategy based on one bad week
```

### Monthly (last day)

```
1. cd ~/Developer/Web/stock\ signal
2. source .venv/bin/activate
3. python run.py health
4. python run.py weekly
5. python run.py paper-status
6. python run.py export
7. Review monthly metrics
8. Only consider strategy changes after enough closed trades
```
