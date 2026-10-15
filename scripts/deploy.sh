#!/usr/bin/env bash
# Автодеплой: cron раз в минуту подтягивает master и пересобирает стек.
# Деплоится только коммит, у которого в GitHub Actions прошли проверки CI.
#
#   scripts/deploy.sh          — обычный запуск (так его вызывает cron)
#   scripts/deploy.sh --force  — пересобрать сейчас, не дожидаясь CI
#
# Необязательные переменные окружения:
#   DEPLOY_REPO     — owner/repo на GitHub (по умолчанию Cu63/coffee_room_bot)
#   DEPLOY_CHECKS   — обязательные проверки CI (по умолчанию "lint test integration docker")
#   DEPLOY_SKIP_CI  — 1: не ждать CI (аварийный режим)
#   GITHUB_TOKEN    — токен GitHub API; для публичного репозитория не нужен
set -euo pipefail
export HOME=/home/bot

# Скрипт лежит в ./scripts/, проект — на уровень выше
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SCRIPT_PATH="${SCRIPT_DIR}/deploy.sh"
cd "$PROJECT_DIR"

DEPLOY_REPO="${DEPLOY_REPO:-Cu63/coffee_room_bot}"
DEPLOY_CHECKS="${DEPLOY_CHECKS:-lint test integration docker}"
HEALTH_TIMEOUT_SECONDS=180  # сколько ждать, пока новый контейнер бота станет healthy
# Состояние — внутри .git, чтобы не попадать в рабочую копию
STATE_FILE=".git/deploy-deployed"   # последний успешно задеплоенный коммит
CI_FAILED_FILE=".git/deploy-ci-failed"  # коммит, у которого упал CI
BUILD_FAILED_FILE=".git/deploy-build-failed"  # "<коммит> <unix-время>" неудачной сборки
BUILD_RETRY_SECONDS=900

log() { echo "[deploy] $(date '+%Y-%m-%d %H:%M:%S') $*"; }

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

# Один экземпляр за раз: сборка может идти дольше минуты
exec 9>"/tmp/coffee_room_bot_deploy.lock"
if ! flock -n 9; then
    exit 0
fi

# Самоустановка в cron при первом запуске — не затирая остальные задания
CRON_JOB="*/1 * * * * $SCRIPT_PATH >> /var/log/deploy.log 2>&1"
if ! crontab -l 2>/dev/null | grep -qF "$SCRIPT_PATH"; then
    log "installing cron job..."
    { crontab -l 2>/dev/null || true; echo "$CRON_JOB"; } | crontab -
fi

if [[ "$(git rev-parse --abbrev-ref HEAD)" != "master" ]]; then
    log "working copy is not on master, aborting"
    exit 1
fi

git fetch --quiet origin master
TARGET="$(git rev-parse origin/master)"
HEAD_SHA="$(git rev-parse HEAD)"

# Первый запуск этой версии скрипта: задеплоенным считаем то, что уже в рабочей копии
if [[ ! -f "$STATE_FILE" ]]; then
    echo "$HEAD_SHA" > "$STATE_FILE"
fi
DEPLOYED="$(cat "$STATE_FILE")"

if [[ "$TARGET" == "$DEPLOYED" && "$FORCE" == false ]]; then
    exit 0
fi

# Сборка этого коммита уже падала — повторяем не чаще раза в BUILD_RETRY_SECONDS
if [[ -f "$BUILD_FAILED_FILE" && "$FORCE" == false ]]; then
    read -r failed_sha failed_at < "$BUILD_FAILED_FILE" || true
    if [[ "${failed_sha:-}" == "$TARGET" && $(( $(date +%s) - ${failed_at:-0} )) -lt $BUILD_RETRY_SECONDS ]]; then
        exit 0
    fi
fi

# Состояние одной проверки CI для коммита: success | pending | failure
check_state() {
    local sha=$1 name=$2 json status conclusion
    local auth=()
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
    fi
    if ! json=$(curl -fsS --max-time 20 -H "Accept: application/vnd.github+json" ${auth[@]+"${auth[@]}"} \
        "https://api.github.com/repos/${DEPLOY_REPO}/commits/${sha}/check-runs?check_name=${name}&filter=latest"); then
        echo pending  # GitHub недоступен — попробуем через минуту
        return
    fi
    status=$(grep -o '"status": *"[a-z_]*"' <<<"$json" | head -1 | sed 's/.*"\([a-z_]*\)"$/\1/' || true)
    conclusion=$(grep -o '"conclusion": *"[a-z_]*"' <<<"$json" | head -1 | sed 's/.*"\([a-z_]*\)"$/\1/' || true)
    if [[ "$status" != "completed" ]]; then
        echo pending  # проверка ещё идёт или ещё не началась
    elif [[ "$conclusion" == "success" ]]; then
        echo success
    else
        echo failure
    fi
}

if [[ "$FORCE" == false && "${DEPLOY_SKIP_CI:-0}" != "1" ]]; then
    # CI этого коммита уже упал — ждём следующий коммит, не дёргая GitHub API каждую минуту
    if [[ "$(cat "$CI_FAILED_FILE" 2>/dev/null || true)" == "$TARGET" ]]; then
        exit 0
    fi
    for check in $DEPLOY_CHECKS; do
        case "$(check_state "$TARGET" "$check")" in
            pending)
                exit 0
                ;;
            failure)
                log "CI check '$check' failed for $TARGET — not deploying"
                echo "$TARGET" > "$CI_FAILED_FILE"
                exit 0
                ;;
        esac
    done
fi

if [[ "$FORCE" == true ]]; then
    log "deploying $DEPLOYED -> $TARGET (force)"
else
    log "deploying $DEPLOYED -> $TARGET"
fi

if ! git merge --ff-only --quiet "$TARGET"; then
    log "git merge --ff-only failed (local changes on the server?), aborting"
    exit 1
fi

# Если обновился сам скрипт — продолжаем новой версией
if ! git diff --quiet "$HEAD_SHA" "$TARGET" -- scripts/deploy.sh; then
    log "deploy.sh updated, re-executing new version"
    exec 9>&-
    exec bash "$SCRIPT_PATH" "$@"
fi

# Ждём, пока healthcheck контейнера бота (пульс из bot/infrastructure/heartbeat.py) станет healthy
wait_bot_healthy() {
    local container status deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
    container="$(docker compose ps -q bot)"
    while (( $(date +%s) < deadline )); do
        status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || echo missing)"
        case "$status" in
            healthy|none) return 0 ;;  # none — образ без HEALTHCHECK, проверять нечем
        esac
        sleep 5
    done
    log "bot container is '$status' after ${HEALTH_TIMEOUT_SECONDS}s — see: docker compose logs --tail 100 bot"
    return 1
}

log "rebuilding all services..."
if docker compose up -d --build; then
    echo "$TARGET" > "$STATE_FILE"
    rm -f "$BUILD_FAILED_FILE"
    if wait_bot_healthy; then
        log "done: $TARGET"
    else
        log "deployed $TARGET, but the bot is not healthy"
        exit 1
    fi
else
    echo "$TARGET $(date +%s)" > "$BUILD_FAILED_FILE"
    log "docker compose failed for $TARGET, retry in $((BUILD_RETRY_SECONDS / 60)) min"
    exit 1
fi
