# Бэклог

Идеи и задачи по улучшению проекта. Перед реализацией каждого пункта — отдельно обсуждаем и уточняем детали.

---

## Архитектура

### Разбить крупные файлы на меньшие по смыслу

**Проблема:** handlers, di.py и service-файлы разрослись. Человеку сложно ориентироваться.

**Идея:**
- Каждый handler-файл — одна команда или одна связная группа (например `handlers/mute/`, `handlers/games/`)
- Вспомогательные функции выносить в `utils/` рядом с модулем, а не в глобальный utils
- В одной папке лежат +- одинаковые сущности

---

### Паттерн UseCase / Interactor вместо fat services

**Проблема:** сервисы сейчас несут слишком много ответственности.

**Идея:**
```
application/
  use_cases/
    score/
      apply_reaction.py      # class ApplyReactionUseCase
      transfer_score.py      # class TransferScoreUseCase
    mute/
      mute_user.py
    games/
      start_dice_game.py
      join_dice_game.py
```

Каждый UseCase — отдельный файл, один метод `execute()`. Общий код — в base-классе или хелперах.
Поведение не меняется, только структура.

---

### Общий код не дублировать

- Логика получения участника чата, форматирования имён, проверки прав — вынести в хелперы
- Вспомогательные функции в handlers дублируются (например `_parse_duration` есть в нескольких файлах) — централизовать

---

### Нейминг

- Привести к единому стилю: `PostgresXxxRepository`, `XxxService`, `XxxUseCase`
- Везде явно указывать что это за слой в имени
- Избегать аббревиатур там, где они неочевидны

---

## DI (Dependency Injection)

### Разбить di.py на несколько провайдеров

**Текущее состояние:** один `di.py` с двумя классами на всё.

**Цель:** каждый модуль несёт свой провайдер рядом с собой.

```
infrastructure/
  db/
    provider.py          # DatabaseProvider — пул, транзакции, репозитории
  redis/
    provider.py          # RedisProvider — клиент, RedisStore
application/
  games/
    provider.py          # GamesProvider — dice, blackjack, slots сервисы
  moderation/
    provider.py          # ModerationProvider — mute, tag, protect
```

**Стиль кода по образцу:**
```python
from dishka import Provider, from_context, Scope, provide_all

class DatabaseProvider(Provider):
    _config = from_context(Config, scope=Scope.APP)

    @provide(scope=Scope.REQUEST)
    async def get_tx_manager(self, pool: asyncpg.Pool) -> AsyncIterable[ITransactionManager]:
        ...
```

---

### Убрать `@inject` — перейти на `auto_inject=True`

**Текущее состояние:** каждый handler декорирован `@inject`.

**Цель:** установить `auto_inject=True` при `setup_dishka(container, dp, auto_inject=True)` и убрать все `@inject`.

---

### `Config` через `from_context` вместо `@provide`

Config загружается один раз при старте — передавать через `context={Config: config}` в `make_async_container`, а не через `@provide`.

---

## Конфигурация

### Перейти с PyYAML + pydantic-settings на dataclasses + adaptix

**Текущее состояние:** YAML-файлы монтируются в контейнер, pydantic-settings для .env.

**Цель:**
```python
from dataclasses import dataclass, field
from adaptix import Retort

@dataclass(slots=True)
class DiceConfig:
    min_bet: int = 1
    max_bet: int = 1000
    min_wait_seconds: int = 10
    max_wait_seconds: int = 3600

@dataclass(slots=True)
class Config:
    bot_token: str
    database: DatabaseConfig
    dice: DiceConfig = field(default_factory=DiceConfig)
    ...

config = Retort().load(load_from_env_or_file(), Config)
```

- Конфиг **не монтировать как файл** в docker-compose — указывать через env-переменные, чтобы перезапуск контейнера сразу видел изменения
- Или использовать `docker-compose.override.yml` для dev-режима

---

### Удалить устаревшие файлы конфигов в корне ✅

~~`config.yaml` и `messages.yaml` в корне~~ — уже удалены. Актуальные — в `configs/`.

---

## Логирование

### Заменить `logging` на `structlog`

**Цель:** структурированные логи (JSON в проде, цветной вывод в dev), легко парсятся.

```python
# infrastructure/logging.py
import logging
import structlog
from dataclasses import dataclass

@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    human_readable_logs: bool = True  # False → JSON (prod)

def setup_logger(config: LoggingConfig) -> None:
    common_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    handler = logging.StreamHandler()
    if config.human_readable_logs:
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
            foreign_pre_chain=common_processors,
        )
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=common_processors,
        )
    handler.setFormatter(formatter)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(config.level)

    structlog.configure(
        processors=[*common_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
```

- Пройтись по всему коду и расставить `info` / `debug` / `warning` / `error` / `exception` на нужном уровне
- DEBUG-переменные вынести в `LoggingConfig` (например `log_sql_queries: bool = False`)

---

## Точка входа (main.py)

### Вынести фоновые задачи в отдельный модуль

**Текущее состояние:** `main.py` содержит определения `cleanup_loop`, `unmute_loop` и т.д. — всё в одном файле.

**Цель:** `main.py` — только инициализация и запуск. Каждый loop — в своём модуле рядом с доменом.

```
infrastructure/
  loops/
    cleanup.py
    unmute.py
    dice.py         # уже есть
    giveaway.py     # уже есть
```

---

### Рассмотреть taskiq для фоновых задач

Вместо ручных `asyncio.create_task(while True: sleep(...))` — использовать [taskiq](https://taskiq-python.github.io/) с планировщиком.

- Cron-задачи через `@scheduler_task(cron="0 */6 * * *")`
- Можно использовать Redis как брокер (уже есть в стеке)
- Retry, dead-letter queue, мониторинг из коробки

---

## Модели и сущности

### Вынести все dataclass/DTO в отдельный пакет

**Текущее состояние:** сущности в `domain/`, DTO и result-объекты — в service-файлах.

**Цель:**
```
bot/
  domain/
    entities/
      user.py
      score.py
      mute.py
      dice.py
      giveaway.py
    value_objects/
      duration.py
      emoji.py
  application/
    dto/
      score_dto.py    # ApplyResult, SpendResult и т.д.
      dice_dto.py     # JoinResult, FinishResult и т.д.
```

---

## Тестирование

### Покрыть новые фичи unit-тестами

- `test_dice_service.py` — логика распределения призов, граничные случаи
- `test_score_service.py` — rate limits, применение реакций
- Интеграционные тесты с реальной БД (testcontainers или in-memory)

---

## Документация

### mkdocs + Material + GitHub Pages ✅ (задел сделан)

Структура задана. Нужно наполнить `docs/` реальным контентом:
- Docstrings для всех публичных классов и методов
- Auto-generated API reference через `mkdocstrings`

---

## DevOps

### .env.example актуализировать

Добавить все нужные переменные с комментариями и примерами значений.

---

### pre-commit хуки

- ruff format + ruff check
- Запрет коммитить `.env`
- Валидация миграций (нет дублей версий)
