from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text


async def _login(async_client, password: str) -> str:
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


async def _bootstrap_connected(async_client, password: str):
    token = await _login(async_client, password)
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/accounts",
        headers=headers,
        json={
            "account_name": "Ops Demo",
            "login": 998877,
            "password": "pass123",
            "server": "Broker-Demo",
            "broker_name": "Broker",
            "account_type": "Demo",
        },
    )
    assert create.status_code == 200

    account_id = (await async_client.get("/api/accounts", headers=headers)).json()["items"][0]["id"]
    connect = await async_client.post("/api/connection/connect", headers=headers, json={"account_id": account_id})
    assert connect.status_code == 200
    return headers


@pytest.mark.asyncio
async def test_data_scraper_writes_ticks(async_client, test_app, test_env: dict):
    headers = await _bootstrap_connected(async_client, test_env["password"])

    # Allow scraper loops to run.
    await asyncio.sleep(0.35)

    db = test_app.state.container.orchestrator.db
    with db.session() as session:
        count = session.execute(text("SELECT COUNT(*) FROM tick_data")).scalar_one()
    assert int(count) > 0

    start = await async_client.post("/api/trading/start", headers=headers, json={})
    assert start.status_code == 200
    await asyncio.sleep(1.1)

    safety = await async_client.get("/api/safety", headers=headers)
    assert safety.status_code == 200
    assert safety.json()["reason"] == "ok"
    assert safety.json()["trading_blocked"] is False


@pytest.mark.asyncio
async def test_start_triggers_immediate_trade_when_rules_pass(async_client, test_app, test_env: dict):
    headers = await _bootstrap_connected(async_client, test_env["password"])

    orchestrator = test_app.state.container.orchestrator
    orchestrator.loaded.config.safety_monitor.news_guard.enabled = False
    orchestrator.loaded.config.trading_logic.entry_rules.trading_hours.enabled = False
    orchestrator.loaded.config.trading_logic.entry_rules.order_book_imbalance.require_consecutive_readings = 1
    orchestrator.loaded.config.trading_logic.entry_rules.order_book_imbalance.min_imbalance_long = 0.1
    orchestrator.loaded.config.trading_logic.entry_rules.order_book_imbalance.min_imbalance_short = -0.1
    orchestrator.loaded.config.trading_logic.entry_rules.spread_requirement.max_pips = 5.0
    orchestrator.set_active_pairs(["EURUSD"])

    start = await async_client.post("/api/trading/start", headers=headers, json={})
    assert start.status_code == 200
    payload = start.json()
    assert payload.get("analysis") == "triggered"
    analysis = payload.get("trade_analysis", {}).get("pairs", {}).get("EURUSD", {})
    assert analysis.get("status") == "opened"
    assert analysis.get("reason") == "approved"
    assert analysis.get("display_reason") == "All entry checks passed and the order was approved."
    assert analysis.get("stage") == "order_submission"

    positions = await async_client.get("/api/positions", headers=headers)
    assert positions.status_code == 200
    assert len(positions.json().get("positions", [])) >= 1

    analysis_response = await async_client.get("/api/trading/analysis", headers=headers)
    assert analysis_response.status_code == 200
    latest = analysis_response.json().get("pairs", {}).get("EURUSD", {})
    assert latest.get("last_opened_trade", {}).get("reason") == "approved"


@pytest.mark.asyncio
async def test_execution_revalidates_signal_before_order(async_client, test_app, test_env: dict):
    headers = await _bootstrap_connected(async_client, test_env["password"])

    orchestrator = test_app.state.container.orchestrator
    orchestrator.loaded.config.safety_monitor.news_guard.enabled = False
    orchestrator.loaded.config.trading_logic.entry_rules.trading_hours.enabled = False
    orchestrator.loaded.config.trading_logic.entry_rules.order_book_imbalance.require_consecutive_readings = 1
    orchestrator.loaded.config.trading_logic.entry_rules.order_book_imbalance.min_imbalance_long = 0.1
    orchestrator.loaded.config.trading_logic.entry_rules.order_book_imbalance.min_imbalance_short = -0.1
    orchestrator.loaded.config.trading_logic.entry_rules.spread_requirement.max_pips = 5.0
    orchestrator.set_active_pairs(["EURUSD"])

    await orchestrator.scraper.stop()
    orchestrator.scraper.start = lambda: None
    orchestrator.scraper.last_tick.clear()
    orchestrator.scraper.last_order_book.clear()

    bullish = {"symbol": "EURUSD", "bids": [{"price": 1.0999, "volume": 25}], "asks": [{"price": 1.1001, "volume": 5}]}
    neutral = {"symbol": "EURUSD", "bids": [{"price": 1.0999, "volume": 10}], "asks": [{"price": 1.1001, "volume": 10}]}
    responses = [bullish, neutral]

    def sequenced_order_book(symbol: str, levels: int = 10):
        if responses:
            return responses.pop(0)
        return neutral

    orchestrator.bridge.get_order_book = sequenced_order_book

    start = await async_client.post("/api/trading/start", headers=headers, json={})
    assert start.status_code == 200
    payload = start.json()
    assert payload.get("analysis") == "triggered"
    analysis = payload.get("trade_analysis", {}).get("pairs", {}).get("EURUSD", {})
    assert analysis.get("status") == "rejected"
    assert analysis.get("stage") == "execution_recheck"
    assert analysis.get("reason") == "no_imbalance_signal"
    assert analysis.get("display_reason") == "Order book imbalance did not reach the LONG or SHORT threshold."

    positions = await async_client.get("/api/positions", headers=headers)
    assert positions.status_code == 200
    assert positions.json().get("positions", []) == []


@pytest.mark.asyncio
async def test_emergency_stop_halts_runtime(async_client, test_app, test_env: dict):
    headers = await _bootstrap_connected(async_client, test_env["password"])

    response = await async_client.post(
        "/api/emergency/stop",
        headers=headers,
        json={"reason": "integration_test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["halted"] is True

    status = await async_client.get("/api/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["halted"] is True
