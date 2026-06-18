# IDX Stock Signal Research System

Personal research system for detecting volume divergence signals on Indonesian (IDX) stocks. Generates daily trading signals via Telegram with AI-generated explanations in Bahasa Indonesia.

Built for a 6-month paper trading experiment to evaluate whether the volume divergence methodology has a real edge in live conditions.

## Features

- **Volume divergence detection** — identifies accumulation/distribution patterns across 15 LQ45 stocks
- **Backtesting engine** — 4 strategies compared, 893 historical volume divergence trades analyzed
- **Confidence scoring** — regime × liquidity × divergence-strength filter pipeline
- **Holding period statistics** — multi-outcome distribution (TP hit / stop hit / expired) from historical data
- **AI explanations** — template-based signal analysis in Bahasa Indonesia (no LLM API calls)
- **Telegram delivery** — daily signals sent to your Telegram at market close
- **Health monitoring** — 10 automated health checks, weekly/monthly performance reports
- **CSV export** — signals, trades, weekly/monthly summaries for spreadsheet analysis
- **Long-only mode** — BUY signals only; SELL (short) signals filtered out for retail suitability
- **One batch per day** — prevents duplicate signal generation

## Prerequisites

- Python 3.10+
- Telegram account

## Quick Start

```bash
# Clone the repository
git clone https://github.com/digimoblife/stock_signal_research.git
cd stock_signal_research

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Set up Telegram credentials
cp .env.example .env
# Edit .env with your credentials:
#   TELEGRAM_BOT_TOKEN — get from @BotFather on Telegram
#   TELEGRAM_CHAT_ID  — get from @userinfobot or @getidsbot

# Test Telegram connection
python run.py test

# Run backtest and rank strategies
python run.py research

# Run daily cycle (fetch data → generate signals → send Telegram)
python run.py daily

# Export data to CSV
python run.py export
```

## Commands

| Command | Description |
|---------|-------------|
| `python run.py research` | Run full backtest, rank all 4 strategies, save trades to DB |
| `python run.py daily` | Full daily cycle: fetch prices → generate signals → send Telegram |
| `python run.py signal` | Generate signals and print to console (no Telegram, no DB save) |
| `python run.py fetch` | Download/update price data for all tickers |
| `python run.py open` | List signals without a resolved trade |
| `python run.py performance` | Print P&L report (win rate, Sharpe, drawdown) and send to Telegram |
| `python run.py health` | Run 10 system health checks, report to Telegram |
| `python run.py export` | Export DB to CSV (signals, trades, weekly/monthly summaries) |
| `python run.py test` | Send a test message to verify Telegram connectivity |
| `python run.py init` | Initialize SQLite database and create tables |

## Configuration

All configuration lives in `settings.py`:

- **TICKERS** — stock universe (15 LQ45 constituents)
- **MIN_CONFIDENCE** — minimum confidence score to emit a signal (default: 50)
- **MAX_DAILY_SIGNALS** — maximum signals per day (default: 3)
- **MIN_RISK_REWARD** — minimum risk-reward ratio (default: 1.5)
- **ONE_DAILY_BATCH** — block duplicate daily runs (default: True)
- **LONG_ONLY_MODE** — filter out SELL signals (default: True)

## Telegram credentials (never committed)

```text
.env          ← contains real token and chat ID (gitignored)
.env.example  ← template with placeholder values (committed)
```

## Stock Universe

BBCA, BBRI, BMRI, BBNI, TLKM, ASII, ADRO, ICBP, INDF, UNVR, GGRM, HMSP, KLBF, SMGR, PGAS

## Project Structure

```
├── settings.py           # Single configuration file
├── run.py                # CLI entry point (10 commands)
├── fetch.py              # Yahoo Finance data downloader (3 retries, backoff)
├── research.py           # 4 backtest strategies + trade persistence
├── gen_signal.py         # Daily signal generation with AI explanations
├── filter.py             # Regime classification, liquidity bucketing, confidence scoring
├── track.py              # SQLite persistence (signals + trades tables)
├── telegram_sender.py    # Telegram message formatting + delivery (3 retries)
├── ai_explain.py         # Template-based AI explanations in Bahasa Indonesia
├── export.py             # CSV export (5 report files)
├── monitor.py            # 10 health checks + rolling metrics
├── audit.py              # Robustness criteria stress tests
├── analyze.py            # Strategy analysis utilities
├── scripts/
│   └── deploy_vps.sh     # One-command VPS setup
├── data/                 # Downloaded OHLCV CSV files (gitignored)
├── exports/              # Generated CSV reports (gitignored)
├── .env                  # Telegram credentials (gitignored)
├── .env.example          # Credential template (committed)
└── requirements.txt      # Python dependencies
```

## VPS Deployment

See `VPS_DEPLOYMENT.md` for a step-by-step beginner-friendly runbook. The `scripts/deploy_vps.sh` script automates:
- Dependency installation
- Cron setup (daily signal at 17:30 WIB, health check, weekly report)
- Log rotation (30-day retention)
- Backup system (7-day + monthly snapshots)
- UFW firewall configuration

## License

Personal research project. Not for commercial use.
