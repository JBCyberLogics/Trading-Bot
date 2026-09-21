from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Protocol, Sequence

import httpx


COUNTRY_TO_CURRENCY = {
    "australia": "AUD",
    "canada": "CAD",
    "china": "CNY",
    "euro zone": "EUR",
    "eurozone": "EUR",
    "european union": "EUR",
    "france": "EUR",
    "germany": "EUR",
    "japan": "JPY",
    "new zealand": "NZD",
    "switzerland": "CHF",
    "united kingdom": "GBP",
    "uk": "GBP",
    "united states": "USD",
    "us": "USD",
}


@dataclass(slots=True)
class EconomicNewsEvent:
    title: str
    timestamp: datetime
    impact: str
    currencies: tuple[str, ...]


class NewsProvider(Protocol):
    async def fetch_events(self) -> list[EconomicNewsEvent]:
        ...


class NewsProviderError(RuntimeError):
    pass


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime_value(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _coerce_utc(raw)
    if isinstance(raw, (int, float)):
        raw_value = float(raw)
        if raw_value > 1_000_000_000_000:
            raw_value /= 1000.0
        return datetime.fromtimestamp(raw_value, tz=timezone.utc)

    value = str(raw).strip()
    if not value:
        return None

    if value.isdigit():
        return _parse_datetime_value(int(value))

    normalized = value.replace("Z", "+00:00")
    try:
        return _coerce_utc(datetime.fromisoformat(normalized))
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_split_date_time(date_raw: Any, time_raw: Any) -> datetime | None:
    date_text = str(date_raw or "").strip()
    time_text = str(time_raw or "").strip()
    if not date_text:
        return None

    direct = _parse_datetime_value(date_text)
    if direct is not None:
        return direct

    if not time_text:
        return None
    if time_text.lower() in {"all day", "tentative"}:
        return None

    candidate = f"{date_text} {time_text}"
    return _parse_datetime_value(candidate)


def _normalize_impact(raw: Any) -> str:
    if raw is None:
        return "unknown"
    if isinstance(raw, (int, float)):
        if float(raw) >= 3:
            return "high"
        if float(raw) >= 2:
            return "medium"
        return "low"

    value = str(raw).strip().lower()
    if not value:
        return "unknown"
    if any(token in value for token in ("high", "red", "critical", "3")):
        return "high"
    if any(token in value for token in ("medium", "med", "moderate", "orange", "2")):
        return "medium"
    if any(token in value for token in ("low", "yellow", "1")):
        return "low"
    return "unknown"


def _collect_currency_tokens(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        tokens: list[str] = []
        for key in ("code", "currency", "symbol", "country", "name"):
            tokens.extend(_collect_currency_tokens(raw.get(key)))
        return tokens
    if isinstance(raw, (list, tuple, set)):
        tokens: list[str] = []
        for item in raw:
            tokens.extend(_collect_currency_tokens(item))
        return tokens

    value = str(raw).strip()
    if not value:
        return []
    lowered = value.lower()
    if lowered in COUNTRY_TO_CURRENCY:
        return [COUNTRY_TO_CURRENCY[lowered]]

    tokens: list[str] = []
    for item in re.split(r"[^A-Za-z]+", value.upper()):
        if not item:
            continue
        if item in {"ALL", "GLOBAL"}:
            tokens.append("ALL")
            continue
        if len(item) == 6 and item.isalpha():
            tokens.extend((item[:3], item[3:6]))
            continue
        if len(item) == 3 and item.isalpha():
            tokens.append(item)
    return tokens


def _event_currencies(item: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("currency", "currencies", "country", "countries", "symbol", "symbols"):
        values.extend(_collect_currency_tokens(item.get(key)))

    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _payload_items(payload: Any) -> Sequence[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "data", "items", "results", "calendar", "news"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
        return [payload]
    raise NewsProviderError("news_provider_invalid_payload")


def _parse_event(item: Any) -> EconomicNewsEvent | None:
    if not isinstance(item, dict):
        return None

    timestamp = None
    for key in ("timestamp", "datetime", "published_at", "publishedAt", "event_time", "time_utc"):
        timestamp = _parse_datetime_value(item.get(key))
        if timestamp is not None:
            break
    if timestamp is None:
        timestamp = _parse_split_date_time(item.get("date"), item.get("time"))
    if timestamp is None and "time" in item:
        timestamp = _parse_datetime_value(item.get("time"))
    if timestamp is None:
        return None

    title = str(
        item.get("title")
        or item.get("event")
        or item.get("name")
        or item.get("headline")
        or item.get("description")
        or "event"
    ).strip()
    if not title:
        title = "event"

    return EconomicNewsEvent(
        title=title,
        timestamp=timestamp,
        impact=_normalize_impact(
            item.get("impact")
            or item.get("importance")
            or item.get("priority")
            or item.get("severity")
            or item.get("impact_level")
        ),
        currencies=_event_currencies(item),
    )


def parse_news_payload(payload: Any) -> list[EconomicNewsEvent]:
    items = list(_payload_items(payload))
    events = [event for item in items if (event := _parse_event(item)) is not None]
    if items and not events:
        raise NewsProviderError("news_provider_invalid_payload")
    return events


class HttpNewsProvider:
    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        self.url = str(url)
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    async def fetch_events(self) -> list[EconomicNewsEvent]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(self.url, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise NewsProviderError("news_provider_unavailable") from exc
        except ValueError as exc:
            raise NewsProviderError("news_provider_invalid_payload") from exc

        return parse_news_payload(payload)
