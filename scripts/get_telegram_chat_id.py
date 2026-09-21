#!/usr/bin/env python3
"""Fetch chat IDs seen by the configured Telegram bot."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List, Set

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_config


def _extract_chat_ids(updates: List[Dict[str, Any]]) -> Set[int]:
    chat_ids: Set[int] = set()
    for item in updates:
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            msg = item.get(key) or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            if isinstance(chat_id, int):
                chat_ids.add(chat_id)
    return chat_ids


def main() -> int:
    config = load_config(PROJECT_ROOT / "config")
    token = config.get("telegram_bot_token")

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not configured")
        return 1

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(f"ERROR: failed to reach Telegram API: {exc}")
        return 1

    if not payload.get("ok"):
        print(f"ERROR: Telegram API returned non-ok payload: {payload}")
        return 1

    updates = payload.get("result", [])
    if not isinstance(updates, list):
        print("ERROR: unexpected Telegram payload format")
        return 1

    chat_ids = _extract_chat_ids(updates)
    if not chat_ids:
        print("No chat IDs found yet.")
        print("Next step: open your bot and send /start, then run this script again.")
        return 0

    print("Detected chat IDs:")
    for chat_id in sorted(chat_ids):
        print(chat_id)

    print("\nSet one of these in config/.env as TELEGRAM_CHAT_ID=...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
