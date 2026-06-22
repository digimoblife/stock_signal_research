"""
universe.py — stock universe definitions + liquidity filtering.

Single source of truth for which tickers to scan, fetch, and trade.
All modules should import from here instead of settings.TICKERS directly.

Usage:
    from universe import get_eligible_tickers, get_universe
    tickers = get_eligible_tickers()        # liquidity-filtered list
    raw    = get_universe("LQ45")           # raw index constituents
"""
import logging
from pathlib import Path

import pandas as pd

import settings as _settings

log = logging.getLogger("universe")

# ── Index constituents (official lists, Kontan — May/Jul 2026) ──

# LQ45 — period 04 May 2026 – 31 Jul 2026
LQ45 = sorted([
    "AADI", "ADMR", "ADRO", "AKRA", "AMMN", "AMRT", "ANTM", "ASII",
    "BBCA", "BBNI", "BBRI", "BBTN", "BMRI", "BRPT", "BUMI", "CPIN",
    "CUAN", "DEWA", "EMTK", "ESSA", "EXCL", "GOTO", "HRTA", "ICBP",
    "INCO", "INDF", "INKP", "ISAT", "ITMG", "JPFA", "KLBF", "MAPI",
    "MBMA", "MDKA", "MEDC", "PGAS", "PGEO", "PTBA", "SCMA", "SMGR",
    "TLKM", "TOWR", "UNTR", "UNVR", "WIFI",
])

# IDX80 — period 04 May 2026 – 31 Jul 2026
IDX80 = sorted([
    "AADI", "ACES", "ADMR", "ADRO", "AKRA", "AMMN", "AMRT", "ANTM",
    "ARTO", "ASII", "BBCA", "BBNI", "BBRI", "BBTN", "BKSL", "BMRI",
    "BRMS", "BRPT", "BSDE", "BUKA", "BUMI", "CBDK", "CMRY", "CPIN",
    "CTRA", "CUAN", "DEWA", "DSNG", "ELSA", "EMTK", "ENRG", "ERAA",
    "ESSA", "EXCL", "GGRM", "GOTO", "HEAL", "HRTA", "HRUM", "ICBP",
    "INCO", "INDF", "INDY", "INKP", "INTP", "ISAT", "ITMG", "JPFA",
    "JSMR", "KIJA", "KLBF", "KPIG", "MAPA", "MAPI", "MBMA", "MDKA",
    "MEDC", "MIKA", "MYOR", "PANI", "PGAS", "PGEO", "PNLF", "PTBA",
    "PTRO", "PWON", "RAJA", "RATU", "SCMA", "SIDO", "SMGR", "SMRA",
    "SSIA", "TAPG", "TLKM", "TOWR", "TPIA", "UNTR", "UNVR", "WIFI",
])

# Kompas100 — period 02 Feb 2026 – 31 Jul 2026
KOMPAS100 = sorted([
    "AADI", "ACES", "ADMR", "ADRO", "AKRA", "AMMN", "AMRT", "ANTM",
    "ARCI", "ARTO", "ASII", "BBCA", "BBNI", "BBRI", "BBTN", "BBYB",
    "BKSL", "BMRI", "BREN", "BRIS", "BRMS", "BRPT", "BSDE", "BTPS",
    "BUKA", "BULL", "BUMI", "BUVA", "CBDK", "CMRY", "CPIN", "CTRA",
    "CUAN", "DEWA", "DSNG", "DSSA", "ELSA", "EMTK", "ENRG", "ERAA",
    "ESSA", "EXCL", "FILM", "GOTO", "HEAL", "HMSP", "HRTA", "HRUM",
    "ICBP", "IMPC", "INCO", "INDF", "INDY", "INET", "INKP", "INTP",
    "ISAT", "ITMG", "JPFA", "JSMR", "KIJA", "KLBF", "KPIG", "MAPA",
    "MAPI", "MBMA", "MDKA", "MEDC", "MIKA", "MTEL", "MYOR", "NCKL",
    "PANI", "PGAS", "PGEO", "PNLF", "PSAB", "PTBA", "PTRO", "PWON",
    "RAJA", "RATU", "SCMA", "SGER", "SIDO", "SMGR", "SMIL", "SMRA",
    "SSIA", "TAPG", "TCPI", "TINS", "TLKM", "TOBA", "TOWR", "TPIA",
    "UNTR", "UNVR", "WIFI", "WIRG",
])

# ── Validate index constituent counts ────────────────────────────

