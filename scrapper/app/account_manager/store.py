from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from app.account_manager.crypto import fernet_from_secret
from app.account_manager.models import AccountIn, AccountStored, AccountUpdate


class AccountStoreError(RuntimeError):
    pass


class EncryptedAccountStore:
    def __init__(self, store_dir: Path, encryption_key: str | None, max_accounts: int = 5) -> None:
        self.store_dir = store_dir
        self.store_file = self.store_dir / "accounts.json.enc"
        self.max_accounts = max(1, int(max_accounts))
        self._enabled = bool(encryption_key)
        self._fernet = fernet_from_secret(encryption_key) if encryption_key else None
        self.store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _read_all(self) -> Dict[str, AccountStored]:
        if not self.store_file.exists():
            return {}
        if not self._fernet:
            raise AccountStoreError("Encrypted account storage is disabled")
        raw = self.store_file.read_bytes()
        try:
            decrypted = self._fernet.decrypt(raw)
            payload = json.loads(decrypted.decode("utf-8"))
        except Exception as exc:
            raise AccountStoreError(f"Failed to decrypt account store: {exc}") from exc
        if not isinstance(payload, dict):
            return {}
        result: Dict[str, AccountStored] = {}
        for account_id, data in payload.items():
            if isinstance(data, dict):
                result[account_id] = AccountStored.model_validate(data)
        return result

    def _write_all(self, accounts: Dict[str, AccountStored]) -> None:
        if not self._fernet:
            raise AccountStoreError("Encrypted account storage is disabled")
        payload = {account_id: account.model_dump() for account_id, account in accounts.items()}
        encrypted = self._fernet.encrypt(json.dumps(payload, ensure_ascii=True).encode("utf-8"))
        self.store_file.write_bytes(encrypted)

    def list_accounts(self) -> List[AccountStored]:
        accounts = self._read_all() if self.enabled else {}
        return sorted(accounts.values(), key=lambda x: x.updated_at, reverse=True)

    def get(self, account_id: str) -> AccountStored | None:
        return self._read_all().get(account_id)

    def create(self, payload: AccountIn) -> AccountStored:
        accounts = self._read_all()
        if len(accounts) >= self.max_accounts:
            raise AccountStoreError(f"Maximum saved accounts reached ({self.max_accounts})")
        now = AccountStored.now_iso()
        account_id = str(uuid4())
        record = AccountStored(id=account_id, created_at=now, updated_at=now, **payload.model_dump())
        accounts[account_id] = record
        self._write_all(accounts)
        return record

    def update(self, account_id: str, payload: AccountUpdate) -> AccountStored:
        accounts = self._read_all()
        current = accounts.get(account_id)
        if current is None:
            raise AccountStoreError("Account not found")
        updated = current.model_copy(update={k: v for k, v in payload.model_dump().items() if v is not None})
        updated.updated_at = AccountStored.now_iso()
        accounts[account_id] = updated
        self._write_all(accounts)
        return updated

    def delete(self, account_id: str) -> None:
        accounts = self._read_all()
        if account_id in accounts:
            del accounts[account_id]
            self._write_all(accounts)
