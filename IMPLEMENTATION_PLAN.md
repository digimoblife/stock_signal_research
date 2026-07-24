# Implementation Plan — Penny Stock Support & Dual Signal System (Scalping vs Swing)

> **Document Location:** `/Users/cahyo/Developer/Web/stock signal/IMPLEMENTATION_PLAN.md`  
> **Target System:** IDX Stock Signal System

This implementation plan details enabling **Penny Stocks on IDX** (minimum price threshold Rp 50) and establishing a **Dual Signal Pipeline** classifying signals into **Scalping** (1–5 days) and **Swing** (10–20 days) with distinct Telegram message tags.

---

## 1. Plan Overview

1. **Penny Stock Access**: Change `MIN_PRICE` from IDR 500 to **IDR 50** in `settings.py` to allow penny stock setups.
2. **Dual Signal Types**:
   - ⚡ **Scalping Signal (1–5 Days)**: Fast volume/momentum spikes. Tighter stop ($1.5 \times \text{ATR}$), profit target ($2.5 \times \text{ATR}$), 5-day max holding.
   - 📈 **Swing Signal (10–20 Days)**: Trend-confirmed accumulation (Close > MA50). Trailing stop ($2.2 \times \text{ATR}$), 20-day max holding.
3. **Telegram Notification Formatting**: Every alert clearly displays whether it is a Scalping or Swing setup in the header badge.

---

## 2. Technical Modifications

### Component 1: Configuration (`settings.py`)
- Set `MIN_PRICE = 50` (Allow penny stocks down to Rp 50).
- Add parameters:
  ```python
  # Scalping parameters (1-5 Days)
  SCALPING_ENABLED = True
  SCALPING_MAX_HOLD_DAYS = 5
  SCALPING_STOP_ATR = 1.5
  SCALPING_TAKE_PROFIT_ATR = 2.5
  SCALPING_MIN_VOL_RATIO = 1.3

  # Swing parameters (10-20 Days)
  SWING_ENABLED = True
  SWING_MAX_HOLD_DAYS = 20
  SWING_STOP_ATR = 3.0
  SWING_MIN_VOL_RATIO = 2.0
  ```

### Component 2: Signal Classification (`gen_signal.py`)
- Categorize generated signals into `style = "SCALPING"` or `style = "SWING"`.
- Assign dynamic targets, stops, and holding period descriptors based on signal style.

### Component 3: Telegram Formatting (`telegram_sender.py`)
- Add distinct header icons and style tags:
  - `⚡ [SCALPING SIGNAL] BBCA — Target Hold: 1–5 Days`
  - `📈 [SWING SIGNAL] TLKM — Target Hold: 10–20 Days`

### Component 4: Paper Trading Tracking (`paper.py`)
- Process Scalping exits (5-day time stop or 2.5x ATR take profit) alongside Swing exits (20-day time stop or 2.2x ATR trailing stop).

---

## 3. Verification Plan

```bash
# Test signal generator and inspect penny stock & scalping/swing labels
python run.py signal

# Test paper trading execution cycle
python run.py paper

# Check open positions
python run.py paper-status
```
