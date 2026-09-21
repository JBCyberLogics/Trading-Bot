from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.core.container import AppContainer
from app.mt5_connection.manager import ConnectionContext


class DummyOrchestrator:
    def __init__(self, active_pairs: list[str], current_account_id: str | None = None) -> None:
        self.active_pairs = list(active_pairs)
        self.current_account = None
        self.set_active_pairs_calls: list[list[str]] = []
        if current_account_id:
            self.current_account = ConnectionContext(
                account_id=current_account_id,
                login=1001,
                password="secret",
                server="Broker-Demo",
            )

    def set_active_pairs(self, symbols: list[str]) -> list[str]:
        self.set_active_pairs_calls.append(list(symbols))
        self.active_pairs = list(symbols)
        return self.active_pairs


def _build_container(
    tmp_path: Path,
    *,
    active_pairs: list[str] | None = None,
    current_account_id: str | None = None,
    save_selection_on_exit: bool = True,
    load_last_pair_selection: bool = True,
    save_current_account: bool = True,
    auto_start_trading: bool = False,
) -> tuple[AppContainer, DummyOrchestrator]:
    orchestrator = DummyOrchestrator(active_pairs or ["EURUSD"], current_account_id=current_account_id)
    loaded = SimpleNamespace(
        runtime=SimpleNamespace(app_config_directory=tmp_path),
        config=SimpleNamespace(
            startup=SimpleNamespace(
                load_last_pair_selection=load_last_pair_selection,
                auto_start_trading=auto_start_trading,
            ),
            shutdown=SimpleNamespace(save_current_account=save_current_account),
            pairs=SimpleNamespace(
                pair_management=SimpleNamespace(save_selection_on_exit=save_selection_on_exit),
            ),
            mt5_connection=SimpleNamespace(
                connection_state=SimpleNamespace(current_account=None),
            ),
        ),
    )
    container = AppContainer(
        loaded=loaded,
        auth=SimpleNamespace(),
        account_service=SimpleNamespace(),
        orchestrator=orchestrator,
        alerts=SimpleNamespace(),
    )
    return container, orchestrator


def test_save_state_respects_optional_persistence_flags(tmp_path: Path) -> None:
    container, _ = _build_container(
        tmp_path,
        current_account_id="acct-42",
        save_selection_on_exit=False,
        save_current_account=False,
        auto_start_trading=True,
    )

    container.save_state()
    payload = json.loads(container.state_file.read_text(encoding="utf-8"))

    assert payload == {"startup_auto_start_trading": True}


def test_save_state_persists_pairs_and_current_account_when_enabled(tmp_path: Path) -> None:
    container, _ = _build_container(
        tmp_path,
        active_pairs=["EURUSD", "GBPUSD"],
        current_account_id="acct-7",
        save_selection_on_exit=True,
        save_current_account=True,
        auto_start_trading=False,
    )

    container.save_state()
    payload = json.loads(container.state_file.read_text(encoding="utf-8"))

    assert payload == {
        "active_pairs": ["EURUSD", "GBPUSD"],
        "current_account": "acct-7",
        "startup_auto_start_trading": False,
    }


def test_load_state_restores_saved_runtime_preferences(tmp_path: Path) -> None:
    container, orchestrator = _build_container(
        tmp_path,
        active_pairs=["USDJPY"],
        load_last_pair_selection=True,
        save_current_account=True,
        auto_start_trading=False,
    )
    container.state_file.write_text(
        json.dumps(
            {
                "active_pairs": [" eurusd ", "", "gbpusd"],
                "current_account": "acct-99",
                "startup_auto_start_trading": True,
            }
        ),
        encoding="utf-8",
    )

    container.load_state()

    assert orchestrator.active_pairs == ["EURUSD", "GBPUSD"]
    assert orchestrator.set_active_pairs_calls == [["EURUSD", "GBPUSD"]]
    assert container.loaded.config.startup.auto_start_trading is True
    assert container.loaded.config.mt5_connection.connection_state.current_account == "acct-99"


def test_load_state_skips_pair_restore_when_startup_restore_disabled(tmp_path: Path) -> None:
    container, orchestrator = _build_container(
        tmp_path,
        active_pairs=["USDJPY"],
        load_last_pair_selection=False,
        save_current_account=True,
        auto_start_trading=False,
    )
    container.state_file.write_text(
        json.dumps(
            {
                "active_pairs": ["EURUSD"],
                "current_account": "acct-13",
                "startup_auto_start_trading": True,
            }
        ),
        encoding="utf-8",
    )

    container.load_state()

    assert orchestrator.active_pairs == ["USDJPY"]
    assert orchestrator.set_active_pairs_calls == []
    assert container.loaded.config.startup.auto_start_trading is True
    assert container.loaded.config.mt5_connection.connection_state.current_account == "acct-13"
