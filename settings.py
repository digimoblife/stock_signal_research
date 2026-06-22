# settings.py — single source of every configuration value
# Change NOTHING else in the codebase. Only edit this file.

import os
from dotenv import load_dotenv

load_dotenv()

# --- Tokens (from .env) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Stock universe ---
# Options for STOCK_UNIVERSE: 'lq45', 'idx80', 'kompas100', 'custom'
# 'custom' uses the TICKERS list below (legacy behavior).
STOCK_UNIVERSE = "custom"

TICKERS = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM",
    "ASII", "ADRO", "ICBP", "INDF", "UNVR",
    "GGRM", "HMSP", "KLBF", "SMGR", "PGAS",
]

# --- Liquidity filter thresholds ---
MIN_PRICE = 500           # Minimum latest close price (IDR)
MIN_ADV = 5_000_000_000   # Minimum average daily value over 20d (IDR)

# Where data lives
DATA_DIR = "data"
DB_PATH = "signals.db"

# --- Data ---
START_DATE = "2018-01-01"
END_DATE = None  # None = up to latest available

# --- Backtest ---
MIN_TRADES = 10  # minimum trades to consider a strategy viable
TEST_START = "2023-01-01"  # last 2 years for testing
TRAIN_START = "2018-01-01"
TRAIN_END = "2022-12-31"

# --- Costs (realistic for IDX retail) ---
SLIPPAGE = 0.0015     # 0.15% per trade
BROKER_FEE = 0.0035   # 0.35% round trip
STAMP_DUTY = 0.001    # 0.10% for >IDR 10M
TOTAL_COST = SLIPPAGE + BROKER_FEE + STAMP_DUTY  # ~0.6% round trip

# --- Signal filters ---
MIN_CONFIDENCE = 50
MAX_DAILY_SIGNALS = 3
MIN_RISK_REWARD = 1.5

# --- C_BALANCED strategy parameters ---
STOP_ATR = 3.0              # Stop loss in ATR units (was 2.0)
TAKE_PROFIT_ATR = None      # None = no take profit, let winners run (was 4.0)
MAX_HOLD_DAYS = 15          # Maximum holding period in trading days (was 5)
MIN_VOL_RATIO = 2.0         # Minimum volume ratio for signal consideration

# --- Paper trading controls ---
ONE_DAILY_BATCH = True
LONG_ONLY_MODE = True

# --- T6_TREND_FILTERED strategy (IDX80) ---
T6_ENABLED = True
T6_STOCK_UNIVERSE = "idx80"
T6_STOP_ATR = 3.0
T6_TAKE_PROFIT_ATR = None
T6_MAX_HOLD_DAYS = 20
T6_MIN_VOL_RATIO = 2.0
T6_STOCK_MA_PERIOD = 50
T6_MAX_POSITIONS = 10
T6_STARTING_CAPITAL = 100_000_000
T6_COST_PER_TRADE = 0.006

# --- Paper tracking ---
PAPER_DB_PATH = "paper_trades.db"
