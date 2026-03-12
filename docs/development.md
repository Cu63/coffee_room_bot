# Разработка

## Настройка окружения

```bash
# Установить зависимости (включая dev)
uv sync

# Поднять инфраструктуру
docker compose up -d db redis

# Применить миграции
just migrate
```

## Команды

```bash
just lint        # ruff format + ruff check --fix
just test        # pytest
just docs        # mkdocs dev-сервер на http://localhost:8000
just docs-build  # собрать статику в site/
just migrate     # применить миграции через Docker
```

## Линтер

Проект использует [ruff](https://docs.astral.sh/ruff/). Конфигурация в `pyproject.toml`.

```bash
uv run ruff format          # автоформатирование
uv run ruff check --fix     # исправление lint-ошибок
```

## Тесты

```bash
uv run pytest               # запустить все тесты
uv run pytest tests/unit/   # только unit-тесты
```

Тесты находятся в `tests/unit/`. Интеграционные тесты — запланированы (см. [бэклог](backlog.md)).

## Добавить новую реакцию

1. Открыть `configs/config.yaml`
2. Добавить строку в секцию `reactions`:
   ```yaml
   "🆕": +2
   ```
3. Перезапустить бота

## Добавить новую миграцию

Создать файл `migrations/V00N__description.sql` (следующий номер по порядку).

```sql
-- V007__my_feature.sql
ALTER TABLE scores ADD COLUMN extra INT NOT NULL DEFAULT 0;
```

## Добавить новую команду

1. Создать хендлер в `bot/presentation/handlers/`
2. Если нужна бизнес-логика — добавить UseCase/сервис в `bot/application/`
3. Если нужна БД — добавить миграцию + репозиторий + интерфейс
4. Зарегистрировать сервис/репозиторий в `bot/infrastructure/di.py`
5. Подключить роутер в `bot/main.py`

## Структура коммитов

```
feat: краткое описание новой фичи
fix: описание исправления
refactor: описание рефакторинга
docs: обновление документации
chore: вспомогательные задачи (зависимости, CI и т.д.)
```

## Документация

Документация пишется в `docs/` в формате Markdown и автоматически публикуется на GitHub Pages при пуше в `master`.

Запустить локально:
```bash
just docs
```

Открыть: [http://localhost:8000](http://localhost:8000)
