# Конфигурация

Конфигурация разделена на три YAML-файла в папке `configs/` и файл `.env` для секретов.

## configs/config.yaml

Основные параметры бота. Изменения применяются после перезапуска контейнера.

### Валюта

```yaml
score:
  singular: "кирчик"     # 1 кирчик
  plural_few: "кирчика"  # 2–4 кирчика
  plural_many: "кирчиков" # 5+ кирчиков
  icon: "⭐"
```

### Реакции

```yaml
reactions:
  "❤️‍🔥": +3
  "🔥": +3
  "❤️": +2
  "👍": +1
  "👎": -1
  "💩": -2
  "🤡": -3
```

Добавить новую реакцию — просто добавить строку и перезапустить.

### Лимиты

```yaml
limits:
  daily_reactions_given: 25     # сколько засчитанных реакций можно поставить в сутки
  daily_score_received: 50      # сколько очков можно получить в сутки
  max_message_age_hours: 36     # реакции на сообщения старше N часов игнорируются
```

### Мут

```yaml
mute:
  cost_per_minute: 20           # стоимость 1 минуты мута
  min_minutes: 1
  max_minutes: 120
  selfmute_min_minutes: 1
  selfmute_max_minutes: 1440    # 24 часа
  protection_cost: 200          # стоимость /protect
  protection_duration_hours: 24
```

### Тег

```yaml
tag:
  cost_self: 50
  cost_member: 100
  cost_admin: 200
  cost_owner: 500
  max_length: 32
```

### Блекджек

```yaml
blackjack:
  min_bet: 1
  max_bet: 500
  max_games_per_window: 5   # ограничение: не более N игр в окне
  window_hours: 1
```

### Кости

```yaml
dice:
  min_bet: 1
  max_bet: 1000
  min_wait_seconds: 10      # минимальное время сбора участников
  max_wait_seconds: 3600    # максимальное время сбора (1 час)
```

### LLM

```yaml
llm:
  model: "gemini-2.5-flash-lite"
  base_url: "https://api.aitunnel.ru/v1"
  max_output_tokens: 4096
  daily_limit_per_user: 4   # запросов /llm + /search в сутки
  search_max_results: 7
```

### Системные параметры

```yaml
system:
  cleanup_interval_hours: 6       # как часто удалять устаревшие события и сообщения
  messages_retention_days: 30     # сколько дней хранить тексты сообщений
```

Тексты сообщений нужны саммари, `/analyze`, `/ff` и проверке реакций — им хватает последних
суток-двух. Старше `messages_retention_days` сообщения удаляются.

### Администраторы

```yaml
admin:
  prefix: "coffee"   # сообщения админов с этим префиксом не попадают в саммари чата
  users:
    - 123456789      # числовой id Telegram — надёжно
    - "username1"    # username тоже работает, но его можно сменить, и имя займёт другой
```

При старте бот пишет в лог предупреждение для каждого админа, заданного по username, и
подсказывает его id, если человек уже писал в чат.

Купленный через `/buyop` титул администратора прав модератора не даёт: отменить чужой режим
чата (`/chatmode off`) и настроить трекер (`/settracker`, `/settopic`) могут админ бота,
владелец чата и администраторы, которым разрешено удалять сообщения или ограничивать участников.

---

## configs/messages.yaml

Все пользовательские тексты. Поддерживаются шаблонные переменные `{user}`, `{delta}` и т.д.

Примеры:

```yaml
score_changed: "{user} {verb} {delta} {score_word}. Итого: {total} {score_word_total}."
mute_success: "🔇 {actor} мутит {target} на {minutes} мин за {cost} {score_word}."
```

---

## configs/help.yaml

Структура интерактивного меню `/help`: разделы, кнопки и тексты для каждого раздела. Редактировать без изменений кода.

---

## .env

Секреты. Никогда не коммитить в репозиторий (добавлен в `.gitignore`).

Пример (`cp .env.example .env`):

```dotenv
BOT_TOKEN=1234567890:AAxxxx...
DATABASE_URL=postgresql+asyncpg://scorebot:scorebot@db:5432/scorebot
REDIS_URL=redis://redis:6379/0
AITUNNEL_API_KEY=sk-aitunnel-xxx
LOG_CHAT_ID=0
LOG_LEVEL=ERROR
```
