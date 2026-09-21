from __future__ import annotations

from app.core.trade_analysis import describe_trade_reason, enrich_trade_analysis


def test_describe_trade_reason_handles_known_engine_reason() -> None:
    assert describe_trade_reason("no_imbalance_signal") == "Order book imbalance did not reach the LONG or SHORT threshold."


def test_describe_trade_reason_handles_prefixed_reason() -> None:
    assert describe_trade_reason("safety_blocked:drawdown_percent_limit_hit") == (
        "Safety monitor blocked the trade. The maximum drawdown limit has been hit."
    )


def test_enrich_trade_analysis_adds_display_fields() -> None:
    payload = enrich_trade_analysis(
        {
            "symbol": "EURUSD",
            "status": "rejected",
            "stage": "initial_evaluation",
            "reason": "spread_too_high",
            "decision": {"reason": "spread_too_high", "allowed": False},
            "last_opened_trade": {"reason": "approved", "direction": "LONG"},
        }
    )

    assert payload["display_status"] == "Rejected"
    assert payload["display_stage"] == "Initial Evaluation"
    assert payload["display_reason"] == "The spread is above the configured maximum."
    assert payload["decision"]["display_reason"] == "The spread is above the configured maximum."
    assert payload["last_opened_trade"]["display_reason"] == "All entry checks passed and the order was approved."
