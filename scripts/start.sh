#!/bin/bash
# Start the trading bot

source venv/bin/activate
python main.py --run-all "$@"
