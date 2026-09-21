# SMC/ICT Trading Bot

Production-oriented SMC/ICT trading bot for MetaTrader 5 (Linux + Wine) with:
- multi-layer market analysis,
- adaptive risk state machine,
- self-learning champion/challenger engine,
- hard-gated live validation,
- production operations stack (health, metrics, alerts, backups, deployment automation).

## Status

Current implementation includes Phases 1-7:
- Phase 1-4: core strategy + learning engine.
- Phase 5: unified risk stack.
- Phase 6: validation and walk-forward gate.
- Phase 7: production deployment and operations assets.

## Core Architecture

- `src/core/`: bias, regime, state machine logic.
- `src/smc/`: POI detectors (`order_blocks`, `fair_value_gaps`).
- `src/risk/`: governor, correlation, session/news filters, circuit breakers, risk manager.
- `src/learning/`: trade DB, recorder, performance tracking, champion/challenger.
- `src/execution/`: ZeroMQ MT5 bridge client.
- `src/monitoring/`: logging, health server, metrics, alerts.
- `mql5/Experts/PythonBridgeEA.mq5`: MT5 EA bridge with `PING/ACCOUNT/POSITIONS/TRADE/RATES` actions.

## Prerequisites

- Linux host with Wine + MetaTrader 5 installed.
- Python 3.11+ (tested with 3.13).
- Active MT5 terminal session with `PythonBridgeEA` attached.
- ZeroMQ bridge ports reachable (`5555`, `5556`, `5557` by default).
- News API configured for live validation/trading gates.

## Installation

```bash
cd /home/darkage/Desktop/Bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Configuration

### 1) Environment variables

```bash
cp config/.env.example config/.env
```

Set at minimum:
- `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
- `NEWS_API_KEY`, `NEWS_API_ENDPOINT`
- `NEWS_RAPIDAPI_HOST` (if endpoint is on RapidAPI)
- `ZMQ_HOST`, `ZMQ_PYTHON_PORT`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (for signal/alert delivery)

Optional production profile:

```bash
cp config/.env.production config/.env
# then edit config/.env with real credentials/secrets
```

### 2) YAML configs

- `config/settings.yaml` (base defaults)
- `config/production.yaml` (production overrides)
- `config/parameters.yaml` (strategy parameters)

`config/loader.py` merges defaults + YAML + env vars, and honors `BOT_ENV` profile selection.

## Validation Before Launch

### Quick pre-start check (used by systemd)

```bash
venv/bin/python scripts/validate_for_live.py --quick
```

### Full live-readiness gate

```bash
venv/bin/python scripts/validate_for_live.py
```

This enforces:
- news feed health,
- MT5 connectivity + candle pull,
- phase tests,
- risk coverage threshold (`>=90%`),
- walk-forward thresholds.

Outputs:
- `reports/live_validation_summary.json`
- `reports/walk_forward_validation.html`

## Launch Guide

### A) Runtime smoke check

```bash
venv/bin/python scripts/live_deploy.py --symbol EURUSD --timeframe H1 --max-loops 5
```

### B) Real trading

```bash
venv/bin/python scripts/live_deploy.py --symbol EURUSD --timeframe H1
```

### C) Launch through main entrypoint

```bash
venv/bin/python main.py --live --symbol EURUSD --timeframe H1
```

### D) Learning maintenance commands

```bash
venv/bin/python main.py --learning-daily
venv/bin/python main.py --learning-weekly
venv/bin/python main.py --risk-report
```

## Runtime Safety Controls

- Runtime is live-only; there is no dry-run confirmation flag.
- Hard blocks on:
  - `HALT` risk state,
  - active circuit breakers,
  - news blocking conditions.
- Minimum lot floor and dynamic sizing via risk governor.
- Runtime audit/status files:
  - `logs/live/runtime_audit.jsonl`
  - `logs/live/status_snapshot.json`

## Monitoring

Official access URL:
- `http://localhost:3000/`

