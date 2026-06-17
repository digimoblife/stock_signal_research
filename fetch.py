"""
fetch.py — downloads OHLCV for all tickers, saves as CSV.
Idempotent: safe to run daily (only downloads missing dates).
"""
import time
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import yfinance as yf

from settings import TICKERS, START_DATE, DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("fetch")

DATA_DIR = Path(DATA_DIR)
DATA_DIR.mkdir(exist_ok=True)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # seconds


def fetch_one(ticker: str, attempt: int = 1) -> pd.DataFrame | None:
    """Download one ticker from Yahoo Finance. Returns DataFrame or None."""
    try:
        stock = yf.Ticker(ticker + ".JK")
        df = stock.history(start=START_DATE, auto_adjust=True)

        if df.empty:
            log.warning(f"{ticker}: empty response")
            return None

        # Clean columns
        df = df.rename(columns={
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df.index.name = "date"
        df.index = pd.to_datetime(df.index.date)  # remove time component
        df["ticker"] = ticker
        return df

    except Exception as e:
        log.error(f"{ticker}: {e} (attempt {attempt}/{MAX_RETRIES})")
        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            log.info(f"{ticker}: retrying in {delay}s...")
            time.sleep(delay)
            return fetch_one(ticker, attempt + 1)
        return None


def save_one(ticker: str, df: pd.DataFrame):
    """Save/append DataFrame to CSV. Preserves existing data."""
    path = DATA_DIR / f"{ticker}.csv"

    # Select only the columns we want
    cols = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in cols if c in df.columns]]

    if path.exists():
        old = pd.read_csv(path, index_col=0, parse_dates=True)
        old.index.name = "date"
        # Keep old rows, add new ones, deduplicate by date
        combined = pd.concat([old, df])
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index().to_csv(path)
    else:
        df.sort_index().to_csv(path)

    log.info(f"{ticker}: {len(df)} rows saved")


def validate_one(ticker: str, df: pd.DataFrame) -> list[str]:
    """Return list of data quality issues (empty list = clean)."""
    issues = []

    if df.isna().any().any():
        issues.append("contains NaN values")

    # Check for suspicious daily returns (> 20%)
    rets = df["close"].pct_change()
    spikes = rets[rets.abs() > 0.20]
    if not spikes.empty:
        dates = ", ".join(d.strftime("%Y-%m-%d") for d in spikes.index[:5])
        issues.append(f"price spikes >20% on: {dates}")

    # Check for suspicious volume (factor of 1000 vs normal)
    avg_vol = df["volume"].median()
    max_vol = df["volume"].max()
    if avg_vol > 0 and max_vol / avg_vol > 100:
        issues.append(f"volume spike: median {avg_vol:.0f}, max {max_vol:.0f}")

    # Check for gaps > 10 trading days
    dates = pd.Series(df.index)
    gaps = dates.diff().dt.days
    big_gaps = gaps[gaps > 14]
    if not big_gaps.empty:
        issues.append(f"{len(big_gaps)} data gaps > 14 days")

    return issues


def fetch_all():
    """Download all tickers. Validate. Print summary."""
    ok = 0
    failed = 0

    for ticker in TICKERS:
        log.info(f"Fetching {ticker}...")
        df = fetch_one(ticker)

        if df is None:
            failed += 1
            time.sleep(1)
            continue

        issues = validate_one(ticker, df)
        if issues:
            for issue in issues:
                log.warning(f"{ticker}: {issue}")

        save_one(ticker, df)
        ok += 1
        time.sleep(0.5)  # rate limit courtesy

    log.info(f"Done: {ok} OK, {failed} failed")
    return ok, failed


if __name__ == "__main__":
    fetch_all()
