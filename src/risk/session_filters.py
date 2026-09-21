"""Session and news filters for risk-aware trade gating."""

from __future__ import annotations

from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from typing import Any, Dict, List, Tuple

from loguru import logger
import requests


class SessionFilter:
    """Detects trading session and provides session multipliers."""

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = config or {}
        self.sessions = {
            "LONDON_OPEN": {"start": time(7, 0), "end": time(9, 0), "multiplier": 1.0},
            "LONDON_CLOSE": {"start": time(15, 0), "end": time(17, 0), "multiplier": 0.7},
            "NY_OPEN": {"start": time(13, 0), "end": time(16, 0), "multiplier": 1.0},
            "ASIAN": {"start": time(22, 0), "end": time(7, 0), "multiplier": 0.5},
            "REGULAR": {"start": time(0, 0), "end": time(23, 59), "multiplier": 0.8},
        }
        logger.info("SessionFilter initialized")

    def get_current_session(self, dt: datetime | None = None) -> Tuple[str, Dict[str, Any]]:
        now = dt or _utcnow_naive()
        current = now.time()

        for name, info in self.sessions.items():
            start = info["start"]
            end = info["end"]
            if start <= end:
                if start <= current <= end:
                    return name, info
            else:
                if current >= start or current <= end:
                    return name, info

        return "REGULAR", self.sessions["REGULAR"]

    def get_session_multiplier(self) -> float:
        _, info = self.get_current_session()
        return float(info.get("multiplier", 1.0))

    def is_session_allowed(self, allowed_sessions: List[str] | None = None) -> bool:
        if not allowed_sessions:
            return True
        session_name, _ = self.get_current_session()
        return session_name in allowed_sessions


class NewsFilter:
    """News-event gate with hard blocking when feed is unavailable."""

    def __init__(
        self,
        api_key: str | None = None,
        hard_block_without_api: bool = True,
        endpoint: str | None = None,
        rapidapi_host: str | None = None,
        rapidapi_app: str | None = None,
    ):
        self.api_key = api_key
        self.hard_block_without_api = bool(hard_block_without_api)
        self.endpoint = endpoint
        self.rapidapi_host = rapidapi_host
        self.rapidapi_app = rapidapi_app
        self.last_error: str | None = None
        self.last_update: datetime | None = None
        logger.info("NewsFilter initialized")

    def fetch_news_events(self, dt: datetime | None = None) -> List[Dict[str, Any]]:
        now = dt or _utcnow_naive()

        if not self.api_key:
            self.last_error = "Missing news API key"
            if self.hard_block_without_api:
                return [{"currency": "ALL", "impact": "HIGH", "time": now.time(), "event": "NO_NEWS_FEED"}]
            return []

        if not self.endpoint:
            self.last_error = "Missing news endpoint"
            return [{"currency": "ALL", "impact": "HIGH", "time": now.time(), "event": "NO_NEWS_ENDPOINT"}]

        try:
            headers: Dict[str, str] = {}
            if self.api_key:
                headers["X-RapidAPI-Key"] = str(self.api_key)
            if self.rapidapi_host:
                headers["X-RapidAPI-Host"] = str(self.rapidapi_host)
            if self.rapidapi_app:
                headers["X-RapidAPI-Application"] = str(self.rapidapi_app)

            response = requests.get(self.endpoint, headers=headers or None, timeout=5)
            response.raise_for_status()
            payload = response.json()
            normalized_payload = self._normalize_payload(payload)
            if not isinstance(normalized_payload, list):
                self.last_error = "Invalid news payload format"
                return [{"currency": "ALL", "impact": "HIGH", "time": now.time(), "event": "INVALID_NEWS_PAYLOAD"}]
            events = [self._normalize_event(x) for x in normalized_payload if isinstance(x, dict)]
            if not events:
                self.last_error = "No parseable events in news payload"
                return [{"currency": "ALL", "impact": "HIGH", "time": now.time(), "event": "INVALID_NEWS_PAYLOAD"}]
            self.last_update = now
            self.last_error = None
            return events
        except Exception as exc:
            self.last_error = f"News fetch failed: {exc}"
            return [{"currency": "ALL", "impact": "HIGH", "time": now.time(), "event": "NEWS_FETCH_FAILED"}]

    @staticmethod
    def _normalize_payload(payload: Any) -> Any:
        """Handle common API envelope formats and return raw events list."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "results", "events", "items", "response"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    for nested_key in ("events", "items", "data", "results"):
                        nested = value.get(nested_key)
                        if isinstance(nested, list):
                            return nested
        return payload

    @staticmethod
    def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize external event schema into internal fields."""
        currency = (
            event.get("currency")
            or event.get("currencyCode")
            or event.get("country")
            or event.get("countryCode")
            or "ALL"
        )
        impact_raw = event.get("impact", event.get("importance", event.get("severity", "HIGH")))
        impact = str(impact_raw).upper()
        if impact in {"3", "VERY_HIGH", "CRITICAL"}:
            impact = "HIGH"
        elif impact in {"2", "MEDIUM"}:
            impact = "MEDIUM"
        elif impact in {"1", "LOW"}:
            impact = "LOW"
        elif impact not in {"HIGH", "MEDIUM", "LOW"}:
            impact = "HIGH"

        event_name = event.get("event") or event.get("title") or event.get("name") or "UNKNOWN"
        event_time = event.get("time") or event.get("datetime") or event.get("timestamp") or "00:00"

        return {
            "currency": str(currency).upper(),
            "impact": impact,
            "time": event_time,
            "event": str(event_name),
        }

    @staticmethod
    def _symbol_currencies(symbol: str) -> List[str]:
        symbol = str(symbol or "").upper()
        if symbol.startswith("XAU") or symbol.startswith("XAG"):
            return ["USD"]
        if len(symbol) >= 6 and symbol.isalpha():
            return [symbol[0:3], symbol[3:6]]
        return [symbol]

    def get_upcoming_news(self, symbol: str, minutes_ahead: int = 60) -> List[Dict[str, Any]]:
        now = _utcnow_naive()
        events = self.fetch_news_events(now)
        currencies = set(self._symbol_currencies(symbol) + ["ALL"])

        upcoming: List[Dict[str, Any]] = []
        for event in events:
            if str(event.get("impact", "")).upper() != "HIGH":
                continue
            if str(event.get("currency", "")).upper() not in currencies:
                continue

            event_time = event.get("time")
            if isinstance(event_time, str):
                try:
                    hh, mm = event_time.split(":")[:2]
                    event_dt = datetime.combine(now.date(), time(int(hh), int(mm)))
                except Exception:
                    event_dt = now
            elif isinstance(event_time, time):
                event_dt = datetime.combine(now.date(), event_time)
            elif isinstance(event_time, datetime):
                event_dt = event_time
            else:
                event_dt = now

            if event_dt < now:
                event_dt += timedelta(days=1)

            minutes_until = (event_dt - now).total_seconds() / 60.0
            if 0 <= minutes_until <= minutes_ahead:
                upcoming.append(
                    {
                        "event": str(event.get("event", "UNKNOWN")),
                        "currency": str(event.get("currency", "")),
                        "minutes": float(minutes_until),
                        "impact": "HIGH",
                    }
                )

        return upcoming

    def should_block_trade(self, symbol: str, buffer_minutes: int = 15) -> bool:
        upcoming = self.get_upcoming_news(symbol, minutes_ahead=buffer_minutes)
        if upcoming:
            logger.info(
                f"Blocking {symbol} trade due to news: {upcoming[0]['event']} in {upcoming[0]['minutes']:.1f} minutes"
            )
            return True
        return False


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
