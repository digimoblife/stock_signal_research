# settings.py — single source of every configuration value
# Change NOTHING else in the codebase. Only edit this file.

# --- Tokens ---
TELEGRAM_BOT_TOKEN = "8761189242:AAGn6MLlFeW9NfmoOOSNf8lT9EeHo2z4GD0"
TELEGRAM_CHAT_ID = "6394819718"

# --- Stock universe ---
TICKERS = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM",
    "ASII", "ADRO", "ICBP", "INDF", "UNVR",
    "GGRM", "HMSP", "KLBF", "SMGR", "PGAS",
]

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

# --- Paper trading controls ---
ONE_DAILY_BATCH = True
LONG_ONLY_MODE = True
