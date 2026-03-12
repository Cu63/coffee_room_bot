# Бэклог

Идеи для улучшения архитектуры и кодовой базы. Не срочно — это технический долг и направления развития.

Полная версия: [BACKLOG.md](https://github.com/your-repo/blob/main/BACKLOG.md) в корне репозитория.

---

## Архитектура

### Разделение провайдеров DI

Текущий `di.py` содержит два провайдера в одном файле. Предлагается разбить:

```
bot/infrastructure/
  di/
    __init__.py
    app_provider.py      # Scope.APP — синглтоны
    request_provider.py  # Scope.REQUEST — per-request
```

### Паттерн Use Cases / Interactors

Добавить явный слой use cases между сервисами и хендлерами:

```
bot/application/use_cases/
  process_reaction.py
  join_dice_game.py
  ...
```

### `auto_inject=True` и `from_context`

Убрать boilerplate `@inject` + `FromDishka[T]` через конфигурацию dishka:

```python
setup_dishka(container, dp, auto_inject=True)
```

---

## Инфраструктура

### structlog вместо logging

Заменить стандартный `logging` на структурированные логи через `structlog`:

```python
import structlog
log = structlog.get_logger()
log.info("reaction_processed", user_id=user_id, score=score)
```

### adaptix для загрузки конфигурации

Заменить ручной парсинг YAML на `adaptix.Retort`:

```python
from adaptix import Retort
retort = Retort()
config = retort.load(raw_yaml, AppConfig)
```

### taskiq для фоновых задач

Заменить `asyncio.create_task(while True)` на `taskiq`:

```python
@broker.task(schedule=[{"cron": "*/1 * * * *"}])
async def check_expired_mutes(): ...
```

---

## Качество кода

### Pre-commit хуки

Добавить `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    hooks:
      - id: mypy
```

### Тесты

Директории `tests/` пустые. Приоритетные цели:

- Unit-тесты для доменной логики (`domain/`)
- Интеграционные тесты для сервисов с тестовой БД
- Pytest + pytest-asyncio + testcontainers

### Разделение models

При росте проекта вынести dataclasses в отдельный пакет:

```
bot/domain/
  models/
    user.py
    reaction.py
    game.py
```

---

## Документация

- Добавить docstrings к публичным методам сервисов
- Настроить `mkdocstrings` для автогенерации API-документации
- ADR (Architecture Decision Records) для ключевых решений
