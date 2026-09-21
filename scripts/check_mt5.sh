#!/bin/bash
# Check MT5 status

if pgrep -f "terminal64.exe" > /dev/null; then
    echo "✅ MT5 is running"
else
    echo "❌ MT5 is not running"
    echo "Start it with: WINEPREFIX=~/.mt5 wine \"C:\\Program Files\\MetaTrader 5\\terminal64.exe\""
fi
