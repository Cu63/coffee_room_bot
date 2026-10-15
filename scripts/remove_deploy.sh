#!/usr/bin/env bash
# Убрать автодеплой из cron
set -euo pipefail
crontab -l | grep -v "deploy.sh" | crontab -