When running, health server exposes:
- `GET /` (official web UI dashboard)
- `GET /api/health`
- `GET /api/bot/status`
- `POST /api/bot/start`
- `POST /api/bot/stop`
- `GET /api/bot/config`
- `PUT /api/bot/config`
- `GET /api/account`
- `GET /api/positions`
- `GET /api/trades/recent?limit=50`
- `GET /api/performance?days=30`
- `GET /api/risk/state`
- `GET /api/symbols`
- `GET /api/fixed-rules`
- `GET /health` (legacy alias)
- `GET /ready`
- `GET /live`
- `GET /metrics`

Default port: `3000`.

Examples:

```bash
xdg-open http://localhost:3000/
curl http://localhost:3000/api/bot/status
curl http://localhost:3000/api/bot/config
curl http://localhost:3000/api/account
curl http://localhost:3000/api/fixed-rules
curl http://localhost:3000/api/trades/recent?limit=10
curl http://localhost:3000/api/risk/state
curl http://localhost:3000/api/health
curl http://localhost:3000/metrics
```

## Telegram Signal Support

The bot can push approved trade signals and execution notifications to Telegram.

### Enable

Set these in `config/.env`:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Test Telegram wiring

```bash
venv/bin/python scripts/test_telegram.py
```

### Get your Telegram chat ID

```bash
venv/bin/python scripts/get_telegram_chat_id.py
```

If no IDs are returned, send `/start` to your bot and run the command again.

## Backups

### Create backup

```bash
venv/bin/python scripts/backup.py
```

### Restore backup

```bash
venv/bin/python scripts/backup.py --restore backups/backup_YYYYMMDD_HHMMSS.tar.gz
```

Scheduled backup helper:

```bash
scripts/schedule_backup.sh
```

## Service Deployment (systemd)

Service file:
- `scripts/smc-bot.service`

Install locally:

```bash
scripts/install_service.sh
```

Service commands:

```bash
sudo systemctl status smc-bot
sudo systemctl restart smc-bot
sudo journalctl -u smc-bot -f
```

## VPS Deployment (Ansible)

Artifacts:
- `deploy/ansible/deploy.yml`
- `deploy/ansible/smc-bot.service.j2`
- `deploy/ansible/inventory.example`
- `deploy/deploy.sh`

Flow:

```bash
cp deploy/ansible/inventory.example deploy/ansible/inventory
# edit inventory host/user/key
./deploy/deploy.sh
```

## Testing

### Phase sequence

```bash
scripts/run_phases.sh
```

### Full unit/integration pass

```bash
venv/bin/pytest tests/unit tests/integration/test_risk_flow.py tests/integration/test_zmq_bridge.py -q
```

### Risk coverage gate

```bash
venv/bin/pytest --cov=src/risk --cov-fail-under=90 \
  tests/unit/test_risk_governor.py \
  tests/unit/test_risk_components.py \
  tests/integration/test_risk_flow.py
```

## Troubleshooting

### `RATES` returns `Unknown action`

Your running MT5 EA instance is outdated. Recompile/reload `mql5/Experts/PythonBridgeEA.mq5` in MT5.

### Validation fails: `NEWS_API_KEY is missing`

Set `NEWS_API_KEY` and `NEWS_API_ENDPOINT` in `config/.env`.

### Integration test depends on live MT5

`tests/integration/test_zmq_bridge.py` requires active MT5 bridge; `RATES` test auto-skips if EA lacks `RATES` action.

## Operations Docs

- [Operations Manual](docs/OPERATIONS.md)
- [Runbook](docs/RUNBOOK.md)
- [Production Readiness Checklist](docs/READINESS.md)

## Go-Live Checklist (Minimum)

1. Configure `config/.env` with real MT5/news credentials.
2. Run full validation and confirm `READY`.
3. Confirm fresh `reports/walk_forward_validation.html` (within 7 days for production gate).
4. Start with a short capped run (`--max-loops`) and review logs/health endpoints.
5. Launch full live runtime.
