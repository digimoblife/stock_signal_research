# settings.py — single source of every configuration value
# Change NOTHING else in the codebase. Only edit this file.

import os
from dotenv import load_dotenv

load_dotenv()

# --- Tokens (from .env) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Stock universe ---
# Options for STOCK_UNIVERSE: 'lq45', 'idx80', 'kompas100', 'idx200', 'custom'
STOCK_UNIVERSE = "idx200"

TICKERS = [
    "AADI", "ACES", "ADMR", "ADRO", "AGRO", "AKRA", "AMMN", "AMRT", "ANTM", "ARCI",
    "ARTO", "ASII", "AUTO", "BACA", "BANK", "BBCA", "BBHI", "BBNI", "BBRI", "BBTN",
    "BBYB", "BDMN", "BEST", "BHIT", "BIRD", "BJBR", "BJTM", "BKSL", "BMRI", "BMTR",
    "BNGA", "BNLI", "BREN", "BRIS", "BRMS", "BRPT", "BSDE", "BSIM", "BTPN", "BTPS",
    "BUKA", "BULL", "BUMI", "BUVA", "CASA", "CBDK", "CENT", "CITA", "CLEO", "CMRY",
    "CPIN", "CTRA", "CUAN", "DEWA", "DILD", "DMAS", "DOID", "DRMA", "DSNG", "DSSA",
    "DUTI", "ELSA", "EMTK", "ENRG", "ERAA", "ESSA", "EXCL", "FAST", "FILM", "FISH",
    "FPNI", "GGRM", "GJTL", "GOOD", "GOTO", "HEAL", "HMSP", "HRTA", "HRUM", "ICBP",
    "IGAR", "IMAS", "IMPC", "INAF", "INCO", "INDF", "INDY", "INET", "INKP", "INPP",
    "INTP", "IPCC", "IRRA", "ISAT", "ITMG", "JPFA", "JSMR", "KAEF", "KBLI", "KIJA",
    "KINO", "KLBF", "KPIG", "LINK", "LPCK", "LPKR", "LTLS", "MAIN", "MAPA", "MAPI",
    "MASA", "MBMA", "MCAS", "MCOL", "MDKA", "MDLN", "MEDC", "MIKA", "MLBI", "MLIA",
    "MPMX", "MTEL", "MTLA", "MYOR", "NCKL", "NISP", "PALM", "PANI", "PEHA", "PGAS",
    "PGEO", "PNLF", "PPRO", "PRDA", "PSAB", "PTBA", "PTRO", "PWON", "RAJA", "RALS",
    "RATU", "RDTX", "ROTI", "SAME", "SAMF", "SCMA", "SGER", "SIDO", "SILO", "SIMP",
    "SMBR", "SMGR", "SMIL", "SMRA", "SMSM", "SPTO", "SRTG", "SSIA", "SSMS", "STAA",
    "TAPG", "TBIG", "TCPI", "TINS", "TKIM", "TLKM", "TOBA", "TOTL", "TOWR", "TPIA",
    "TSPC", "ULTJ", "UNIC", "UNTR", "UNVR", "VICI", "WIFI", "WIRG", "WOOD", "ZINC"
]


# --- Liquidity filter thresholds ---
MIN_PRICE = 50            # Minimum latest close price (IDR) — allows penny stocks down to Rp 50
MIN_ADV = 5_000_000_000   # Minimum average daily value over 20d (IDR)

# --- Dual Signal Types: Scalping vs Swing ---
SCALPING_ENABLED = True
SCALPING_MAX_HOLD_DAYS = 5
SCALPING_STOP_ATR = 1.5
SCALPING_TAKE_PROFIT_ATR = 2.5
SCALPING_MIN_VOL_RATIO = 1.3

SWING_ENABLED = True
SWING_MAX_HOLD_DAYS = 20
SWING_STOP_ATR = 3.0
SWING_MIN_VOL_RATIO = 2.0


# Where data lives
DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "signals.db") if DATA_DIR != "data" else "signals.db")

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

# --- T6_TREND_FILTERED strategy (IDX200) ---
T6_ENABLED = True
T6_STOCK_UNIVERSE = "idx200"

T6_STOP_ATR = 3.0
T6_TAKE_PROFIT_ATR = None
T6_MAX_HOLD_DAYS = 20
T6_MIN_VOL_RATIO = 2.0
T6_STOCK_MA_PERIOD = 50
T6_MAX_POSITIONS = 10
T6_STARTING_CAPITAL = 100_000_000
T6_COST_PER_TRADE = 0.006

# --- T6 Enhancements (Quality Filters, Dynamic Exits, Risk Parity) ---
T6_USE_MARKET_FILTER = True          # Require IHSG > MA50
T6_MARKET_MA_PERIOD = 50
T6_MIN_RS20 = 1.05                   # Stock 20d return beat IHSG by >= 5%
T6_FOREIGN_FLOW_ENABLED = True
T6_FOREIGN_FLOW_BONUS = 15           # Confidence score bonus for 5d foreign net buy

T6_PARTIAL_TP_ENABLED = True         # Partial profit taking (50% position)
T6_PARTIAL_TP_ATR_MULT = 2.0         # Sell 50% at +2.0 ATR
T6_TRAILING_STOP_ENABLED = True
T6_TRAILING_STOP_ATR_MULT = 2.2      # Trail distance: High Water Mark - 2.2 ATR

T6_RISK_PARITY_ENABLED = True        # Volatility Sizing (1/ATR)
T6_RISK_PER_TRADE_PCT = 0.01         # 1% equity risk per trade

# --- Paper tracking ---
PAPER_DB_PATH = os.getenv("PAPER_DB_PATH", os.path.join(DATA_DIR, "paper_trades.db") if DATA_DIR != "data" else "paper_trades.db")

