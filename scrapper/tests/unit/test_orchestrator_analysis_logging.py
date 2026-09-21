from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import Mock

from app.config.loader import load_config
from app.core.orchestrator import BotOrchestrator


def _build_orchestrator() -> BotOrchestrator:
    loaded = load_config()
    connection = SimpleNamespace(status="connected")
    trading = SimpleNamespace(running=True, paused=False)
    return BotOrchestrator(
        loaded=loaded,
        connection=connection,
        scraper=Mock(),
        trading=trading,
        safety=Mock(),
        risk=Mock(),
        db=Mock(),
        bridge=Mock(),
        alerts=Mock(),
    )


def test_pair_analysis_logs_exact_reason_once_for_identical_rejections(
    test_env: dict, caplog
) -> None:
    orchestrator = _build_orchestrator()

    with caplog.at_level(logging.INFO, logger="app.core.orchestrator"):
        orchestrator._update_pair_analysis(
            "XAUUSD",
            status="rejected",
            stage="initial_evaluation",
            reason="spread_too_high",
            safety_ok=True,
            safety_reason="ok",
            tick={"bid": 5113.52988, "ask": 5113.53011, "spread_pips": 2.3},
            order_book={"imbalance": 0.0921},
        )
        orchestrator._update_pair_analysis(
            "XAUUSD",
            status="rejected",
            stage="initial_evaluation",
            reason="spread_too_high",
            safety_ok=True,
            safety_reason="ok",
            tick={"bid": 5113.52988, "ask": 5113.53011, "spread_pips": 2.3},
            order_book={"imbalance": 0.0921},
        )

    messages = [record.getMessage() for record in caplog.records if "trade_analysis symbol=XAUUSD" in record.getMessage()]

    assert len(messages) == 1
    assert "reason=spread_too_high" in messages[0]
    assert "display_reason=The spread is above the configured maximum." in messages[0]
    assert "spread_pips=2.3" in messages[0]
    assert "imbalance=0.0921" in messages[0]


def test_pair_analysis_logs_again_when_reason_changes(test_env: dict, caplog) -> None:
    orchestrator = _build_orchestrator()

    with caplog.at_level(logging.INFO, logger="app.core.orchestrator"):
        orchestrator._update_pair_analysis(
            "XAUUSD",
            status="rejected",
            stage="initial_evaluation",
            reason="spread_too_high",
            safety_ok=True,
            safety_reason="ok",
            tick={"bid": 5113.52988, "ask": 5113.53011, "spread_pips": 2.3},
            order_book={"imbalance": 0.0921},
        )
        orchestrator._update_pair_analysis(
            "XAUUSD",
            status="rejected",
            stage="initial_evaluation",
            reason="no_imbalance_signal",
            safety_ok=True,
            safety_reason="ok",
            tick={"bid": 5113.52988, "ask": 5113.53011, "spread_pips": 1.8},
            order_book={"imbalance": 0.1469},
        )

    messages = [record.getMessage() for record in caplog.records if "trade_analysis symbol=XAUUSD" in record.getMessage()]

    assert len(messages) == 2
    assert "reason=spread_too_high" in messages[0]
    assert "reason=no_imbalance_signal" in messages[1]
    assert "display_reason=Order book imbalance did not reach the LONG or SHORT threshold." in messages[1]
