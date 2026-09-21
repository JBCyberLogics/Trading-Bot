from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List

from app.config.schema import AlertsConfig

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self, config: AlertsConfig) -> None:
        self.config = config
        self.events: List[Dict[str, Any]] = []

    def emit(self, level: str, event: str, message: str, payload: Dict[str, Any] | None = None) -> None:
        if not self.config.enabled:
            return
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "event": event,
            "message": message,
            "payload": payload or {},
        }
        self.events.append(row)
        if len(self.events) > 1000:
            self.events = self.events[-1000:]
        logger.info("alert level=%s event=%s msg=%s", row["level"], event, message)

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.events[-max(1, limit) :]
