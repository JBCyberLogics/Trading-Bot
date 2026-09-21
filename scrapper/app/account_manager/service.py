from __future__ import annotations

from typing import List

from app.account_manager.models import AccountIn, AccountStored, AccountUpdate
from app.account_manager.store import AccountStoreError, EncryptedAccountStore


class AccountService:
    def __init__(self, store: EncryptedAccountStore) -> None:
        self.store = store

    def ensure_enabled(self) -> None:
        if not self.store.enabled:
            raise AccountStoreError("Account persistence is disabled: missing BOT_ENCRYPTION_KEY")

    def list_accounts(self) -> List[AccountStored]:
        self.ensure_enabled()
        return self.store.list_accounts()

    def create_account(self, payload: AccountIn, *, test_login_cb=None, auto_test: bool = True) -> AccountStored:
        self.ensure_enabled()
        if auto_test and test_login_cb is not None:
            ok, reason = test_login_cb(payload.login, payload.password, payload.server)
            if not ok:
                raise AccountStoreError(f"Account validation failed: {reason}")
        return self.store.create(payload)

    def update_account(self, account_id: str, payload: AccountUpdate) -> AccountStored:
        self.ensure_enabled()
        return self.store.update(account_id, payload)

    def delete_account(self, account_id: str) -> None:
        self.ensure_enabled()
        self.store.delete(account_id)

    def get_account(self, account_id: str) -> AccountStored | None:
        self.ensure_enabled()
        return self.store.get(account_id)
