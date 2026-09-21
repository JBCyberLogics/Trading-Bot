from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from typing import Dict

from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class AuthError(RuntimeError):
    pass


class AuthService:
    def __init__(self, username: str, password_hash: str, session_timeout_minutes: int = 60) -> None:
        self.username = username
        self.password_hash = password_hash
        self.session_timeout_minutes = max(1, int(session_timeout_minutes))
        self.sessions: Dict[str, datetime] = {}

    def login(self, username: str, password: str) -> str:
        if username != self.username:
            raise AuthError("invalid_credentials")
        if not pwd_context.verify(password, self.password_hash):
            raise AuthError("invalid_credentials")

        token = secrets.token_urlsafe(32)
        self.sessions[token] = datetime.now(timezone.utc) + timedelta(minutes=self.session_timeout_minutes)
        return token

    def logout(self, token: str) -> None:
        self.sessions.pop(token, None)

    def validate(self, token: str) -> bool:
        expiry = self.sessions.get(token)
        if expiry is None:
            return False
        if expiry < datetime.now(timezone.utc):
            self.sessions.pop(token, None)
            return False
        return True
