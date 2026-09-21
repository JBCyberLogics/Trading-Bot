"""Entry State Machine for SMC/ICT trading."""

from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
from loguru import logger


class TradeState(Enum):
    IDLE = "IDLE"
    SWEEP_DETECTED = "SWEEP_DETECTED"
    DISPLACEMENT = "DISPLACEMENT"
    RETRACE = "RETRACE"
    ENTRY_READY = "ENTRY_READY"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_FILLED = "ORDER_FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass
class Setup:
    symbol: str
    direction: str  # "LONG" or "SHORT"
    bias: str
    poi_type: str
    poi_zone: list
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    confidence: float
    rr_ratio: float
    created_at: datetime


class EntryStateMachine:
    """
    State machine for trade entry progression.
    Must go through states in order: IDLE -> SWEEP -> DISPLACEMENT -> RETRACE -> ENTRY_READY
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.sweep_max_age = self.config.get("sweep_max_age_candles", 5)
        self.displacement_required = self.config.get("displacement_required_candles", 3)
        self.retrace_max_candles = self.config.get("retrace_max_candles", 8)

        self.state = TradeState.IDLE
        self.current_setup = None
        self.current_poi = None
        self.sweep_data = None
        self.displacement_data = None
        self.candle_count = 0
        self.sweep_age_count = 0
        self.last_bias_confidence = 70.0

        logger.info("EntryStateMachine initialized")

    def reset(self):
        """Reset the state machine to IDLE."""
        self.state = TradeState.IDLE
        self.current_setup = None
        self.current_poi = None
        self.sweep_data = None
        self.displacement_data = None
        self.candle_count = 0
        self.sweep_age_count = 0
        self.last_bias_confidence = 70.0
        logger.debug("State machine reset")

    def process_candle(self, candle, bias, pois, liquidity_levels, bias_confidence: float | None = None):
        """
        Process a new candle and update state.

        Returns:
            Setup object if ENTRY_READY, None otherwise
        """
        self.candle_count += 1
        if bias_confidence is not None:
            try:
                self.last_bias_confidence = float(bias_confidence)
            except (TypeError, ValueError):
                pass
        if self.state != TradeState.IDLE:
            self.sweep_age_count += 1

        if self.state == TradeState.IDLE:
            self._check_for_sweep(candle, liquidity_levels, bias)

        elif self.state == TradeState.SWEEP_DETECTED:
            self._check_for_displacement(candle, bias)

        elif self.state == TradeState.DISPLACEMENT:
            self._check_for_retrace(candle, pois)

        elif self.state == TradeState.RETRACE:
            setup = self._check_entry_ready(candle, pois)
            if setup:
                self.state = TradeState.ENTRY_READY
                return setup

        # Check for expiration
        if self.state in {TradeState.SWEEP_DETECTED, TradeState.DISPLACEMENT, TradeState.RETRACE}:
            if self.sweep_age_count > self.sweep_max_age:
                logger.debug(f"Sweep expired after {self.sweep_age_count} candles")
                self.state = TradeState.EXPIRED
                self.reset()
                return None

        if self.state != TradeState.IDLE and self.candle_count > self.retrace_max_candles:
            logger.debug(f"Setup expired after {self.candle_count} candles")
            self.state = TradeState.EXPIRED
            self.reset()

        return None

    def _check_for_sweep(self, candle, liquidity_levels, bias):
        """Check for liquidity sweep."""
        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        if bias in ["BULLISH", "BULLISH_CAUTION"]:
            # Look for sweep of lows
            for level in liquidity_levels.get("below", []):
                if low < level and close > level:
                    self.sweep_data = {
                        "level": level,
                        "candle_time": candle["time"],
                        "sweep_low": low,
                        "direction": "BULLISH",
                    }
                    self.state = TradeState.SWEEP_DETECTED
                    self.candle_count = 0
                    self.sweep_age_count = 0
                    logger.info(f"Bullish sweep detected at {level}")
                    break

        elif bias in ["BEARISH", "BEARISH_CAUTION"]:
            # Look for sweep of highs
            for level in liquidity_levels.get("above", []):
                if high > level and close < level:
                    self.sweep_data = {
                        "level": level,
                        "candle_time": candle["time"],
                        "sweep_high": high,
                        "direction": "BEARISH",
                    }
                    self.state = TradeState.SWEEP_DETECTED
                    self.candle_count = 0
                    self.sweep_age_count = 0
                    logger.info(f"Bearish sweep detected at {level}")
                    break

    def _check_for_displacement(self, candle, bias):
        """Check for displacement in bias direction after sweep."""
        if self.sweep_data is None:
            return

        # Check if displacement occurred within required window
        if self.candle_count > self.displacement_required:
            self.state = TradeState.EXPIRED
            self.reset()
            return

        # Displacement criteria
        if bias in ["BULLISH", "BULLISH_CAUTION"] and self.sweep_data["direction"] == "BULLISH":
            if candle["close"] > candle["open"] and candle["close"] > self.sweep_data["level"]:
                self.displacement_data = {
                    "candle_time": candle["time"],
                    "price": candle["close"],
                    "strength": (candle["high"] - candle["low"]) / (candle["close"] - candle["open"]),
                }
                self.state = TradeState.DISPLACEMENT
                self.candle_count = 0
                logger.info(f"Displacement confirmed at {candle['close']}")

        elif bias in ["BEARISH", "BEARISH_CAUTION"] and self.sweep_data["direction"] == "BEARISH":
            if candle["close"] < candle["open"] and candle["close"] < self.sweep_data["level"]:
                self.displacement_data = {
                    "candle_time": candle["time"],
                    "price": candle["close"],
                    "strength": (candle["high"] - candle["low"]) / (candle["open"] - candle["close"]),
                }
                self.state = TradeState.DISPLACEMENT
                self.candle_count = 0
                logger.info(f"Displacement confirmed at {candle['close']}")

    def _check_for_retrace(self, candle, pois):
        """Check if price retraces into a POI."""
        if self.displacement_data is None:
            return

        direction = "BULLISH" if self.sweep_data["direction"] == "BULLISH" else "BEARISH"

        for poi in pois.get(direction, []):
            zone = poi["zone"]
            if zone[0] <= candle["low"] <= zone[1] or zone[0] <= candle["high"] <= zone[1]:
                self.state = TradeState.RETRACE
                self.current_poi = poi
                logger.info(f"Retrace into {poi['type']} zone {zone}")
                break

    def _check_entry_ready(self, candle, pois):
        """Check if entry conditions are met."""
        if self.current_poi is None:
            return None

        zone = self.current_poi["zone"]

        # Entry price: midpoint of zone
        entry_price = (zone[0] + zone[1]) / 2

        # Stop loss: just outside zone
        if self.sweep_data["direction"] == "BULLISH":
            stop_loss = zone[0] - (zone[1] - zone[0]) * 0.2
        else:
            stop_loss = zone[1] + (zone[1] - zone[0]) * 0.2

        # Take profit targets (would come from liquidity levels)
        tp1 = entry_price * 1.01 if self.sweep_data["direction"] == "BULLISH" else entry_price * 0.99
        tp2 = entry_price * 1.02 if self.sweep_data["direction"] == "BULLISH" else entry_price * 0.98

        # Calculate R:R
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            logger.warning("Invalid risk: stop loss equals entry price")
            return None
        reward1 = abs(tp1 - entry_price)
        reward2 = abs(tp2 - entry_price)
        rr_ratio = (reward1 * 0.6 + reward2 * 0.4) / risk

        confidence = self.last_bias_confidence
        if confidence <= 1.0:
            confidence *= 100.0
        confidence = max(70.0, min(95.0, confidence))

        setup = Setup(
            symbol="EURUSD",
            direction="LONG" if self.sweep_data["direction"] == "BULLISH" else "SHORT",
            bias=self.sweep_data["direction"],
            poi_type=self.current_poi["type"],
            poi_zone=zone,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            confidence=confidence,
            rr_ratio=rr_ratio,
            created_at=candle["time"],
        )

        logger.info(f"Entry ready: {setup.direction} at {setup.entry_price}")
        return setup
