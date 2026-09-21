from __future__ import annotations

from typing import Any, Dict, List

from app.mt5_connection.zmq_bridge import ZMQBridge


def is_unsupported_action_error(reason: str | None) -> bool:
    message = str(reason or "").lower()
    return "unknown action" in message or "unsupported" in message


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in str(symbol).upper() else 0.0001


def price_precision(symbol: str) -> int:
    return 3 if "JPY" in str(symbol).upper() else 5


def _as_bars(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("bars")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def derive_tick_from_rates(
    bridge: ZMQBridge,
    symbol: str,
    *,
    timeframe: str = "M1",
    count: int = 3,
) -> Dict[str, Any]:
    pair = str(symbol).upper()
    payload = bridge.get_rates(pair, str(timeframe).upper(), max(2, int(count)))
    bars = _as_bars(payload)
    if not bars:
        return {"error": str(payload.get("error") or "no_rates_data")}

    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else bars[-1]
    close_now = float(last.get("close", last.get("open", 0.0)) or 0.0)
    close_prev = float(prev.get("close", prev.get("open", close_now)) or close_now)
    high = float(last.get("high", close_now) or close_now)
    low = float(last.get("low", close_now) or close_now)

    pip = pip_size(pair)
    movement_pips = abs(close_now - close_prev) / pip if pip > 0 else 0.0
    bar_range_pips = abs(high - low) / pip if pip > 0 else 0.0
    spread_pips = _clamp(0.7 + movement_pips * 0.12 + bar_range_pips * 0.04, 0.6, 2.3)

    half_spread = spread_pips * pip / 2.0
    precision = price_precision(pair)
    bid = round(close_now - half_spread, precision)
    ask = round(close_now + half_spread, precision)

    return {
        "symbol": pair,
        "bid": bid,
        "ask": ask,
        "spread_pips": round(spread_pips, 3),
        "source": "derived_rates",
        "rates_error": payload.get("error"),
    }


def derive_order_book_from_rates(
    bridge: ZMQBridge,
    symbol: str,
    *,
    levels: int = 10,
    timeframe: str = "M1",
    count: int = 24,
) -> Dict[str, Any]:
    pair = str(symbol).upper()
    level_count = max(1, min(50, int(levels)))
    payload = bridge.get_rates(pair, str(timeframe).upper(), max(level_count + 2, int(count)))
    bars = _as_bars(payload)
    if not bars:
        return {"error": str(payload.get("error") or "no_rates_data")}

    pip = pip_size(pair)
    precision = price_precision(pair)
    recent = bars[-min(len(bars), 10) :]

    total_weight = 0.0
    weighted_signal = 0.0
    for idx, row in enumerate(recent, start=1):
        open_px = float(row.get("open", row.get("close", 0.0)) or 0.0)
        close_px = float(row.get("close", open_px) or open_px)
        high_px = float(row.get("high", max(open_px, close_px)) or max(open_px, close_px))
        low_px = float(row.get("low", min(open_px, close_px)) or min(open_px, close_px))
        span = max(abs(high_px - low_px), pip)
        candle_signal = _clamp((close_px - open_px) / span, -1.0, 1.0)
        total_weight += float(idx)
        weighted_signal += candle_signal * float(idx)

    imbalance = _clamp(weighted_signal / total_weight if total_weight > 0 else 0.0, -1.0, 1.0)

    last_close = float(recent[-1].get("close", recent[-1].get("open", 0.0)) or 0.0)
    bids: List[Dict[str, float]] = []
    asks: List[Dict[str, float]] = []
    for level in range(1, level_count + 1):
        decay = max(0.15, 1.0 - (level - 1) / float(level_count))
        bid_volume = round((1.0 + max(0.0, imbalance)) * decay, 4)
        ask_volume = round((1.0 + max(0.0, -imbalance)) * decay, 4)
        bids.append({"price": round(last_close - pip * level, precision), "volume": bid_volume})
        asks.append({"price": round(last_close + pip * level, precision), "volume": ask_volume})

    return {
        "symbol": pair,
        "bids": bids,
        "asks": asks,
        "imbalance": round(imbalance, 4),
        "source": "derived_rates",
        "rates_error": payload.get("error"),
    }
