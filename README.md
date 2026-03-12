# Coffee Room Bot

Telegram-бот для геймификации групповых чатов через реакции на сообщения. Пользователи зарабатывают и теряют очки (кирчики ⭐), участвуют в играх и борются за место в лидерборде.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-blue.svg)](https://aiogram.dev)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-green.svg)](https://cu63.github.io/coffee_room_bot)

---

## Возможности

| Категория | Описание |
|-----------|----------|
| **Реакции** | Emoji-реакции → ±очки. Настраиваемые веса. |
| **Лидерборд** | `/top` — топ участников чата |
| **История** | `/history` — лента начислений |
| **Мут** | `/mute @user 10m` — мут за очки. `/selfmute`. Защита `/protect`. |
| **Тег** | `/tag новый_тег` — персональный тег за очки |
| **Блекджек** | `/bj 50` — игра против дилера |
| **Слоты** | `/slots 25` — однорукий бандит с прогрессивным джекпотом |
| **Кости** | `/dice 10 2m` — мультиплеерная игра, победитель по наибольшему броску |
| **Гивэвей** | `/giveaway 500 100` — розыгрыш очков |
| **LLM** | `/llm вопрос`, `/search запрос` — AI-ответы через AiTunnel |

---

## Быстрый старт

### Требования

- Docker и Docker Compose
- Telegram-бот (создать через [@BotFather](https://t.me/BotFather))
- (Опционально) AiTunnel API-ключ для LLM-команд

### 1. Клонировать репозиторий

```bash
git clone https://github.com/Cu63/coffee_room_bot.git
cd coffee_room_bot
```

### 2. Создать `.env`

```bash
cp .env.example .env
```

Открыть `.env` и заполнить (минимум — `BOT_TOKEN`):

```dotenv
BOT_TOKEN=1234567890:AAxxxx...          # Обязательно
DATABASE_URL=postgresql+asyncpg://scorebot:scorebot@db:5432/scorebot
AITUNNEL_API_KEY=sk-aitunnel-xxx        # Опционально, для /llm и /search
REDIS_URL=redis://redis:6379/0
LOG_CHAT_ID=0                           # ID чата для логов (0 = выключено)
LOG_LEVEL=ERROR
```

### 3. Настроить бота в BotFather

```
/setprivacy → Disable        (бот видит все сообщения)
/setjoingroups → Enable
```

<details>
<summary>Список команд для /setcommands</summary>

```
score - Мой счёт
top - Топ участников
history - История начислений
bj - Блекджек (/bj <ставка>)
slots - Слоты (/slots <ставка>)
dice - Кости (/dice <ставка> <время>)
mute - Замутить пользователя
selfmute - Замутить себя
protect - Купить защиту от мута
tag - Изменить тег
transfer - Перевести очки
giveaway - Создать розыгрыш
llm - Вопрос к AI
search - Поиск с AI-ответом
help - Справка
```
</details>

### 4. Запустить

```bash
docker compose up -d --build
```

Миграции применяются автоматически через Flyway при старте.

---

## Локальная разработка

### Требования

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16 и Redis 7 (или через Docker)

### Установка и запуск

```bash
uv sync                              # установить зависимости

docker compose up -d db redis        # поднять инфраструктуру

# применить миграции
docker run --rm --network=host \
  -v $(pwd)/migrations:/flyway/sql \
  flyway/flyway:11 \
  -url=jdbc:postgresql://localhost:5432/scorebot \
  -user=scorebot -password=scorebot migrate

uv run python -m bot                 # запустить бота
```

### Полезные команды

```bash
just lint        # ruff format + ruff check --fix
just test        # pytest
just docs        # mkdocs dev-сервер (http://localhost:8000)
just docs-build  # собрать документацию в site/
```

---

## Конфигурация

### `configs/config.yaml` — основные параметры

Реакции и их веса, лимиты, стоимость мута/тега, параметры игр, интервалы фоновых задач. Редактировать без перезапуска кода (требуется перезапуск контейнера).

### `configs/messages.yaml` — тексты сообщений

Все пользовательские сообщения на русском. Редактировать без изменений Python-кода.

### `configs/help.yaml` — структура /help

Разделы, кнопки и тексты интерактивного меню `/help`.

### `.env` — секреты

| Переменная | Обязательна | Описание |
|------------|-------------|----------|
| `BOT_TOKEN` | ✅ | Токен бота от BotFather |
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://user:pass@host:port/db` |
| `REDIS_URL` | ✅ | `redis://host:port/db` |
| `AITUNNEL_API_KEY` | — | API-ключ для `/llm` и `/search` |
| `OPENSERP_URL` | — | URL openserp для `/search` |
| `LOG_CHAT_ID` | — | Telegram chat ID для логов (0 = выключено) |
| `LOG_LEVEL` | — | `ERROR` / `WARNING` / `INFO` |

---

## Структура проекта

```
coffee_room_bot/
├── bot/
│   ├── domain/          # Чистая бизнес-логика (без фреймворков)
│   ├── application/     # Сервисы + интерфейсы репозиториев
│   │   └── interfaces/
│   ├── infrastructure/  # PostgreSQL, Redis, DI, конфиги, фоновые задачи
│   │   └── db/
│   └── presentation/    # aiogram хендлеры и middleware
│       ├── handlers/
│       └── middlewares/
├── configs/             # YAML-конфиги
├── docs/                # Документация (mkdocs)
├── migrations/          # SQL-миграции Flyway (V001__description.sql)
└── tests/
```

Архитектура: **Domain ← Application ← Infrastructure ← Presentation**

Подробнее — в [документации](https://cu63.github.io/coffee_room_bot/architecture/).

---

## Миграции

Новая миграция — создать `migrations/V00N__description.sql`.

Применить вручную (локально):
```bash
docker run --rm --network=host \
  -v $(pwd)/migrations:/flyway/sql \
  flyway/flyway:11 \
  -url=jdbc:postgresql://localhost:5432/scorebot \
  -user=scorebot -password=scorebot migrate
```

---

## Технологии

- **Python 3.12** + async/await
- **aiogram 3** — Telegram Bot API
- **PostgreSQL 16** + asyncpg (raw SQL)
- **Redis 7** — состояние игровых сессий
- **Flyway** — миграции БД
- **dishka** — Dependency Injection
- **uv** — пакетный менеджер
- **ruff** — линтер / форматтер
- **mkdocs Material** — документация

---

## Лицензия

MIT — см. [LICENSE](LICENSE).
