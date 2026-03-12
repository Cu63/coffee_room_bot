# Установка и запуск

## Docker Compose (рекомендуется)

### Требования

- Docker Engine 24+
- Docker Compose v2
- Telegram-бот: создать через [@BotFather](https://t.me/BotFather)

### 1. Клонировать репозиторий

```bash
git clone https://github.com/Cu63/coffee_room_bot.git
cd coffee_room_bot
```

### 2. Создать `.env`

```bash
cp .env.example .env
```

Обязательно заполнить `BOT_TOKEN`. Остальное — по необходимости.

### 3. Настроить бота в BotFather

```
/setprivacy → Disable
/setjoingroups → Enable
```

### 4. Запустить

```bash
docker compose up -d --build
```

Docker Compose запустит:

| Сервис | Описание |
|--------|----------|
| `db` | PostgreSQL 16 |
| `redis` | Redis 7 |
| `flyway` | Применяет SQL-миграции |
| `openserp` | Поисковый движок для `/search` |
| `bot` | Telegram-бот |

### Полезные команды

```bash
docker compose logs -f bot     # логи бота
docker compose restart bot     # перезапуск (подхватит новый configs/)
docker compose down            # остановить всё
docker compose down -v         # остановить и удалить тома (сброс данных)
```

---

## Локальная разработка

### Требования

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- PostgreSQL 16 и Redis 7

### Установка зависимостей

```bash
uv sync
```

### Запустить инфраструктуру через Docker

```bash
docker compose up -d db redis
```

### Применить миграции

```bash
docker run --rm --network=host \
  -v $(pwd)/migrations:/flyway/sql \
  flyway/flyway:11 \
  -url=jdbc:postgresql://localhost:5432/scorebot \
  -user=scorebot -password=scorebot migrate
```

### Запустить бота

```bash
uv run python -m bot
```

---

## Переменные окружения

| Переменная | Обязательна | По умолчанию | Описание |
|------------|-------------|--------------|----------|
| `BOT_TOKEN` | ✅ | — | Токен бота от BotFather |
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://scorebot:scorebot@db:5432/scorebot` | PostgreSQL DSN |
| `REDIS_URL` | ✅ | `redis://redis:6379/0` | Redis URL |
| `AITUNNEL_API_KEY` | — | `""` | API-ключ для LLM (`/llm`, `/search`) |
| `OPENSERP_URL` | — | `http://openserp:7000` | URL поискового движка |
| `LOG_CHAT_ID` | — | `0` | Telegram chat ID для отправки логов (0 = выключено) |
| `LOG_LEVEL` | — | `ERROR` | Уровень логов в Telegram: `ERROR` / `WARNING` / `INFO` |
