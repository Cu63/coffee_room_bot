#!/usr/bin/env bash
set -euo pipefail

# Скрипт лежит в ./scripts/, проект — на уровень выше
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SCRIPT_PATH="${SCRIPT_DIR}/deploy.sh"

cd "$PROJECT_DIR"

# Самоустановка в крон при первом запуске
CRON_JOB="*/5 * * * * $SCRIPT_PATH >> /var/log/deploy.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "$SCRIPT_PATH"; then
    echo "[deploy] installing cron job..."
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "[deploy] cron job installed"
fi

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

echo "[deploy] $(date '+%Y-%m-%d %H:%M:%S') — starting${FORCE:+ (force)}"

# Запоминаем текущий коммит ДО пула
BEFORE=$(git rev-parse HEAD)

# Пуллим
if ! git pull origin master; then
    echo "[deploy] git pull failed, aborting"
    exit 1
fi

AFTER=$(git rev-parse HEAD)

# Ничего не изменилось — пропускаем, если не --force
if [[ "$BEFORE" == "$AFTER" ]] && [[ "$FORCE" == "false" ]]; then
    echo "[deploy] nothing changed, skipping"
    exit 0
fi

if [[ "$BEFORE" != "$AFTER" ]]; then
    echo "[deploy] updated $BEFORE -> $AFTER"
fi

# Если обновился сам скрипт — перезапускаем его новую версию
CHANGED_SCRIPTS=$(git diff --name-only "$BEFORE" "$AFTER" -- scripts/deploy.sh)
if [[ -n "$CHANGED_SCRIPTS" ]]; then
    echo "[deploy] script itself updated, re-executing new version"
    exec bash "$SCRIPT_PATH" "$@"
fi

# Пересобираем и перезапускаем весь стек
echo "[deploy] rebuilding all services..."
docker compose up -d --build

echo "[deploy] done"