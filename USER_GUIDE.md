# User Guide — How to Use the IDX Stock Signal System

> **Document Location:** `/Users/cahyo/Developer/Web/stock signal/USER_GUIDE.md`  
> **System:** IDX Stock Signal System (T6_TREND_FILTERED + Scalping & Swing Engine)  
> **Target Market:** Indonesian Stock Exchange (IDX spot equities)

---

## 📖 Introduction

The **IDX Stock Signal System** is an automated quantitative research and paper-trading assistant designed for the Indonesian stock market. It scans **179 liquid stocks** (including penny stocks down to Rp 50) for **Volume Divergence** patterns (smart money accumulation) and generates real-time alerts via Telegram.

---

## ⚡ 1. Understanding Signal Types

The system categorizes signals into two distinct strategies based on trend confirmation and holding duration:

```
                                  ┌─────────────────────────┐
                                  │   IDX SIGNAL ENGINE     │
                                  └────────────┬────────────┘
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       ▼                                               ▼
             ⚡ SCALPING SIGNAL                                📈 SWING SIGNAL
             • Target Hold: 1–5 Days                           • Target Hold: 10–20 Days
             • Stop Loss: 1.5x ATR (~2-4%)                     • Stop Loss: 3.0x ATR (~5-7%)
             • Take Profit: 2.5x ATR (~5-8%)                   • Trailing Stop: HWM - 2.2x ATR
             • Vol Ratio >= 1.3x                               • Trend: Price > MA50 + Vol Ratio >= 2.0x
```

### A. ⚡ Scalping Signal (Target Hold: 1–5 Days)
- **Goal**: Capture quick 1–5 day momentum surges and volume spikes.
- **Stop Loss**: Tight adaptive stop ($1.5 \times \text{ATR}$, typically $2\text{–}4\%$).
- **Take Profit**: Quick target ($2.5 \times \text{ATR}$, typically $5\text{–}8\%$).
- **Time Exit**: Exits automatically after **5 trading days** if neither Stop nor TP is hit.

### B. 📈 Swing Signal (Target Hold: 10–20 Days)
- **Goal**: Ride multi-week trends on stocks confirmed above their 50-day Moving Average (MA50).
- **Stop Loss**: Dynamic adaptive stop ($3.0 \times \text{ATR}$, typically $5\text{–}8\%$).
- **Take Profit**: **No fixed target** — uses an **ATR Trailing Stop** ($\text{High Water Mark} - 2.2 \times \text{ATR}$) to let winners run.
- **Time Exit**: Exits automatically after **20 trading days** or when trailing stop triggers.

---

## 📱 2. How to Read Telegram Messages

### Sample Scalping Telegram Alert
```text
⚡ [SCALPING BUY] $BKSL  |  Confidence: 78/100
Timeframe: ⚡ Scalping Signal (1–5 Days)

Entry Zone: 59 – 61
Stop Loss:  56  (5.1%)
Take Profit: 66  |  R:R: 1.7

⚡ Scalping Signal (1–5 Days): Volume Divergence bullish signal

💡 AI Analysis (Bahasa Indonesia):
Saham BKSL menunjukkan akumulasi volume signifikan selama 2 hari berturut-turut.
Volume hari ini 1.8x dari rata-rata 5 hari. Disarankan entri pada area 59 - 61.

Market: bull  |  Cap: mid
Expected Resolution Window: 2 — 5 trading days
```

### Key Elements to Inspect
1. **Header Badge**: `⚡ [SCALPING BUY]` vs `📈 [SWING BUY]`.
2. **Confidence Score**: Scale of 0–100 (scores $\ge 50$ pass filter).
3. **Entry Zone**: Suggested buy price range (Current Price $\pm 1\%$).
4. **Stop Loss**: Recommended exit price if trade moves against you.
5. **AI Analysis**: Concise technical explanation in Bahasa Indonesia.

---

## 🕒 3. Daily Operating Procedures (SOP)

### Recommended Routine (Run Mon–Fri after market close or at 15:30 WIB)

```bash
# Step 1: Open Terminal & Navigate to Project Directory
cd ~/Developer/Web/stock\ signal
source .venv/bin/activate

# Step 2: Fetch Latest Market Prices (Downloads daily OHLCV CSVs)
python run.py fetch

# Step 3: Run Paper Trading Cycle (Processes exits & scans new entries)
python run.py paper

# Step 4: Check Open Positions & Performance
python run.py paper-status
```

### Pre-Closing Execution Option (15:30 WIB)
If you want to scan and enter positions at closing prices **before the 16:00 WIB market close**:
```bash
python run.py paper --preclose
```

---

## 🛡️ 4. Risk & Portfolio Management Rules

To protect your capital, the system automatically enforces 5 safety rules:

1. **Long-Only Mode**: Only generates **BUY** signals (SELL signals are safely skipped for spot trading compatibility).
2. **IHSG Market Trend Benchmark**: New signals are automatically **paused** if IHSG Composite Index falls below its 50-day moving average (protecting capital during market drawdowns).
3. **Relative Strength ($RS_{20}$)**: Stock 20-day return must outperform IHSG by at least 5% ($RS_{20} \ge 1.05$).
4. **Volatility Parity Sizing**: Dynamic position sizing sizes shares by $1 / \text{ATR}$ so that every trade risks exactly **1% of account equity**.
5. **Max 10 Concurrent Positions**: Portfolio limits open trades to a maximum of 10 positions at any time.

---

## 💻 5. CLI Command Reference Table

| Command | Action |
|---|---|
| `python run.py fetch` | Download/update latest daily prices from Yahoo Finance into `data/` |
| `python run.py paper` | Execute T6 paper trading cycle (check exits & record new entries) |
| `python run.py paper --preclose` | Execute pre-close scan at 15:30 WIB before market close |
| `python run.py paper-status` | Display paper portfolio status, open positions, and P&L metrics |
| `python run.py signal` | Scan for signals and print results to terminal (no DB write) |
| `python run.py weekly` | Generate and send weekly performance summary report to Telegram |
| `python run.py health` | Run 10 diagnostic system health checks |
| `python run.py export` | Export signals & paper trades database to CSV files in `exports/` |
| `python run.py test` | Send test message to Telegram to verify bot connectivity |

---

## ❓ 6. Frequently Asked Questions (FAQ)

### Q1: Why are there no signals on some days?
**Answer**: This is completely normal. The system is designed to be highly selective (~20–25 quality signals per year). No signal means no stock currently satisfies all strict technical criteria (MA trend + volume ratio + IHSG benchmark).

### Q2: Why does the terminal say `SELL skipped (long-only mode)`?
**Answer**: The indicator detected a bearish/distribution pattern on that stock. Because `LONG_ONLY_MODE = True`, the system safely discards short/sell signals so you only receive actionable BUY alerts.

### Q3: How do penny stocks work in this system?
**Answer**: Stocks priced between Rp 50 and Rp 500 are fully included in the `IDX200` scan, provided they meet the minimum liquidity requirement (Average Daily Volume $\ge \text{Rp 5 Billion}$).