_EXPECTED_COUNTS = {"LQ45": 45, "IDX80": 80, "KOMPAS100": 100}
for _name, _actual in [("LQ45", len(LQ45)), ("IDX80", len(IDX80)), ("KOMPAS100", len(KOMPAS100))]:
    _expected = _EXPECTED_COUNTS[_name]
    if _actual != _expected:
        log.warning(
            f"{_name} has {_actual} constituents, expected {_expected}. "
            "Trading decisions based on this index may be unreliable."
        )

# ── Universe name → ticker list ──────────────────────────────────

UNIVERSE_MAP = {
    "lq45": LQ45,
    "idx80": IDX80,
    "kompas100": KOMPAS100,
    "custom": None,  # loaded from settings.TICKERS at runtime
}


def get_universe(name: str = None):
    """
    Return the raw ticker list for a given universe name.

    name: one of 'lq45', 'idx80', 'kompas100', 'custom'.
          If None, uses settings.STOCK_UNIVERSE.
    """
    if name is None:
        name = _settings.STOCK_UNIVERSE

    key = name.lower().replace(" ", "").replace("-", "")
    if key == "custom":
        from settings import TICKERS
        return list(TICKERS)

    tickers = UNIVERSE_MAP.get(key)
    if tickers is None:
        log.warning(f"Unknown universe '{name}', falling back to custom")
        from settings import TICKERS
        return list(TICKERS)

    return list(tickers)


# ── Liquidity filter ────────────────────────────────────────────

def filter_liquidity(tickers, data_dir=None):
    """
    Filter tickers by minimum price and average daily value (ADV).

    Checks:
      - Latest close price >= MIN_PRICE (500 IDR)
      - Average daily traded value over 20 days >= MIN_ADV (5B IDR)
        ADV = close_price * volume, averaged over trailing 20 trading days

    Returns: (eligible, skipped_no_data, skipped_liquidity)
    """
    if data_dir is None:
        data_dir = Path(_settings.DATA_DIR)

    eligible = []
    no_data = []
    liquidity_fail = []

    for ticker in tickers:
        path = data_dir / f"{ticker}.csv"
        if not path.exists():
            no_data.append(ticker)
            continue

        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.empty or len(df) < 30:
            no_data.append(ticker)
            continue

        # Latest close price
        last_close = df["close"].iloc[-1]
        if pd.isna(last_close) or last_close < _settings.MIN_PRICE:
            liquidity_fail.append((ticker, f"price {last_close:.0f} < {_settings.MIN_PRICE}"))
            continue

        # Average daily value over trailing 20 days
        recent = df.tail(20)
        adv = (recent["close"] * recent["volume"]).mean()
        if pd.isna(adv) or adv < _settings.MIN_ADV:
            adv_str = f"{adv:,.0f}" if not pd.isna(adv) else "NaN"
            liquidity_fail.append((ticker, f"ADV {adv_str} < {_settings.MIN_ADV:,}"))
            continue

        eligible.append(ticker)

    return eligible, no_data, liquidity_fail


def get_eligible_tickers(data_dir=None):
    """
    One-call convenience: get raw universe → apply liquidity filter.
    Returns (eligible, counts_dict) where counts_dict has:
        scanned, no_data, liquidity_fail, eligible
    """
    all_tickers = get_universe()
    scanned = len(all_tickers)
    eligible, no_data, liq_fail = filter_liquidity(all_tickers, data_dir)

    counts = {
        "scanned": scanned,
        "eligible": len(eligible),
        "no_data": len(no_data),
        "liquidity_fail": len(liq_fail),
    }

    log.info(
        f"Universe scan: {scanned} scanned, {len(no_data)} missing data, "
        f"{len(liq_fail)} liquidity fail, {len(eligible)} eligible"
    )
    if no_data:
        log.info(f"  Missing data: {', '.join(no_data)}")
    if liq_fail:
        for t, reason in liq_fail[:5]:
            log.info(f"  Liquidity fail: {t} — {reason}")

    return eligible, counts


# ── Direct test ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"LQ45: {len(LQ45)} tickers")
    print(f"IDX80: {len(IDX80)} tickers")
    print(f"KOMPAS100: {len(KOMPAS100)} tickers")
    print(f"Custom: {len(get_universe('custom'))} tickers")
    print()
    eligible, counts = get_eligible_tickers()
    print(f"\nFinal: {counts['eligible']} eligible / {counts['scanned']} scanned")
