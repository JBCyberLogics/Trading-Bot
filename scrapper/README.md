# Phase 1 Standalone Trading Bot (scrapper)

Standalone FastAPI trading bot implementation for Linux+Wine MT5 with:
- interactive account login/connect dashboard,
- ZMQ EA bridge MT5 integration,
- encrypted account storage,
- conservative Phase 1 trading engine,
- independent safety monitor and fail-closed controls,
- tick/order-book/OHLC/account data scraping into SQLite.

## Important Security Rules

- Dashboard auth is mandatory when enabled.
- Set `DASHBOARD_PASSWORD_HASH` or startup is blocked.
- Encrypted account persistence requires `BOT_ENCRYPTION_KEY`.
- If missing, account save/load endpoints are blocked.

## Quick Start

```bash
cd /home/darkage/Desktop/Bot/scrapper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Generate dashboard hash:

```bash
scripts/generate_password_hash.py
```

Set environment:

```bash
cp .env.example .env
# export values (or source via your shell)
export BOT_CONFIG_PATH=config/phase1_config.json
export DASHBOARD_PASSWORD_HASH='<bcrypt hash>'
export BOT_ENCRYPTION_KEY='change-me'
export MT5_ZMQ_HOST=127.0.0.1
export MT5_ZMQ_PORT=5555
```

Run:

```bash
python run.py
```

Or launch backend + frontend together:

```bash
./bot
```

Open:

- `http://localhost:5000/`

## API Endpoints

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET/POST /api/accounts`
- `PUT/DELETE /api/accounts/{account_id}`
- `POST /api/connection/connect`
- `POST /api/connection/disconnect`
- `GET /api/connection/status`
- `GET /api/pairs`
- `PUT /api/pairs/active`
- `POST /api/trading/start`
- `POST /api/trading/pause`
- `POST /api/trading/resume`
- `POST /api/trading/stop`
- `POST /api/emergency/stop`
- `POST /api/emergency/close-all`
- `GET /api/account`
- `GET /api/positions`
- `GET /api/safety`
- `GET /api/logs`
- `GET/PUT /api/config`

## Tests

```bash
pytest
```
