from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config.schema import LoggingConfig


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def setup_logging(config: LoggingConfig, log_dir: Path) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = JsonFormatter()

    for output in config.outputs:
        out_type = str(getattr(output, "type", "")).lower()
        if out_type == "file":
            file_path = Path(getattr(output, "path", log_dir / "bot.log"))
            if not file_path.is_absolute():
                file_path = (log_dir / file_path).resolve()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            max_size_mb = int(getattr(output, "max_size_mb", 100))
            max_files = int(getattr(output, "max_files", 10))
            handler = RotatingFileHandler(file_path, maxBytes=max_size_mb * 1024 * 1024, backupCount=max_files)
            handler.setFormatter(formatter)
            root.addHandler(handler)
        elif out_type == "console" and bool(getattr(output, "enabled", True)):
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            root.addHandler(handler)

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)
