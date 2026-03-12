# Архитектура

## Слои

Проект следует строгой слоистой архитектуре. Зависимости направлены только снизу вверх:

```
Presentation
    ↓
Infrastructure
    ↓
Application
    ↓
Domain
```

Нижние слои ничего не знают о верхних.

---

## Domain (`bot/domain/`)

Чистая бизнес-логика. Нет импортов aiogram, asyncpg или других фреймворков.

- **Entities** (`entities.py`, `dice_entities.py`, `giveaway_entities.py`) — датаклассы: `User`, `Score`, `MuteEntry`, `DiceGame`, `Giveaway`
- **ReactionRegistry** — маппинг emoji → вес реакции
- **ScorePluralizer** — склонение названия валюты (русские правила)
- **bot_utils** — `parse_duration()`, `format_duration()`, `is_admin()`
- **tz** — часовой пояс (МСК, UTC+3)

---

## Application (`bot/application/`)

Сервисы (use cases) и интерфейсы репозиториев.

### Сервисы

| Сервис | Ответственность |
|--------|-----------------|
| `ScoreService` | Применение реакций, изменение очков, лимиты |
| `LeaderboardService` | Топ участников |
| `HistoryService` | Лента событий |
| `CleanupService` | Удаление устаревших событий |
| `MuteService` | Управление мутами |
| `DiceService` | Игра в кости: создание, вступление, завершение |
| `BlackjackService` | Колода, логика раздачи, подсчёт очков |
| `SlotsService` | Барабаны, RTP, джекпот |
| `GiveawayService` | Розыгрыши: создание, вступление, финиш |
| `LlmService` | LLM-запросы и веб-поиск |

### Интерфейсы (`interfaces/`)

Абстрактные базовые классы репозиториев (`IScoreRepository`, `IDiceRepository` и т.д.). Сервисы зависят только от интерфейсов — конкретных реализаций не знают.

---

## Infrastructure (`bot/infrastructure/`)

Реализации репозиториев, DI-контейнер, загрузка конфигов, фоновые задачи.

### База данных (`db/`)

PostgreSQL через asyncpg (raw SQL, без ORM). Каждый репозиторий реализует свой интерфейс.

| Таблица | Назначение |
|---------|------------|
| `users` | Telegram-пользователи |
| `scores` | Счёт per user per chat |
| `messages` | Отслеживание сообщений для реакций |
| `score_events` | История начислений |
| `daily_limits` | Дневные квоты |
| `active_mutes` | Активные муты |
| `saved_permissions` | Сохранённые права администраторов |
| `llm_requests` | Лог LLM-запросов |
| `giveaways` + `participants` + `winners` | Розыгрыши |
| `dice_games` + `dice_participants` | Игры в кости |
| `mute_protection` | Защита от мута |

### Транзакции

`TransactionManager` оборачивает соединение: начинает транзакцию на входе в скоп, коммитит при успехе, откатывает при исключении. Репозитории никогда не управляют транзакциями сами.

### DI (dishka)

Два провайдера:

- **`AppProvider`** (Scope.APP) — синглтоны: конфиг, пулы подключений, реестры
- **`RequestProvider`** (Scope.REQUEST) — per-handler: транзакция, репозитории, сервисы

### Redis (`redis_store.py`)

Хранит состояние активных игровых сессий (блекджек, слоты, мут-рулетка) с TTL.

### Фоновые задачи

| Файл | Задача | Интервал |
|------|--------|----------|
| `giveaway_loop.py` | Завершение просроченных розыгрышей | 60 сек |
| `dice_loop.py` | Разрешение игр в кости | 5 сек |
| В `main.py` | Очистка старых событий | 6 ч |
| В `main.py` | Снятие истёкших мутов | 60 сек |
| В `main.py` | Завершение мут-рулеток | 10 сек |

---

## Presentation (`bot/presentation/`)

Только этот слой знает про aiogram.

### Handlers (`handlers/`)

| Файл | Команды |
|------|---------|
| `commands.py` | `/score`, `/top`, `/history`, `/limits`, `/transfer` |
| `reactions.py` | Обработка emoji-реакций |
| `admin_commands.py` | Все административные команды |
| `blackjack.py` | `/bj` + inline-кнопки Hit/Stand |
| `slots.py` | `/slots` |
| `dice.py` | `/dice` + inline-кнопка «Участвовать» |
| `giveaway.py` | `/giveaway`, `/giveaway_end`, `/mutegiveaway` |
| `llm_commands.py` | `/llm`, `/search` |
| `help_renderer.py` | `/help` (динамическое меню из YAML) |

### Middlewares (`middlewares/`)

- **`ChatContextMiddleware`** — фильтрует личные сообщения, пропускает только групповые
- **`TrackMessageMiddleware`** — записывает автора и время сообщения (нужно для реакций)

---

## Поток данных: обработка реакции

```
Telegram → aiogram MessageReaction update
  → ChatContextMiddleware (только группы)
  → reactions handler
    → ScoreService.apply_reaction(actor_id, chat_id, msg_id, emoji)
      → MessageRepository.get (есть ли сообщение в БД?)
      → ScoreRepository.get (проверка отрицательного баланса актора)
      → DailyLimitsRepository.check_and_increment
      → EventRepository.exists (дубль реакции?)
      → ScoreRepository.add_delta (изменить счёт)
      → EventRepository.create (записать событие)
    → TransactionManager.commit()
```
