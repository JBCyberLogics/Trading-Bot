#!/usr/bin/env python3
from __future__ import annotations

import getpass

from passlib.context import CryptContext


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def main() -> int:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm: ")
    if password != confirm:
        print("Passwords do not match")
        return 1
    print(pwd_context.hash(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
