from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn


if __name__ == "__main__":
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)
    host = os.getenv("BOT_BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("BOT_BACKEND_PORT", "5100"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
