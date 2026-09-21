from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config.loader import load_config
from app.safety_monitor.service import SafetyMonitor
from app.safety_monitor.news_provider import EconomicNewsEvent
from app.trading_logic.engine import TradingEngine
from app.trading_logic.models import MarketSnapshot
from app.persistence.database import Database


def test_trading_engine_fail_closed_on_missing_order_book(test_env: dict) -> None:
    loaded = load_config()
    loaded.config.trading_logic.entry_rules.trading_hours.enabled = False
    engine = TradingEngine(loaded.config)
    engine.start()

    snapshot = MarketSnapshot(
        symbol="EURUSD",
        bid=1.1000,
        ask=1.1001,
        spread_pips=1.0,
        order_book_imbalances=[],
        spread_history=[1.0, 1.0, 1.0],
        tick_timestamp=datetime.now(timezone.utc),
    )

    decision = engine.evaluate(
        snapshot,
        positions=[],
        connection_ok=True,
        safety_ok=True,
        safety_reason="ok",
        lot_size=0.1,
    )
    assert decision.allowed is False
    assert "order_book" in decision.reason


def test_trading_engine_approves_when_rules_pass(test_env: dict) -> None:
    loaded = load_config()
    loaded.config.trading_logic.entry_rules.trading_hours.enabled = False

    engine = TradingEngine(loaded.config)
    engine.start()

    snapshot = MarketSnapshot(
        symbol="EURUSD",
        bid=1.1000,
        ask=1.1001,
        spread_pips=1.0,
        order_book_imbalances=[0.8, 0.85, 0.9],
        spread_history=[1.2, 1.1, 1.0],
    )

    decision = engine.evaluate(
        snapshot,
        positions=[],
        connection_ok=True,
        safety_ok=True,
        safety_reason="ok",
        lot_size=0.1,
    )

    assert decision.allowed is True
    assert decision.direction == "LONG"
    assert decision.take_profit is not None


class StaticNewsProvider:
    def __init__(self, events: list[EconomicNewsEvent]) -> None:
        self.events = list(events)

    async def fetch_events(self) -> list[EconomicNewsEvent]:
        return list(self.events)


@pytest.mark.asyncio
async def test_news_guard_allows_when_provider_missing(test_env: dict) -> None:
    loaded = load_config()
    db = Database(loaded.runtime.database_path)
    db.initialize()

    safety = SafetyMonitor(loaded.config, loaded.runtime, db)
    safety.update_account(balance=10000.0, equity=10000.0)

    ok, reason = await safety.evaluate_gate(
        symbol="EURUSD",
        positions=[],
        pending_lot=0.1,
        connection_status="connected",
    )
    assert ok is True
    assert reason == "ok"


@pytest.mark.asyncio
async def test_news_guard_blocks_matching_high_impact_event(test_env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWS_PROVIDER_URL", "https://calendar.example.test/feed")
    loaded = load_config()
    db = Database(loaded.runtime.database_path)
    db.initialize()

    safety = SafetyMonitor(
        loaded.config,
        loaded.runtime,
        db,
        news_provider=StaticNewsProvider(
            [
                EconomicNewsEvent(
                    title="US CPI",
                    timestamp=datetime.now(timezone.utc),
                    impact="high",
                    currencies=("USD",),
                )
            ]
        ),
    )
    safety.update_account(balance=10000.0, equity=10000.0)

    ok, reason = await safety.evaluate_gate(
        symbol="EURUSD",
        positions=[],
        pending_lot=0.1,
        connection_status="connected",
    )
    assert ok is False
    assert reason == "news_window_active"
