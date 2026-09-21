from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List


@dataclass(slots=True)
class MarketSnapshot:
    symbol: str
    bid: float
    ask: float
    spread_pips: float
    order_book_imbalances: List[float]
    spread_history: List[float] = field(default_factory=list)
    tick_timestamp: datetime | None = None


@dataclass(slots=True)
class TradeDecision:
    allowed: bool
    reason: str
    direction: str | None = None
    lot_size: float | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)
