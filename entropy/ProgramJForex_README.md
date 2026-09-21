# ProgramJForex (Pine -> Java/JForex)

`ProgramJForex.java` is a Java/JForex strategy port of your Pine Script chart-pattern detector.

## What it includes
- Pivot detection using the same `period` logic as Pine (`high[period] > highest(period)` / `low[period] < lowest(period)`).
- Pattern detection for:
  - `LL`
  - `LL & LH`
  - `HH`
  - `HH & HL`
  - `Double Top`
  - `Double Bottom`
- Pattern state transitions:
  - `Active`
  - `Confirmed`
  - `Invalid`
- ATR(14)-based tolerance for Double Top/Bottom parity (`abs(top1 - top2) <= ATR/6`).
- `showLast` cleanup behavior.

## What is intentionally different
- This Java version logs detections/state changes to JForex console.
- It does **not** draw TradingView-style lines/labels/fills.

## Run in JForex
1. Add `ProgramJForex.java` to your JForex strategies source folder.
2. Compile with JForex API on classpath (Dukascopy SDK).
3. Run strategy on your target symbol/timeframe (default: `EURUSD`, `THIRTY_MINS`, `BID`).
4. Watch the strategy output console for pattern events.
