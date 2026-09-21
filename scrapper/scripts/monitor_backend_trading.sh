#!/usr/bin/env bash
set -euo pipefail

INTERVAL_SECONDS="${1:-5}"
API_BASE="${API_BASE:-http://127.0.0.1:5100}"
DB_PATH="${DB_PATH:-runtime/data/trades.db}"
AUTH_TOKEN="${AUTH_TOKEN:-}"

if ! [[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ ]] || [ "$INTERVAL_SECONDS" -lt 1 ]; then
  echo "interval must be a positive integer (seconds)"
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 is required but not installed"
  exit 1
fi

while true; do
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  health_payload="$(curl -sS --max-time 3 "${API_BASE}/health" 2>/dev/null || echo '{"status":"down"}')"

  total_trades=0
  open_trades=0
  latest_trade=""
  latest_connection=""
  latest_safety=""

  if [ -f "$DB_PATH" ]; then
    counts="$(sqlite3 "$DB_PATH" "select coalesce(count(*),0)||'|'||coalesce(sum(case when status='open' then 1 else 0 end),0) from trades;" 2>/dev/null || echo "0|0")"
    total_trades="${counts%%|*}"
    open_trades="${counts##*|}"
    latest_trade="$(sqlite3 -separator ' | ' "$DB_PATH" "select id,symbol,direction,status,opened_at,coalesce(closed_at,''),coalesce(pnl,0) from trades order by id desc limit 1;" 2>/dev/null || true)"
    latest_connection="$(sqlite3 -separator ' | ' "$DB_PATH" "select id,status,timestamp,details from connection_events order by id desc limit 1;" 2>/dev/null || true)"
    latest_safety="$(sqlite3 -separator ' | ' "$DB_PATH" "select id,severity,event,timestamp,details from safety_events order by id desc limit 1;" 2>/dev/null || true)"
  fi

  echo "[$ts] health=${health_payload} total_trades=${total_trades} open_trades=${open_trades}"
  if [ -n "$latest_trade" ]; then
    echo "  latest_trade: ${latest_trade}"
  fi
  if [ -n "$latest_connection" ]; then
    echo "  latest_connection: ${latest_connection}"
  fi
  if [ -n "$latest_safety" ]; then
    echo "  latest_safety: ${latest_safety}"
  fi

  if [ -n "$AUTH_TOKEN" ]; then
    api_status="$(curl -sS --max-time 3 -H "Authorization: Bearer ${AUTH_TOKEN}" "${API_BASE}/api/status" 2>/dev/null || true)"
    if [ -n "$api_status" ]; then
      echo "  api_status: ${api_status}"
    fi
  fi

  echo
  sleep "$INTERVAL_SECONDS"
done
