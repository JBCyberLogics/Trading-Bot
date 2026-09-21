from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.account_manager.service import AccountService
from app.alerts.service import AlertService
from app.auth.service import AuthService
from app.config.loader import LoadedConfig
from app.core.orchestrator import BotOrchestrator


class AppContainer:
    def __init__(
        self,
        loaded: LoadedConfig,
        auth: AuthService,
        account_service: AccountService,
        orchestrator: BotOrchestrator,
        alerts: AlertService,
    ) -> None:
        self.loaded = loaded
        self.auth = auth
        self.account_service = account_service
        self.orchestrator = orchestrator
        self.alerts = alerts

    @property
    def state_file(self) -> Path:
        return self.loaded.runtime.app_config_directory / "state.json"

    def _state_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "startup_auto_start_trading": bool(self.loaded.config.startup.auto_start_trading),
        }
        if self.loaded.config.pairs.pair_management.save_selection_on_exit:
            payload["active_pairs"] = self.orchestrator.active_pairs
        if self.loaded.config.shutdown.save_current_account and self.orchestrator.current_account:
            payload["current_account"] = self.orchestrator.current_account.account_id
        return payload

    def save_state(self) -> None:
        payload = self._state_payload()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(payload, dict):
            auto_start = payload.get("startup_auto_start_trading")
            if isinstance(auto_start, bool):
                self.loaded.config.startup.auto_start_trading = auto_start

            if self.loaded.config.pairs.pair_management.save_selection_on_exit and self.loaded.config.startup.load_last_pair_selection:
                pairs = payload.get("active_pairs")
                if isinstance(pairs, list):
                    cleaned: List[str] = [str(item).upper().strip() for item in pairs if str(item).strip()]
                    if cleaned:
                        self.orchestrator.set_active_pairs(cleaned)

            if self.loaded.config.shutdown.save_current_account:
                current_account = payload.get("current_account")
                if isinstance(current_account, str) and current_account.strip():
                    self.loaded.config.mt5_connection.connection_state.current_account = current_account.strip()
