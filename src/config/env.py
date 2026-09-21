"""Environment loader for local .env configuration."""

from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv


def load_env(env_path: Path | None = None) -> Path:
    """Load environment variables from config/.env."""
    root = Path(__file__).resolve().parents[2]
    env_path = env_path or (root / "config" / ".env")
    load_dotenv(env_path)
    return env_path
