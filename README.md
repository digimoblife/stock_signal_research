# IDX Stock Signal Research System

Automated quantitative research, signal generation, and paper-trading engine for Indonesian Stock Exchange (IDX) spot equities. Generates real-time daily trading signals delivered to Telegram with structured AI-based explanations in Bahasa Indonesia.

Built for a multi-month paper trading experiment evaluating volume divergence, trend filtering, and volatility-adjusted risk management under live market conditions.

---

## 🔥 Key Features

- **Expanded Stock Universe (`IDX200`)** — Scans **179 liquid constituents** across banking, energy, mining, consumer, tech, healthcare, and property sectors.
- **Penny Stock Support** — Allows scanning penny stocks down to **Rp 50** with 20-day Average Daily Volume ($\text{ADV}_{20} \ge \text{Rp 5B}$).
- **Dual Signal System (Scalping vs. Swing)**:
  - **⚡ `[SCALPING BUY]`** (Hold 1–5 Days): Quick momentum setup with $1.5 \times \text{ATR}$ stop loss and $2.5 \times \text{ATR}$ take profit.
  - **📈 `[SWING BUY]`** (Hold 10–20 Days): High-conviction trend setup with $3.0 \times \text{ATR}$ stop loss and $\text{HWM} - 2.2 \times \text{ATR}$ dynamic trailing stop.
- **IHSG Market Trend Benchmark Filter** — Automatically pauses new long signals when the IHSG Composite Index is below its 50-day SMA.
- **Relative Strength ($RS_{20}$)** — Filters for stocks outperforming the IHSG benchmark ($RS_{20} \ge 1.05$).
- **Foreign Net Flow / Bandarmology Scoring** — Adds a +15 point confidence bonus when 5-day foreign net accumulation is detected.
- **Volatility Parity Position Sizing (Risk Parity)** — Sizes positions dynamically by $1 / \text{ATR}$ so each trade risks exactly 1% of account equity.
- **Pre-Closing Execution Scan (15:30 WIB)** — Supports same-day execution before market close via `python run.py paper --preclose`.
- **AI Explanations in Bahasa Indonesia** — Rule-based explainer engine producing clear signal insights without requiring paid LLM API calls.
- **Telegram Notifications & Health Monitoring** — Real-time signal delivery, duplicate reminders, 10 automated health checks, and weekly performance summaries.
- **Long-Only Mode** — Retail-compliant spot trading filter (BUY signals only; short/sell signals safely skipped).

---

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/digimoblife/stock_signal_research.git
cd stock_signal_research

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Set up Telegram credentials
cp .env.example .env
# Edit .env with your credentials:
#   TELEGRAM_BOT_TOKEN — get from @BotFather on Telegram
#   TELEGRAM_CHAT_ID  — get from @userinfobot or @getidsbot

# Test Telegram connection
python run.py test

# Download / update price data for all 179 tickers
python run.py fetch

# Run T6 paper trading cycle (processes exits & scans new entries)
python run.py paper

# Check open paper positions and performance stats
python run.py paper-status
```

---

## 💻 Commands Reference

| Command | Description |
|---|---|
| `python run.py fetch` | Download/update historical price data for all 179 tickers in `IDX200` |
| `python run.py paper` | Run daily paper trading cycle (checks exits & enters new Scalping/Swing setups) |
| `python run.py paper --preclose` | Run pre-close execution scan at 15:30 WIB before market close |
| `python run.py paper-status` | Display paper portfolio metrics (Win Rate, Profit Factor, Open Positions) |
| `python run.py signal` | Generate signals and print to console with `[SCALPING]` and `[SWING]` labels |
| `python run.py weekly` | Send weekly paper trading performance summary report to Telegram |
| `python run.py health` | Run 10 system diagnostic health checks |
| `python run.py export` | Export database records (`signals.db`, `paper_trades.db`) to CSV files in `exports/` |
| `python run.py test` | Send a test message to verify Telegram bot connectivity |
| `python run.py research` | Rerun multi-strategy historical backtest & ranking |

---

## ⚙️ Configuration (`settings.py`)

Key settings in [`settings.py`](file:///Users/cahyo/Developer/Web/stock%20signal/settings.py):

```python
STOCK_UNIVERSE = "idx200"            # Options: 'lq45', 'idx80', 'kompas100', 'idx200', 'custom'
MIN_PRICE = 50                       # Minimum price floor (IDR) — allows penny stocks down to Rp 50
MIN_ADV = 5_000_000_000              # Minimum 20-day Average Daily Volume (IDR 5 Billion)

# Scalping vs Swing Setup
SCALPING_MAX_HOLD_DAYS = 5           # 1-5 Days hold
SCALPING_STOP_ATR = 1.5              # 1.5x ATR stop loss
SCALPING_TAKE_PROFIT_ATR = 2.5       # 2.5x ATR take profit

SWING_MAX_HOLD_DAYS = 20             # 10-20 Days hold
SWING_STOP_ATR = 3.0                 # 3.0x ATR stop loss
T6_TRAILING_STOP_ENABLED = True      # ATR Trailing stop at HWM - 2.2x ATR

# Quality & Market Filters
T6_USE_MARKET_FILTER = True          # Require IHSG > 50-day SMA
T6_MIN_RS20 = 1.05                   # Require stock 20d return beat IHSG by >= 5%
T6_RISK_PARITY_ENABLED = True        # Volatility Sizing (1/ATR)
LONG_ONLY_MODE = True                # BUY signals only (Long-only)
```

---

## 📂 Project Documentation

- **[`USER_GUIDE.md`](file:///Users/cahyo/Developer/Web/stock%20signal/USER_GUIDE.md)** — Comprehensive operating guide, Telegram alert reading manual, and FAQ.
- **[`PROJECT_ANALYSIS.md`](file:///Users/cahyo/Developer/Web/stock%20signal/PROJECT_ANALYSIS.md)** — Architectural blueprint, module map, and quantitative strategy specifications.
- **[`IMPLEMENTATION_PLAN.md`](file:///Users/cahyo/Developer/Web/stock%20signal/IMPLEMENTATION_PLAN.md)** — 3-Phase technical implementation roadmap.
- **[`WALKTHROUGH.md`](file:///Users/cahyo/Developer/Web/stock%20signal/WALKTHROUGH.md)** — Verification audit logs, empirical backtest results, and sample trade logs.
- **[`DAILY_OPERATION_GUIDE.md`](file:///Users/cahyo/Developer/Web/stock%20signal/DAILY_OPERATION_GUIDE.md)** — Daily, weekly, and monthly operating routines.

---

## 📄 License

Personal quantitative research project. Not intended for commercial use.
