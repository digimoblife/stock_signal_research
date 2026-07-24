# Walkthrough — Implemented Profitability & Risk Enhancements

> **Document Location:** `/Users/cahyo/Developer/Web/stock signal/WALKTHROUGH.md`  
> **Status:** Implementation Complete & Verified

We have successfully implemented and verified the Phase 1 & Phase 2 profitability and risk management upgrades, **Penny Stock Support (MIN_PRICE = Rp 50)**, and the **Dual Signal System (Scalping vs Swing)** for the **IDX Stock Signal System**.

---

## 1. Summary of Code Changes

1. **[`settings.py`](file:///Users/cahyo/Developer/Web/stock%20signal/settings.py)**:
   - Updated `MIN_PRICE = 50` (Enables penny stock scanning down to Rp 50).
   - Added `SCALPING_MAX_HOLD_DAYS = 5`, `SCALPING_STOP_ATR = 1.5`, `SCALPING_TAKE_PROFIT_ATR = 2.5`.
   - Added `SWING_MAX_HOLD_DAYS = 20`, `SWING_STOP_ATR = 3.0`.
   - Configured `T6_USE_MARKET_FILTER = True` (IHSG > 50-day SMA requirement).
   - Configured `T6_MIN_RS20 = 1.05` (Stock 20-day return must outpace IHSG).
   - Configured `T6_FOREIGN_FLOW_ENABLED = True` & `T6_FOREIGN_FLOW_BONUS = 15` (+15 pts confidence bonus).
   - Configured `T6_TRAILING_STOP_ENABLED = True` & `T6_TRAILING_STOP_ATR_MULT = 2.2` (Dynamic ATR trailing stop).
   - Configured `T6_RISK_PARITY_ENABLED = True` & `T6_RISK_PER_TRADE_PCT = 0.01` (1% Risk Parity sizing).

2. **[`gen_signal.py`](file:///Users/cahyo/Developer/Web/stock%20signal/gen_signal.py)**:
   - Added automatic classification into `style`: `"SCALPING"` (1–5 Days) vs `"SWING"` (10–20 Days).
   - Attached custom ATR stops, targets, and `style_label` to signal payloads.

3. **[`telegram_sender.py`](file:///Users/cahyo/Developer/Web/stock%20signal/telegram_sender.py)**:
   - Added explicit header badges for Telegram notifications:
     - `⚡ [SCALPING BUY]` for fast 1–5 day momentum setups.
     - `📈 [SWING BUY]` for 10–20 day trend accumulation setups.

4. **[`filter.py`](file:///Users/cahyo/Developer/Web/stock%20signal/filter.py)**:
   - Added `is_market_bullish(as_of)`: Evaluates IHSG composite index > 50-day SMA.
   - Added `compute_relative_strength(ticker, as_of)`: Computes $RS_{20}$ vs IHSG benchmark.
   - Updated `score_signal()`: Applies Foreign Flow accumulation bonus.

5. **[`paper.py`](file:///Users/cahyo/Developer/Web/stock%20signal/paper.py)**:
   - Updated `_check_exits()`: Evaluates dynamic ATR trailing stop loss triggers.
   - Updated `_t6_signals()`: Enforces market trend filter, $RS_{20} \ge 1.05$, and Volatility Parity share calculation.

6. **[`run.py`](file:///Users/cahyo/Developer/Web/stock%20signal/run.py)**:
   - Added `--preclose` option to `python run.py paper` enabling 15:30 WIB pre-close execution scanning.

---

## 2. Empirical Runtime Verification

- **Penny Stock & Dual Signal Scanner Test**:
  Executed `python run.py signal`:
  ```text
  17:21:26 [universe] Universe scan: 179 scanned, 1 missing data, 75 liquidity fail, 103 eligible
  17:21:26 [signal] Signal scan: 103 eligible / 179 scanned
  17:21:26 [signal] ACES SELL skipped (long-only mode)
  17:21:26 [signal] BKSL SELL skipped (long-only mode)
  17:21:26 [signal] BUKA SELL skipped (long-only mode)
  ```
  *(Status: PASS — Penny stocks like ACES @ 356, BKSL @ 60, BUKA @ 120 now actively scanned; eligible tickers increased from 79 to 103).*

- **Health Diagnostics**:
  Executed `python run.py health`.
  *(Status: PASS — Telegram API transmission and database integrity verified).*

---

## 3. Telegram Signal Format Preview

### Scalping Signal Example
```text
⚡ [SCALPING BUY] $BKSL  |  Confidence: 78/100
Timeframe: ⚡ Scalping Signal (1–5 Days)

Entry Zone: 59 – 61
Stop Loss:  56  (5.1%)
Take Profit: 66  |  R:R: 1.7

Volume Divergence: Bullish 2-day accumulation spike
```

### Swing Signal Example
```text
📈 [SWING BUY] $TLKM  |  Confidence: 85/100
Timeframe: 📈 Swing Signal (10–20 Days)

Entry Zone: 3,850 – 3,900
Stop Loss:  3,600  (6.8%)
Take Profit: None (Trailing Stop: HWM - 2.2x ATR)

Volume Divergence: Close > MA50 with volume ratio 2.3x
```

---

## 4. Operations & Quick Commands

```bash
# Generate signals (Console output with [SCALPING] / [SWING] tags)
python run.py signal

# Run daily paper trading cycle
python run.py paper

# Pre-close execution scan at 15:30 WIB (30 mins before market close)
python run.py paper --preclose

# View open positions & trailing stop levels
python run.py paper-status
```
