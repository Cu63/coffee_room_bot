# Justfile — удобные команды для разработки
# Требует: just (https://github.com/casey/just)
# Установка: brew install just | cargo install just

# Показать список команд
default:
    @just --list

# ── Зависимости ─────────────────────────────────────────────────

# Установить все зависимости (включая dev и docs)
install:
    uv sync --all-groups

# ── Запуск ──────────────────────────────────────────────────────

# Запустить бота локально
run:
    uv run python -m bot

# Запустить через Docker Compose
up:
    docker compose up --build -d

# Остановить Docker Compose
down:
    docker compose down

# ── Линтер и форматтер ──────────────────────────────────────────

# Проверить код линтером (ruff check + ruff format --check)
lint:
    uv run ruff check .
    uv run ruff format --check .

# Автоматически исправить замечания линтера и отформатировать
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# ── Тесты ───────────────────────────────────────────────────────

# Запустить тесты
test:
    uv run pytest

# Запустить тесты с покрытием
test-cov:
    uv run pytest --cov=bot --cov-report=term-missing

# ── Миграции БД ─────────────────────────────────────────────────

# Применить Flyway-миграции (требует запущенный PostgreSQL)
migrate:
    docker run --rm --network=host \
        -v $(pwd)/migrations:/flyway/sql \
        flyway/flyway:11 \
        -url=jdbc:postgresql://localhost:5432/scorebot \
        -user=scorebot \
        -password=scorebot \
        migrate

# Проверить статус миграций
migrate-info:
    docker run --rm --network=host \
        -v $(pwd)/migrations:/flyway/sql \
        flyway/flyway:11 \
        -url=jdbc:postgresql://localhost:5432/scorebot \
        -user=scorebot \
        -password=scorebot \
        info

# ── Документация ────────────────────────────────────────────────

# Запустить сервер документации локально (http://127.0.0.1:8000)
docs:
    uv run mkdocs serve

# Собрать статику документации в папку site/
docs-build:
    uv run mkdocs build --strict

# Задеплоить документацию на GitHub Pages вручную
docs-deploy:
    uv run mkdocs gh-deploy --force
