# Production Readiness Checklist

## Pre-Deployment Validation

### System Validation
- [ ] All unit tests pass
- [ ] Integration tests pass
- [ ] Walk-forward validation complete
- [ ] `reports/walk_forward_validation.html` reviewed (generated within last 7 days)
- [ ] Drawdown profile within limits
- [ ] Sharpe and win-rate meet configured thresholds

### Risk Management
- [ ] Production risk limits configured
- [ ] Circuit breakers tested
- [ ] Correlation controls verified
- [ ] Session/news filtering verified
- [ ] Emergency stop procedure confirmed

### Infrastructure
- [ ] VPS meets minimum CPU/RAM requirements
- [ ] Python/Wine/MT5 installed and configured
- [ ] ZeroMQ connectivity validated
- [ ] Firewall rules applied for required ports

### Database
- [ ] Production database path configured
- [ ] Backup process validated
- [ ] Retention policy configured

### Monitoring and Alerts
- [ ] `/health`, `/ready`, `/live`, `/metrics` reachable
- [ ] Telegram alert test passed
- [ ] Email alert test passed (if enabled)
- [ ] Log rotation and retention verified

### Security
- [ ] `config/.env` populated from secure source
- [ ] Secrets not committed to VCS
- [ ] File permissions hardened (`chmod 600 config/.env`)

## Controlled Rollout

### Stage 1 (minimum size)
- [ ] Run live with minimum lot size
- [ ] First 5 trades reviewed
- [ ] No critical runtime errors

### Stage 2 (gradual scale)
- [ ] Scale only after rollout trade target reached
- [ ] Risk state remains NORMAL/CAUTION under policy

### Stage 3 (full production)
- [ ] Stable operation period completed
- [ ] Team sign-off completed

## Final Sign-Off

Technical Lead: __________________  Date: __________

Risk Manager: ____________________ Date: __________

Operations: ______________________ Date: __________
