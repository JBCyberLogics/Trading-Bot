"""Trade recording system with regime tagging and lifecycle updates."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
import uuid
from typing import Dict, Optional

from loguru import logger

from src.execution.zmq_bridge import TradeOrder


class TradeRecorder:
    """Records trades and manages execution/exit updates."""

    def __init__(self, db, regime_detector, bridge):
        self.db = db
        self.regime_detector = regime_detector
        self.bridge = bridge
        self.pending_trades: Dict[str, Dict] = {}
        logger.info("TradeRecorder initialized")

    def generate_trade_id(self) -> str:
        """Generate a unique external trade identifier."""
        return f"TRADE_{self._utcnow_naive().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def record_signal(self, setup, regime_result) -> str:
        """Record a pre-execution signal with regime tags."""
        trade_id = self.generate_trade_id()

        trade_record = {
            "trade_id": trade_id,
            "timestamp": self._utcnow_naive(),
            "symbol": setup.symbol,
            "direction": setup.direction,
            "entry_price": setup.entry_price,
            "stop_loss": setup.stop_loss,
            "take_profit_1": setup.take_profit_1,
            "take_profit_2": setup.take_profit_2,
            "lots": 0.0,
            "bias_at_entry": setup.bias,
            "bias_confidence": int(setup.confidence),
            "poi_type": setup.poi_type,
            "entry_zone_min": setup.poi_zone[0],
            "entry_zone_max": setup.poi_zone[1],
            "confidence_score": float(setup.confidence),
            "rr_ratio": float(setup.rr_ratio),
            "version_id": "CHAMPION_V1",
        }

        trade_record = self.regime_detector.tag_trade(trade_record, regime_result)
        self.db.add_trade(trade_record)
        self.pending_trades[trade_id] = {"setup": setup, "record": trade_record, "sent": False}

        logger.info(f"Trade signal recorded: {trade_id}")
        return trade_id

    def send_trade(self, trade_id: str, volume: float = 0.01) -> Optional[Dict]:
        """Send a recorded trade to MT5 for execution."""
        if trade_id not in self.pending_trades:
            logger.error(f"Trade {trade_id} not found")
            return None

        pending = self.pending_trades[trade_id]
        setup = pending["setup"]
        direction = "BUY" if setup.direction.upper() in {"LONG", "BUY"} else "SELL"
        point_size = self._resolve_point_size(setup)
        entry_method = str(getattr(setup, "entry_method", "MARKET")).upper()
        order_type = "LIMIT" if "LIMIT" in entry_method else "MARKET"

        order = TradeOrder(
            actionType=direction,
            symbol=setup.symbol,
            volume=float(volume),
            orderType=order_type,
            price=float(setup.entry_price) if order_type == "LIMIT" else 0.0,
            stoploss=self._price_distance_to_points(setup.entry_price, setup.stop_loss, point_size),
            takeprofit=self._price_distance_to_points(
                setup.take_profit_1, setup.entry_price, point_size
            ),
            comment=f"SMC_{setup.poi_type}_{trade_id}",
        )

        feedback = self.bridge.execute_trade(order)
        if not feedback or feedback.get("error"):
            logger.error(f"Trade {trade_id} execution failed: {feedback}")
            return None

        if feedback.get("action") == "EXECUTION":
            pending["sent"] = True
            pending["ticket"] = feedback.get("ticket")
            self.db.update_trade_exit(
                trade_id,
                {
                    "lots": float(volume),
                    "entry_price": feedback.get("price", setup.entry_price),
                },
            )
            logger.info(f"Trade {trade_id} executed")
            return feedback

        logger.error(f"Trade {trade_id} returned unexpected response: {feedback}")
        return None

    def record_exit(self, trade_id: str, exit_price: float, exit_reason: str) -> Optional[Dict]:
        """Record exit details and update PnL/R metrics."""
        pending = self.pending_trades.get(trade_id)
        if pending is None:
            trade = self.db.get_trade_by_trade_id(trade_id)
            if trade is None:
                logger.error(f"Trade {trade_id} not found for exit recording")
                return None
            record = trade.to_dict()
        else:
            record = pending["record"]

        entry = float(record["entry_price"])
        lots = float(record.get("lots") or 0.01)
        direction = str(record["direction"]).upper()

        if direction in {"LONG", "BUY"}:
            pnl_pips = (float(exit_price) - entry) * 10000
            pnl = pnl_pips * lots * 10
        else:
            pnl_pips = (entry - float(exit_price)) * 10000
            pnl = pnl_pips * lots * 10

        risk = abs(entry - float(record["stop_loss"]))
        r_multiple = 0.0
        if risk > 0:
            if direction in {"LONG", "BUY"}:
                r_multiple = (float(exit_price) - entry) / risk
            else:
                r_multiple = (entry - float(exit_price)) / risk

        if pnl > 0:
            outcome = "WIN"
        elif pnl < 0:
            outcome = "LOSS"
        else:
            outcome = "BE"

        self.db.update_trade_exit(
            trade_id,
            {
                "exit_price": float(exit_price),
                "pnl": float(pnl),
                "pnl_pips": float(pnl_pips),
                "r_multiple": float(r_multiple),
                "outcome": outcome,
                "exit_reason": exit_reason,
            },
        )

        logger.info(f"Trade {trade_id} closed: {outcome} {pnl_pips:.1f} pips")
        if trade_id in self.pending_trades:
            del self.pending_trades[trade_id]

        return {"trade_id": trade_id, "outcome": outcome, "pnl": float(pnl), "r_multiple": float(r_multiple)}

    def get_pending_trades(self):
        """Get trades sent but not closed."""
        return [tid for tid, payload in self.pending_trades.items() if payload.get("sent")]

    @staticmethod
    def _resolve_point_size(setup) -> float:
        """Resolve symbol point size with setup override support."""
        explicit = getattr(setup, "point_size", None)
        if isinstance(explicit, (int, float)) and explicit > 0:
            return float(explicit)

        symbol = str(getattr(setup, "symbol", "")).upper()
        if "JPY" in symbol:
            return 0.001
        if symbol.startswith(("XAU", "XAG")):
            return 0.01
        return 0.00001

    @staticmethod
    def _price_distance_to_points(price_a: float, price_b: float, point_size: float) -> float:
        """Convert absolute price distance into MT5 points."""
        if point_size <= 0:
            return 0.0
        return float(abs(float(price_a) - float(price_b)) / point_size)

    @staticmethod
    def _utcnow_naive() -> datetime:
        """Return a UTC timestamp without tzinfo for SQLite compatibility."""
        return datetime.now(timezone.utc).replace(tzinfo=None)
