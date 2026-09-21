#!/bin/bash
# Login to MT5 using credentials from config/.env via Wine Python

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/config/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing config/.env"
    echo "Create it: cp config/.env.example config/.env"
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

: "${MT5_LOGIN:?Missing MT5_LOGIN in config/.env}"
: "${MT5_PASSWORD:?Missing MT5_PASSWORD in config/.env}"
: "${MT5_SERVER:?Missing MT5_SERVER in config/.env}"

WINEPREFIX="${WINEPREFIX:-/home/$USER/.mt5}"

if [[ -z "${MT5_TERMINAL_PATH:-}" ]]; then
    if [[ -n "${MT5_WINE_PATH:-}" ]]; then
        prefix="${WINEPREFIX%/}/drive_c/"
        if [[ "$MT5_WINE_PATH" == "$prefix"* ]]; then
            rel="${MT5_WINE_PATH#$prefix}"
            MT5_TERMINAL_PATH="C:\\${rel//\//\\\\}"
        else
            MT5_TERMINAL_PATH="C:\\Program Files\\MetaTrader 5\\terminal64.exe"
        fi
    else
        MT5_TERMINAL_PATH="C:\\Program Files\\MetaTrader 5\\terminal64.exe"
    fi
fi

export WINEPREFIX
export MT5_TERMINAL_PATH
export MT5_LOGIN MT5_PASSWORD MT5_SERVER

WINEDLLOVERRIDES="ucrtbase=n" wine "C:\\Python313\\python.exe" - <<'PY'
import os
import MetaTrader5 as mt5

login = int(os.environ["MT5_LOGIN"])
password = os.environ["MT5_PASSWORD"]
server = os.environ["MT5_SERVER"]
path = os.environ.get("MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")

if not mt5.initialize(path=path):
    print("MT5_INIT_FAIL", mt5.last_error())
    raise SystemExit(1)

if mt5.login(login, password=password, server=server):
    print("MT5_LOGIN_OK")
    mt5.shutdown()
    raise SystemExit(0)

print("MT5_LOGIN_FAIL", mt5.last_error())
mt5.shutdown()
raise SystemExit(2)
PY
