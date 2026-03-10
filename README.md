# Score Bot — Telegram-бот системы начисления очков

Telegram-бот на базе **aiogram 3**, реализующий систему геймификации через реакции.
Участники чата получают и теряют очки, когда другие пользователи ставят эмодзи-реакции на их сообщения.

---

## Возможности

- Начисление/списание очков по реакциям с настраиваемыми весами
- Независимые счета в каждом чате
- Дневные лимиты на количество реакций и получаемых очков
- Игнорирование реакций на старые сообщения
- Защита от самонакрутки
- Автоматическая очистка устаревшей истории
- Команды `/score`, `/top`, `/history`

---

## Стек

| Компонент          | Технология                |
|--------------------|---------------------------|
| Фреймворк бота     | aiogram 3                 |
| База данных         | PostgreSQL 16             |
| Драйвер БД         | asyncpg (чистый SQL)      |
| Миграции            | Flyway                    |
| DI-контейнер        | dishka                    |
| Пакетный менеджер   | uv                        |
| Контейнеризация     | Docker + Docker Compose   |

---

## Требования

- Docker и Docker Compose (v2)
- Telegram Bot Token (получить у [@BotFather](https://t.me/BotFather))

Для локальной разработки без Docker:

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — пакетный менеджер
- PostgreSQL 16+

---

## Быстрый старт (Docker)

### 1. Клонировать репозиторий

```bash
git clone <your-repo-url>
cd score-bot
```

### 2. Создать `.env`

```bash
cp .env.example .env
```

Открыть `.env` и вписать токен бота:

```
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
DATABASE_URL=postgresql+asyncpg://scorebot:scorebot@db:5432/scorebot
```

### 3. Запустить

```bash
docker compose up --build -d
```

Порядок запуска автоматический:
1. **db** — PostgreSQL поднимается, проходит healthcheck
2. **flyway** — применяет миграции из `migrations/`
3. **bot** — стартует после успешной миграции

### 4. Проверить логи

```bash
docker compose logs -f bot
```

### 5. Остановить

```bash
docker compose down
```

Для полного сброса (включая данные БД):

```bash
docker compose down -v
```

---

## Локальная разработка (без Docker)

### 1. Установить зависимости

```bash
uv sync
```

### 2. Поднять PostgreSQL

Любым удобным способом — локально, через Docker или облачный сервис.

Пример через Docker (только БД):

```bash
docker run -d \
  --name scorebot-db \
  -e POSTGRES_USER=scorebot \
  -e POSTGRES_PASSWORD=scorebot \
  -e POSTGRES_DB=scorebot \
  -p 5432:5432 \
  postgres:16-alpine
```

### 3. Применить миграции

Через Flyway CLI:

```bash
flyway -url=jdbc:postgresql://localhost:5432/scorebot \
       -user=scorebot \
       -password=scorebot \
       -locations=filesystem:./migrations \
       migrate
```

Или через Flyway в Docker:

```bash
docker run --rm --network=host \
  -v $(pwd)/migrations:/flyway/sql \
  flyway/flyway:11 \
  -url=jdbc:postgresql://localhost:5432/scorebot \
  -user=scorebot \
  -password=scorebot \
  migrate
```

### 4. Настроить `.env`

```
BOT_TOKEN=your-token-here
DATABASE_URL=postgresql+asyncpg://scorebot:scorebot@localhost:5432/scorebot
```

### 5. Запустить бота

```bash
uv run python -m bot
```

---

## Настройка бота в BotFather

Чтобы бот видел реакции в чатах, необходимо:

1. Открыть [@BotFather](https://t.me/BotFather)
2. `/mybots` → выбрать бота → **Bot Settings** → **Group Privacy** → отключить (**Turn off**)
3. Добавить бота в чат как администратора (необязательно, но рекомендуется для стабильной работы)

> **Важно:** без отключения Group Privacy бот не будет видеть обычные сообщения и не сможет отслеживать их авторов для начисления очков по реакциям.

---

## Конфигурация

### `config.yaml` — основные настройки

```yaml
score:
  singular: "балл"        # 1 балл
  plural_few: "балла"     # 2-4 балла
  plural_many: "баллов"   # 5+ баллов
  icon: "⭐"

reactions:
  "👍": +1
  "❤️": +2
  "🔥": +3
  "👎": -1
  "💩": -2

self_reaction_allowed: false

limits:
  daily_reactions_given: 10     # макс. реакций от одного пользователя в сутки
  daily_score_received: 20      # макс. очков одному пользователю в сутки
  max_message_age_hours: 48     # реакции на сообщения старше N часов игнорируются

history:
  retention_days: 7             # сколько дней хранить историю событий
```

Добавление новой реакции — только правка `config.yaml` и перезапуск бота. Код менять не нужно.

### `messages.yaml` — тексты ответов

Все пользовательские тексты вынесены в отдельный файл. Можно локализовать или менять формулировки без правки кода.

### `.env` — секреты

| Переменная    | Описание                                           |
|---------------|----------------------------------------------------|
| `BOT_TOKEN`   | Токен Telegram-бота                                |
| `DATABASE_URL` | DSN подключения к PostgreSQL                       |

---

## Команды бота

| Команда              | Описание                                              |
|----------------------|-------------------------------------------------------|
| `/score`             | Показать свой счёт в текущем чате                     |
| `/score @username`   | Показать счёт указанного пользователя                 |
| `/top`               | Топ-10 участников чата по очкам                       |
| `/top N`             | Топ-N участников (макс. 50)                           |
| `/history`           | История начислений за последние N дней (из конфига)   |

---

## Миграции

Миграции хранятся в `migrations/` в формате Flyway:

```
migrations/
└── V001__initial_schema.sql
```

Для добавления новой миграции создайте файл по шаблону `V002__description.sql`.

При запуске через `docker compose` миграции применяются автоматически сервисом `flyway` до старта бота.

---

## Структура проекта

```
score-bot/
├── bot/
│   ├── domain/                    # Сущности и бизнес-правила
│   │   ├── entities.py
│   │   ├── pluralizer.py
│   │   └── reaction_registry.py
│   ├── application/               # Use cases и интерфейсы
│   │   ├── interfaces/
│   │   │   ├── transaction_manager.py
│   │   │   ├── score_repository.py
│   │   │   ├── event_repository.py
│   │   │   ├── daily_limits_repository.py
│   │   │   ├── user_repository.py
│   │   │   └── message_repository.py
│   │   ├── score_service.py
│   │   ├── leaderboard_service.py
│   │   ├── history_service.py
│   │   └── cleanup_service.py
│   ├── infrastructure/            # БД, конфиг, DI
│   │   ├── db/
│   │   │   ├── transaction_manager.py
│   │   │   ├── postgres_score_repository.py
│   │   │   ├── postgres_event_repository.py
│   │   │   ├── postgres_daily_limits_repository.py
│   │   │   ├── postgres_user_repository.py
│   │   │   └── postgres_message_repository.py
│   │   ├── config_loader.py
│   │   ├── message_formatter.py
│   │   └── di.py
│   ├── presentation/              # aiogram-хэндлеры
│   │   ├── handlers/
│   │   │   ├── reactions.py
│   │   │   └── commands.py
│   │   └── middlewares/
│   │       ├── chat_context.py
│   │       └── track_message.py
│   ├── main.py
│   └── __main__.py
├── migrations/
│   └── V001__initial_schema.sql
├── tests/
├── config.yaml
├── messages.yaml
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

### Слоистая архитектура

- **Domain** и **Application** не импортируют ничего из aiogram, asyncpg или dishka — тестируются изолированно.
- **Infrastructure** реализует интерфейсы из Application. Транзакции управляются исключительно через `ITransactionManager` (не в репозиториях, не в use cases).
- **Presentation** — единственный слой, знающий об aiogram. Зависимости прокидываются через dishka (`FromDishka[T]`).

---

## Лицензия

created by t.me/shared_mutex. На момент создания вообще всё полностью навайбкоженно, но по идее это надо будет исправить.
Справедливости ради оно и с учётом этого работает очень круто, поэтому да.

MIT