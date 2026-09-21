from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import Type

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.account_manager.models import AccountIn
from app.account_manager.service import AccountService
from app.account_manager.store import AccountStoreError, EncryptedAccountStore
from app.alerts.service import AlertService
from app.api.routes import build_router
from app.auth.service import AuthService
from app.config.loader import ConfigValidationError, LoadedConfig, load_config
from app.core.container import AppContainer
from app.core.logging import setup_logging
from app.core.orchestrator import BotOrchestrator
from app.data_scraper.service import DataScraperService
from app.mt5_connection.manager import ConnectionManager
from app.mt5_connection.zmq_bridge import ZMQBridge
from app.persistence.database import Database
from app.risk.service import RiskService
from app.safety_monitor.service import SafetyMonitor
from app.trading_logic.engine import TradingEngine


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

logger = logging.getLogger(__name__)


def _seed_account_from_env(container: AppContainer) -> None:
    runtime = container.loaded.runtime
    if not runtime.encryption_enabled:
        return
    if runtime.mt5_account_login is None or not runtime.mt5_account_password or not runtime.mt5_account_server:
        return

    try:
        existing = container.account_service.list_accounts()
        if any(
            row.login == runtime.mt5_account_login and row.server == runtime.mt5_account_server
            for row in existing
        ):
            return

        container.account_service.create_account(
            AccountIn(
                account_name=f"{runtime.mt5_account_login}@{runtime.mt5_account_server}",
                login=runtime.mt5_account_login,
                password=runtime.mt5_account_password,
                server=runtime.mt5_account_server,
                account_type="Demo",
            ),
            auto_test=False,
        )
        logger.info("Seeded default MT5 account from environment")
    except AccountStoreError as exc:
        logger.warning("Failed seeding default MT5 account: %s", exc)


def create_app(
    *,
    loaded_override: LoadedConfig | None = None,
    bridge_class: Type[ZMQBridge] = ZMQBridge,
) -> FastAPI:
    loaded = loaded_override or load_config()
    setup_logging(loaded.config.logging, loaded.runtime.log_directory)

    db = Database(loaded.runtime.database_path)
    bridge = bridge_class(
        host=loaded.runtime.mt5_zmq_host,
        port=loaded.runtime.mt5_zmq_port,
        timeout_ms=loaded.config.mt5_connection.server_config.connection_timeout_ms,
    )
    connection = ConnectionManager(loaded.config, bridge, db)

    account_store = EncryptedAccountStore(
        store_dir=loaded.runtime.credentials_store,
        encryption_key=loaded.runtime.encryption_key,
        max_accounts=loaded.config.account_manager.max_saved_accounts,
    )
    account_service = AccountService(account_store)

    auth = AuthService(
        username=loaded.config.dashboard.authentication.username,
        password_hash=loaded.runtime.dashboard_password_hash,
        session_timeout_minutes=loaded.config.dashboard.authentication.session_timeout_minutes,
    )

    risk = RiskService(loaded.config)
    safety = SafetyMonitor(loaded.config, loaded.runtime, db)

    orchestrator = BotOrchestrator(
        loaded=loaded,
        connection=connection,
        scraper=DataScraperService(
            config=loaded.config,
            db=db,
            bridge=bridge,
            symbols_provider=lambda: orchestrator.active_pairs,
            is_connected=lambda: connection.status == "connected",
        ),
        trading=TradingEngine(loaded.config),
        safety=safety,
        risk=risk,
        db=db,
        bridge=bridge,
        alerts=AlertService(loaded.config.alerts),
    )

    # Rewire scraper now that orchestrator exists.
    orchestrator.scraper.symbols_provider = lambda: orchestrator.active_pairs
    orchestrator.scraper.is_connected = lambda: connection.status == "connected"

    container = AppContainer(
        loaded=loaded,
        auth=auth,
        account_service=account_service,
        orchestrator=orchestrator,
        alerts=orchestrator.alerts,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container
        container.load_state()
        _seed_account_from_env(container)
        await orchestrator.startup()
        try:
            yield
        finally:
            if loaded.config.shutdown.save_state:
                container.save_state()
            await orchestrator.shutdown()

    app = FastAPI(
        title="Phase 1 Standalone Trading Bot",
        version=loaded.config.version,
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        template = Path(__file__).resolve().parent / "dashboard" / "templates" / "index.html"
        return HTMLResponse(template.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)

    app.include_router(build_router())
    return app


def _misconfigured_app(message: str) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        raise RuntimeError(message)
        yield

    app = FastAPI(
        title="Phase 1 Standalone Trading Bot",
        version="0.0.0-misconfigured",
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse)
    async def _root() -> HTMLResponse:
        return HTMLResponse(f"<h1>Configuration Error</h1><p>{message}</p>", headers=NO_CACHE_HEADERS)

    return app


try:
    app = create_app()
except ConfigValidationError as exc:
    app = _misconfigured_app(str(exc))
