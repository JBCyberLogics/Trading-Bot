#!/bin/bash
# Scheduled backup script for cron

set -euo pipefail

cd /home/darkage/Desktop/Bot
source venv/bin/activate
python scripts/backup.py >> logs/backup.log 2>&1
