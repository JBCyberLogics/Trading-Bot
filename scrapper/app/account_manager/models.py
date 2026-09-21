from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class AccountIn(BaseModel):
    account_name: str = Field(min_length=1)
    login: int
    password: str = Field(min_length=1)
    server: str = Field(min_length=1)
    broker_name: Optional[str] = None
    account_type: str = "Demo"


class AccountStored(AccountIn):
    id: str
    created_at: str
    updated_at: str

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


class AccountUpdate(BaseModel):
    account_name: Optional[str] = None
    login: Optional[int] = None
    password: Optional[str] = None
    server: Optional[str] = None
    broker_name: Optional[str] = None
    account_type: Optional[str] = None
