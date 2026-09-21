# SMC Trading Bot - Production Runbook

## Emergency Procedures

### Immediate halt

```bash
sudo systemctl stop smc-bot
pkill -f "python.*live_deploy" || true
```

### Emergency restart

```bash
sudo systemctl restart smc-bot
```

### Restore from backup

```bash
cd /home/darkage/Desktop/Bot
source venv/bin/activate
python scripts/backup.py --restore /home/darkage/Desktop/Bot/backups/backup_YYYYMMDD_HHMMSS.tar.gz
```

## Daily Checks

1. Service health
```bash
sudo systemctl status smc-bot
```

2. API health
```bash
curl -s http://localhost:3000/health | jq .
```

3. Runtime logs
```bash
sudo journalctl -u smc-bot --since "24 hours ago"
```

4. MT5 process
```bash
pgrep -f terminal64.exe
```

## Common Fixes

### Connection refused on ZMQ

```bash
ss -ltnp | grep -E '5555|5556|5557'
sudo systemctl restart smc-bot
```

### High memory usage

```bash
free -h
sudo systemctl restart smc-bot
```

### Database lock issues

```bash
lsof /home/darkage/Desktop/Bot/database/live_trades.db
```

## Recovery Scenarios

### Stuck in HALT

1. Inspect reason from `/health` and logs.
2. Verify drawdown/loss thresholds.
3. Wait cooldown or apply approved manual reset procedure.

### MT5 crashed

1. Restart MT5 terminal.
2. Verify broker login.
3. Restart bot service.

### Network outage

1. Confirm connectivity.
2. Check reconnect behavior in logs.
3. Restart service if session does not recover.

## Incident Log Template

```text
Date:
Time (UTC):
Incident ID:
Severity:
Description:
Root Cause:
Action Taken:
Resolution Time:
Prevention:
```
