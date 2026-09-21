from __future__ import annotations

from typing import Any


_STAGE_LABELS = {
    "pending": "Pending",
    "trading_start": "Trading Start",
    "market_data": "Market Data",
    "initial_evaluation": "Initial Evaluation",
    "execution_recheck": "Execution Recheck",
    "order_submission": "Order Submission",
}

_STATUS_LABELS = {
    "idle": "Idle",
    "blocked": "Blocked",
    "rejected": "Rejected",
    "error": "Error",
    "opened": "Opened",
}

_REASON_LABELS = {
    "approved": "All entry checks passed and the order was approved.",
    "not_analyzed_yet": "No trade analysis has run for this pair yet.",
    "not_connected": "MT5 is not connected, so analysis cannot place a trade.",
    "tick_data_unavailable": "Live tick data is unavailable for this pair.",
    "order_book_data_unavailable": "Order book data is unavailable for this pair.",
    "latest_market_data_unavailable": "Fresh market data disappeared during the final confirmation step.",
    "trading_not_running": "Trading is not currently running.",
    "connection_required": "A live connection is required before this strategy can trade.",
    "outside_trading_hours": "The current time is outside the configured trading session.",
    "missing_spread_data": "Spread data is missing, so the trade cannot be evaluated safely.",
    "spread_too_high": "The spread is above the configured maximum.",
    "spread_increasing": "The recent spread readings are widening, so the trade was rejected.",
    "order_book_data_insufficient": "There are not enough order book imbalance readings yet.",
    "invalid_order_book_imbalance": "The order book imbalance data is invalid.",
    "order_book_rule_disabled_fail_closed": "The order book rule is disabled and the engine is configured to fail closed.",
    "no_imbalance_signal": "Order book imbalance did not reach the LONG or SHORT threshold.",
    "min_time_between_trades_not_met": "The minimum time between trades for this pair has not elapsed yet.",
    "max_trades_per_hour_reached": "This pair already reached its maximum trades for the last hour.",
    "cooloff_after_loss_active": "The post-loss cooloff window is still active for this pair.",
    "max_total_positions_reached": "The maximum total number of open positions has already been reached.",
    "max_per_pair_reached": "This pair already has the maximum allowed open positions.",
    "max_direction_exposure_reached": "The current directional exposure is already at its configured limit.",
    "daily_loss_limit_hit": "The daily loss percentage limit has been hit.",
    "daily_loss_absolute_hit": "The daily absolute loss limit has been hit.",
    "drawdown_percent_limit_hit": "The maximum drawdown limit has been hit.",
    "lot_size_per_trade_limit": "The calculated lot size is above the per-trade safety limit.",
    "total_lot_limit": "Opening this trade would exceed the maximum total lot limit.",
    "disconnect_timeout": "The connection has been unavailable longer than the allowed timeout.",
    "news_window_active": "A matching high-impact news event is inside the protection window.",
    "manual_start_required": "Auto-start is disabled, so trading must be started manually.",
}


def _humanize_token(value: str) -> str:
    token = str(value or "").strip().replace("_", " ")
    if not token:
        return "Unknown"
    return token[0].upper() + token[1:]


def describe_trade_stage(stage: str) -> str:
    return _STAGE_LABELS.get(str(stage or ""), _humanize_token(str(stage or "")))


def describe_trade_status(status: str) -> str:
    return _STATUS_LABELS.get(str(status or ""), _humanize_token(str(status or "")))


def describe_trade_reason(reason: str) -> str:
    raw = str(reason or "").strip()
    if not raw:
        return "No analysis reason was provided."

    mapped = _REASON_LABELS.get(raw)
    if mapped:
        return mapped

    if raw.startswith("safety_blocked:"):
        inner = raw.split(":", 1)[1]
        return f"Safety monitor blocked the trade. {describe_trade_reason(inner)}"

    if raw.startswith("signal_changed:"):
        direction = raw.split(":", 1)[1].strip().upper()
        if direction in {"LONG", "SHORT"}:
            return f"The signal changed during final confirmation and now points {direction}."
        return "The signal changed during final confirmation."

    if raw.startswith("correlation_blocked_with_"):
        symbol = raw.removeprefix("correlation_blocked_with_").upper()
        return f"Correlation guard blocked the trade because {symbol} is already open."

    if raw.startswith("halted:") or raw.startswith("blocked_halted:"):
        detail = raw.split(":", 1)[1].strip()
        if detail:
            return f"Trading is halted: {detail.replace('_', ' ')}."
        return "Trading is halted."

    if raw.startswith("failed:"):
        detail = raw.split(":", 1)[1].strip()
        if detail:
            return f"Analysis failed with an error: {detail}"
        return "Analysis failed with an error."

    return f"{_humanize_token(raw)}."


def enrich_trade_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["display_status"] = describe_trade_status(str(payload.get("status") or ""))
    enriched["display_stage"] = describe_trade_stage(str(payload.get("stage") or ""))
    enriched["display_reason"] = describe_trade_reason(str(payload.get("reason") or ""))

    decision = payload.get("decision")
    if isinstance(decision, dict):
        normalized = dict(decision)
        normalized["display_reason"] = describe_trade_reason(str(decision.get("reason") or ""))
        enriched["decision"] = normalized

    last_opened_trade = payload.get("last_opened_trade")
    if isinstance(last_opened_trade, dict):
        normalized_last = dict(last_opened_trade)
        normalized_last["display_reason"] = describe_trade_reason(str(last_opened_trade.get("reason") or ""))
        enriched["last_opened_trade"] = normalized_last

    return enriched
