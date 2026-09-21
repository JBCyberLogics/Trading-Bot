#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SYMBOL="${1:-XAUUSD}"
TIMEFRAME="${2:-H1}"
POLL_SECONDS="${POLL_SECONDS:-15}"

LIVE_SESSION_NAME="bot_xauusd_live"
STATUS_FILE="logs/live/status_snapshot.json"
AUDIT_FILE="logs/live/runtime_audit.jsonl"
MONITOR_LOG="logs/live/monitor_watchdog.log"
EVENT_LOG="logs/live/monitor_events.log"
LAUNCH_OUT="logs/live/launch.out"

mkdir -p logs/live
touch "$MONITOR_LOG" "$EVENT_LOG" "$AUDIT_FILE" "$LAUNCH_OUT"

last_audit_lines="$(wc -l < "$AUDIT_FILE" || echo 0)"

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log_monitor() {
  printf "%s %s\n" "$(utc_now)" "$1" >> "$MONITOR_LOG"
}

restart_live_bot() {
  log_monitor "live bot missing, attempting restart for ${SYMBOL}/${TIMEFRAME}"
  tmux has-session -t "$LIVE_SESSION_NAME" 2>/dev/null && tmux kill-session -t "$LIVE_SESSION_NAME" || true
  tmux new-session -d -s "$LIVE_SESSION_NAME" \
    "cd $ROOT_DIR && venv/bin/python scripts/live_deploy.py --symbol $SYMBOL --timeframe $TIMEFRAME >> $LAUNCH_OUT 2>&1"
  sleep 3
}

extract_status_summary() {
  if [[ ! -f "$STATUS_FILE" ]]; then
    echo "status_snapshot_missing"
    return
  fi
  jq -r '[.timestamp,.loop,.last_action.status,.risk.governor.state,.risk.governor.can_trade] | @tsv' "$STATUS_FILE" 2>/dev/null || echo "status_snapshot_parse_error"
}

capture_new_events() {
  local current_lines
  current_lines="$(wc -l < "$AUDIT_FILE" || echo 0)"
  if (( current_lines <= last_audit_lines )); then
    return
  fi

  local new_count
  new_count=$((current_lines - last_audit_lines))

  tail -n "$new_count" "$AUDIT_FILE" | while IFS= read -r line; do
    local event
    event="$(printf '%s' "$line" | jq -r '.event // ""' 2>/dev/null || echo "")"
    case "$event" in
      executed|execution_failed|rejected|loop_warning|loop_error|fatal|hard_stop)
        printf "%s %s\n" "$(utc_now)" "$line" >> "$EVENT_LOG"
        ;;
    esac
  done

  last_audit_lines="$current_lines"
}

log_monitor "watchdog started for ${SYMBOL}/${TIMEFRAME} (poll=${POLL_SECONDS}s)"

while true; do
  if ! pgrep -f "venv/bin/python scripts/live_deploy.py --symbol ${SYMBOL} --timeframe ${TIMEFRAME}" >/dev/null; then
    restart_live_bot
  fi

  status_summary="$(extract_status_summary)"
  log_monitor "heartbeat ${status_summary}"
  capture_new_events

  sleep "$POLL_SECONDS"
done
