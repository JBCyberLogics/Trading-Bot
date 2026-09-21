from __future__ import annotations

from pathlib import Path

import pytest

from app.account_manager.models import AccountIn
from app.account_manager.service import AccountService
from app.account_manager.store import AccountStoreError, EncryptedAccountStore


def test_encrypted_store_roundtrip(tmp_path: Path) -> None:
    store = EncryptedAccountStore(tmp_path / "accounts", encryption_key="secret", max_accounts=5)
    service = AccountService(store)

    payload = AccountIn(
        account_name="Demo",
        login=123456,
        password="pass",
        server="Broker-Demo",
        broker_name="Broker",
        account_type="Demo",
    )
    created = service.create_account(payload, auto_test=False)
    rows = service.list_accounts()

    assert store.enabled is True
    assert created.id
    assert len(rows) == 1
    assert rows[0].login == 123456


def test_account_service_disabled_without_key(tmp_path: Path) -> None:
    store = EncryptedAccountStore(tmp_path / "accounts", encryption_key=None, max_accounts=5)
    service = AccountService(store)

    with pytest.raises(AccountStoreError):
        service.list_accounts()
