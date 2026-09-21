from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.orchestrator import BotOrchestrator


def _loaded_config(*, auto_start_trading: bool, auto_start_on_connect: bool):
    return SimpleNamespace(
        config=SimpleNamespace(
            pairs=SimpleNamespace(active=["EURUSD"], available=["EURUSD"]),
            startup=SimpleNamespace(auto_start_trading=auto_start_trading),
            trading_logic=SimpleNamespace(auto_start_on_connect=auto_start_on_connect),
        ),
        runtime=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_enable_trading_logic_requires_manual_start_when_auto_start_is_disabled() -> None:
    trading = Mock()
    orchestrator = BotOrchestrator(
        loaded=_loaded_config(auto_start_trading=False, auto_start_on_connect=True),
        connection=Mock(),
        scraper=Mock(),
        trading=trading,
        safety=Mock(),
        risk=Mock(),
        db=Mock(),
        bridge=Mock(),
        alerts=Mock(),
    )

    result = await orchestrator._enable_trading_logic()

    assert result == "manual_start_required"
    trading.start.assert_not_called()
    trading.stop.assert_called_once()


@pytest.mark.asyncio
async def test_enable_trading_logic_starts_when_auto_start_is_enabled() -> None:
    trading = Mock()
    orchestrator = BotOrchestrator(
        loaded=_loaded_config(auto_start_trading=True, auto_start_on_connect=True),
        connection=Mock(),
        scraper=Mock(),
        trading=trading,
        safety=Mock(),
        risk=Mock(),
        db=Mock(),
        bridge=Mock(),
        alerts=Mock(),
    )

    orchestrator._ensure_trading_task = Mock()

    result = await orchestrator._enable_trading_logic()

    assert result == "started"
    trading.start.assert_called_once()
    trading.stop.assert_not_called()
    orchestrator._ensure_trading_task.assert_called_once()
