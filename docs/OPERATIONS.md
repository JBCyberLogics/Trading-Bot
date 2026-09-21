# SMC Trading Bot - Production Operations Manual

## Overview

This document covers operational procedures for running the SMC/ICT trading bot in production.

## Quick Reference

| Action | Command |
|--------|---------|
| Start bot | `sudo systemctl start smc-bot` |
| Stop bot | `sudo systemctl stop smc-bot` |
| Restart bot | `sudo systemctl restart smc-bot` |
| Check status | `sudo systemctl status smc-bot` |
| View logs | `sudo journalctl -u smc-bot -f` |
| Health check | `curl http://localhost:3000/health` |
| Metrics | `curl http://localhost:3000/metrics` |

## Monitoring

### Health Endpoints

- `GET /health` - overall bot health
- `GET /ready` - readiness probe
- `GET /live` - liveness probe
- `GET /metrics` - Prometheus metrics payload

### Key Metrics

- `bot_account_balance`
- `bot_account_equity`
- `bot_drawdown_percent`
- `bot_risk_state` (0 normal, 1 caution, 2 defensive, 3 halt)
- `bot_trades_total`

### Alert Threshold Guidance

| Alert | Threshold | Action |
|-------|-----------|--------|
| Daily Loss | >2% | Immediate investigation |
| Drawdown | >6% | Reduce risk and review |
| Consecutive Losses | >=4 | Pause trading |
| System Error | Any | Investigate logs |

## Backup & Recovery

### Automatic Backups

- Frequency: every 6 hours
- Retention: 30 days
- Path: `/home/darkage/Desktop/Bot/backups`

### Manual Backup

```bash
cd /home/darkage/Desktop/Bot
source venv/bin/activate
python scripts/backup.py
```

### Restore

```bash
cd /home/darkage/Desktop/Bot
source venv/bin/activate
python scripts/backup.py --restore /home/darkage/Desktop/Bot/backups/backup_YYYYMMDD_HHMMSS.tar.gz
```

## Troubleshooting

### Bot does not start

1. `sudo systemctl status smc-bot`
2. `sudo journalctl -u smc-bot -n 100`
3. Confirm MT5 is running under Wine and ports are open.
4. Confirm `config/.env` has valid production values.

### MT5 connection issues

```bash
pgrep -f terminal64.exe
ss -ltnp | grep -E '5555|5556|5557'
```

### High drawdown

1. Check `/health` response.
2. Review latest entries in `logs/live/runtime_audit.jsonl`.
3. Stop bot if limits are breached.

## Security

- Never commit `.env` secrets.
- Keep `.env` at mode `600`.
- Rotate API and broker credentials periodically.
- Keep OS and dependencies updated.

## Maintenance Schedule

| Frequency | Task |
|-----------|------|
| Daily | Review logs and alerts |
| Weekly | Re-run walk-forward validation |
| Monthly | Backup restore drill + DB maintenance |
| Quarterly | Full risk parameter review |

## Incident Response

### Severity 1 (minor)
- transient warning, no risk breach
- restart specific component if needed

### Severity 2 (major)
- repeated connection failures or prolonged degraded behavior
- alert operators and pause trading if required

### Severity 3 (critical)
- HALT state, drawdown breach, or execution anomalies
- stop bot immediately and investigate root cause before restart
