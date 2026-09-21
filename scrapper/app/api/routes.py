from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.account_manager.models import AccountIn, AccountUpdate
from app.account_manager.store import AccountStoreError
from app.core.container import AppContainer
from app.core.orchestrator import ConnectionFlowError
from app.mt5_connection.market_fallback import (
    derive_order_book_from_rates,
    derive_tick_from_rates,
    is_unsupported_action_error,
)
from app.mt5_connection.zmq_bridge import parse_imbalance_from_order_book

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str


class ConnectRequest(BaseModel):
    account_id: str = Field(min_length=1)


class PairUpdateRequest(BaseModel):
    active: List[str]


class EmergencyRequest(BaseModel):
    reason: Optional[str] = None


class HaltResetRequest(BaseModel):
    reason: Optional[str] = None


class SafeConfigUpdate(BaseModel):
    active_pairs: Optional[List[str]] = None
    startup_auto_start_trading: Optional[bool] = None


async def _safe_to_thread(label: str, fn, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        payload = await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as exc:
        logger.warning("%s_failed err=%s", label, exc)
        return {"error": str(exc)}
    if isinstance(payload, dict):
        return payload
    return {"error": f"{label}_invalid_payload"}


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(status_code=500, detail="container_unavailable")
    return container


def require_auth(
    container: AppContainer = Depends(get_container),
    authorization: str | None = Header(default=None),
    x_session_token: str | None = Header(default=None),
) -> str:
    token = x_session_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token or not container.auth.validate(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return token


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health(container: AppContainer = Depends(get_container)) -> Dict[str, Any]:
        return {
            "status": "ok",
            "phase": container.loaded.config.phase,
            "connection_mode": container.loaded.config.connection_mode.value,
        }

    @router.post("/api/auth/login", response_model=LoginResponse)
    async def login(payload: LoginRequest, container: AppContainer = Depends(get_container)) -> LoginResponse:
        try:
            token = container.auth.login(payload.username, payload.password)
        except Exception:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
        return LoginResponse(token=token)

    @router.post("/api/auth/logout")
    async def logout(
        token: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        container.auth.logout(token)
        return {"ok": True}

    @router.get("/api/accounts")
    async def list_accounts(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if not container.loaded.runtime.encryption_enabled:
            raise HTTPException(status_code=503, detail=container.loaded.runtime.encryption_error)
        try:
            rows = container.account_service.list_accounts()
        except AccountStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"items": [row.model_dump(exclude={"password"}) for row in rows]}

    @router.post("/api/accounts")
    async def create_account(
        payload: AccountIn,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if not container.loaded.runtime.encryption_enabled:
            raise HTTPException(status_code=503, detail=container.loaded.runtime.encryption_error)

        def _test(login: int, password: str, server: str) -> tuple[bool, str]:
            # Quick synchronous bridge validation for save-time safety.
            try:
                result = container.orchestrator.bridge.login(login=login, password=password, server=server)
            except Exception as exc:
                return False, str(exc)
            if result.get("error"):
                return False, str(result.get("error"))
            return bool(result.get("ok", result.get("success", True))), "ok"

        auto_test = bool(
            container.loaded.config.account_manager.auto_test_on_save
            and container.orchestrator.connection.status == "connected"
        )
        try:
            row = container.account_service.create_account(payload, test_login_cb=_test, auto_test=auto_test)
        except AccountStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        container.alerts.emit("INFO", "account_added", f"Account added: {row.account_name}")
        return {"item": row.model_dump(exclude={"password"})}

    @router.put("/api/accounts/{account_id}")
    async def update_account(
        account_id: str,
        payload: AccountUpdate,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if not container.loaded.runtime.encryption_enabled:
            raise HTTPException(status_code=503, detail=container.loaded.runtime.encryption_error)
        try:
            row = container.account_service.update_account(account_id, payload)
        except AccountStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"item": row.model_dump(exclude={"password"})}

    @router.delete("/api/accounts/{account_id}")
    async def delete_account(
        account_id: str,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if not container.loaded.runtime.encryption_enabled:
            raise HTTPException(status_code=503, detail=container.loaded.runtime.encryption_error)
        try:
            container.account_service.delete_account(account_id)
        except AccountStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @router.post("/api/connection/connect")
    async def connect_account(
        payload: ConnectRequest,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if not container.loaded.runtime.encryption_enabled:
            raise HTTPException(status_code=503, detail=container.loaded.runtime.encryption_error)

        account = container.account_service.get_account(payload.account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="account_not_found")

        try:
            result = await container.orchestrator.connect_account(account)
        except ConnectionFlowError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return result

    @router.post("/api/connection/disconnect")
    async def disconnect(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        await container.orchestrator.disconnect(reason="manual")
        return {"status": "disconnected"}

    @router.get("/api/connection/status")
    async def connection_status(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return container.orchestrator.connection.snapshot()

    @router.get("/api/pairs")
    async def get_pairs(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return {
            "active": container.orchestrator.active_pairs,
            "available": container.orchestrator.available_pairs,
        }

    @router.put("/api/pairs/active")
    async def set_pairs(
        payload: PairUpdateRequest,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        active = container.orchestrator.set_active_pairs(payload.active)
        return {"active": active}

    @router.post("/api/pairs/refresh")
    async def refresh_pairs(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        available = await container.orchestrator.refresh_available_pairs(full_scan=False)
        container.orchestrator.schedule_pair_refresh(force=True)
        return {
            "active": container.orchestrator.active_pairs,
            "available": available,
            "count": len(available),
        }

    @router.post("/api/trading/start")
    async def trading_start(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.trading_start()

    @router.post("/api/trading/pause")
    async def trading_pause(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.trading_pause()

    @router.post("/api/trading/resume")
    async def trading_resume(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.trading_resume()

    @router.post("/api/trading/stop")
    async def trading_stop(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.trading_stop()

    @router.get("/api/trading/analysis")
    async def trading_analysis(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return container.orchestrator.trade_analysis_payload()

    @router.post("/api/emergency/stop")
    async def emergency_stop(
        payload: EmergencyRequest,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.emergency_stop(payload.reason or "manual_emergency_stop")

    @router.post("/api/system/reset-halt")
    async def reset_halt(
        payload: HaltResetRequest,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.reset_halt(payload.reason or "manual_dashboard_reset")

    @router.post("/api/emergency/close-all")
    async def close_all_positions(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return await container.orchestrator.close_all_positions()

    @router.get("/api/account")
    async def account_info(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if container.orchestrator.connection.status == "connected":
            payload = await asyncio.to_thread(container.orchestrator.bridge.get_account_info)
            if not payload.get("error"):
                normalized = dict(payload)
                normalized["connected"] = True
                normalized["source"] = "bridge"
                return normalized

        snapshot = container.orchestrator.db.account_latest()
        if snapshot:
            normalized = dict(snapshot)
            normalized["connected"] = False
            normalized["source"] = "cache"
            return normalized
        return {"connected": False, "source": "none"}

    @router.get("/api/positions")
    async def positions(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if container.orchestrator.connection.status != "connected":
            return {"positions": []}
        payload = await asyncio.to_thread(container.orchestrator.bridge.get_positions)
        if payload.get("error"):
            return {"positions": [], "error": payload.get("error")}
        rows = payload.get("positions")
        return {"positions": rows if isinstance(rows, list) else []}

    @router.get("/api/market/tick/{symbol}")
    async def market_tick(
        symbol: str,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        pair = str(symbol).upper()
        if container.orchestrator.connection.status != "connected":
            return {"symbol": pair, "connected": False, "error": "not_connected"}

        payload = await _safe_to_thread("market_tick", container.orchestrator.bridge.get_tick, pair)
        if payload.get("error"):
            fallback = await _safe_to_thread("market_tick_fallback", derive_tick_from_rates, container.orchestrator.bridge, pair)
            if not fallback.get("error"):
                return {
                    "symbol": pair,
                    "connected": True,
                    "bid": float(fallback.get("bid", 0.0) or 0.0),
                    "ask": float(fallback.get("ask", 0.0) or 0.0),
                    "spread_pips": float(fallback.get("spread_pips", 0.0) or 0.0),
                    "source": fallback.get("source", "derived_rates"),
                    "warning": str(payload.get("error")),
                }
            return {
                "symbol": pair,
                "connected": True,
                "error": str(fallback.get("error") or payload.get("error")),
                "warning": str(payload.get("error")),
            }

        bid = float(payload.get("bid", 0.0) or 0.0)
        ask = float(payload.get("ask", 0.0) or 0.0)
        pip = 0.01 if "JPY" in pair else 0.0001
        spread_pips = (ask - bid) / pip if pip > 0 else 0.0
        return {
            "symbol": pair,
            "connected": True,
            "bid": bid,
            "ask": ask,
            "spread_pips": round(float(spread_pips), 3),
            "source": "bridge",
        }

    @router.get("/api/market/order-book/{symbol}")
    async def market_order_book(
        symbol: str,
        levels: int = 10,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        pair = str(symbol).upper()
        if container.orchestrator.connection.status != "connected":
            return {"symbol": pair, "connected": False, "bids": [], "asks": [], "imbalance": 0.0, "error": "not_connected"}

        payload = await _safe_to_thread(
            "market_order_book",
            container.orchestrator.bridge.get_order_book,
            pair,
            max(1, min(50, levels)),
        )
        if payload.get("error"):
            fallback = await _safe_to_thread(
                "market_order_book_fallback",
                derive_order_book_from_rates,
                container.orchestrator.bridge,
                pair,
                levels=max(1, min(50, levels)),
            )
            if not fallback.get("error"):
                return {
                    "symbol": pair,
                    "connected": True,
                    "bids": fallback.get("bids") if isinstance(fallback.get("bids"), list) else [],
                    "asks": fallback.get("asks") if isinstance(fallback.get("asks"), list) else [],
                    "imbalance": float(fallback.get("imbalance", 0.0) or 0.0),
                    "source": fallback.get("source", "derived_rates"),
                    "warning": str(payload.get("error")),
                }
            return {
                "symbol": pair,
                "connected": True,
                "bids": [],
                "asks": [],
                "imbalance": 0.0,
                "error": str(fallback.get("error") or payload.get("error")),
                "warning": str(payload.get("error")),
            }

        bids = payload.get("bids") if isinstance(payload.get("bids"), list) else []
        asks = payload.get("asks") if isinstance(payload.get("asks"), list) else []
        return {
            "symbol": pair,
            "connected": True,
            "bids": bids,
            "asks": asks,
            "imbalance": parse_imbalance_from_order_book({"bids": bids, "asks": asks}),
            "source": "bridge",
        }

    @router.get("/api/market/rates/{symbol}")
    async def market_rates(
        symbol: str,
        timeframe: str = "M5",
        count: int = 48,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        pair = str(symbol).upper()
        tf = str(timeframe).upper()
        if container.orchestrator.connection.status != "connected":
            return {"symbol": pair, "timeframe": tf, "connected": False, "bars": [], "error": "not_connected"}

        payload = await _safe_to_thread(
            "market_rates",
            container.orchestrator.bridge.get_rates,
            pair,
            tf,
            max(5, min(500, count)),
        )
        if payload.get("error"):
            # If symbol action is unsupported upstream, return explicit error to keep UI state accurate.
            if is_unsupported_action_error(str(payload.get("error"))):
                return {"symbol": pair, "timeframe": tf, "connected": True, "bars": [], "error": str(payload.get("error"))}
            return {"symbol": pair, "timeframe": tf, "connected": True, "bars": [], "error": str(payload.get("error"))}

        bars = payload.get("bars")
        return {
            "symbol": pair,
            "timeframe": tf,
            "connected": True,
            "bars": bars if isinstance(bars, list) else [],
            "source": "bridge",
        }

    @router.get("/api/safety")
    async def safety(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return container.orchestrator.safety.snapshot()

    @router.get("/api/logs")
    async def logs(
        limit: int = 200,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        file_path = Path(container.loaded.runtime.log_directory) / "bot.log"
        lines: List[str] = []
        if file_path.exists():
            with file_path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()[-max(1, min(5000, limit)) :]
        return {
            "lines": [line.rstrip("\n") for line in lines],
            "alerts": container.alerts.recent(limit=min(200, limit)),
        }

    @router.get("/api/config")
    async def get_safe_config(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        cfg = container.loaded.config
        return {
            "version": cfg.version,
            "phase": cfg.phase,
            "connection_mode": cfg.connection_mode.value,
            "startup": cfg.startup.model_dump(),
            "trading_logic": {
                "name": cfg.trading_logic.name,
                "entry_rules": cfg.trading_logic.entry_rules.model_dump(),
                "exit_rules": cfg.trading_logic.exit_rules.model_dump(),
            },
            "pairs": {
                "active": container.orchestrator.active_pairs,
                "available": container.orchestrator.available_pairs,
            },
            "security": {
                "account_storage_enabled": container.loaded.runtime.encryption_enabled,
                "account_storage_error": container.loaded.runtime.encryption_error,
                "dashboard_auth_enabled": cfg.dashboard.authentication.enabled,
            },
        }

    @router.put("/api/config")
    async def update_safe_config(
        payload: SafeConfigUpdate,
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        if payload.active_pairs is not None:
            container.orchestrator.set_active_pairs(payload.active_pairs)
        if payload.startup_auto_start_trading is not None:
            container.loaded.config.startup.auto_start_trading = bool(payload.startup_auto_start_trading)
        container.save_state()
        return {"ok": True}

    @router.get("/api/status")
    async def status_payload(
        _: str = Depends(require_auth),
        container: AppContainer = Depends(get_container),
    ) -> Dict[str, Any]:
        return container.orchestrator.status_payload()

    return router
