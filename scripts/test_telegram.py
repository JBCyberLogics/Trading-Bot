#!/usr/bin/env python3
"""Send a test Telegram message using current bot configuration."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_config
from src.monitoring.alerts import AlertSystem


def main() -> int:
    config = load_config(PROJECT_ROOT / "config")
    alerts = AlertSystem(config)

    sent = alerts.alert_signal(
        {
            "symbol": "EURUSD",
            "direction": "LONG",
            "entry_price": 1.10000,
            "stop_loss": 1.09800,
            "take_profit_1": 1.10200,
            "take_profit_2": 1.10400,
            "lots": 0.01,
            "confidence": 0.85,
            "rr_ratio": 1.5,
            "version_id": "CHAMPION_V1",
        },
        mode="TELEGRAM-TEST",
    )

    if sent:
        print("OK: Telegram test signal sent")
        return 0

    print("FAILED: Telegram not configured or send failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
