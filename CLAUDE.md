# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot (aiogram 3) for group-chat gamification: an internal currency ("баллы") earned through emoji reactions, spent on paid actions (tag, buyop, …), wager games (blackjack, tic-tac-toe, duels, dice, slots, word games, lot auctions) and LLM commands. The bot works in several chats; balances are per chat. Everything user-facing is in Russian (texts, comments, config).

## Key Commands

```bash
uv sync                                    # dependencies (the dev group is installed by default)
uv run python -m bot                       # run locally: needs PostgreSQL, Redis and .env
uv run pytest tests/unit                   # unit tests
uv run pytest tests/integration            # integration + e2e: needs TEST_DATABASE_URL and TEST_REDIS_URL, see Tests
uv run ruff check . && uv run ruff format --check .   # lint, exactly as CI runs it
docker compose up --build -d               # full stack
just migrate                               # Flyway migrations (see Justfile)
```

CI (`.github/workflows/ci.yml`) runs four jobs: `lint` (ruff check + ruff format --check), `test` (pytest `tests/unit`), `integration` (pytest `tests/integration` against Postgres 16 and Redis 7 service containers) and `docker` (builds the image, checks it runs as non-root and imports). Keep all four green: `scripts/deploy.sh` on the server deploys `master` only after they pass, then waits up to 3 minutes for the bot container to become `healthy`.

## Architecture

Layers, dependency direction: Domain ← Application ← Infrastructure ← Presentation. Some application modules still import config models and clients from infrastructure — don't add new violations.

- **Domain** (`bot/domain/`): entities, emoji utilities, `ReactionRegistry`, pluralization, MSK time zone (`tz.py`). No framework imports.
- **Application** (`bot/application/`): services + repository interfaces (`interfaces/`). Many features still keep their logic in handlers; when changing a feature, prefer moving logic into a service.
- **Infrastructure** (`bot/infrastructure/`): Postgres repositories (`db/`, raw SQL via asyncpg), Redis store (`redis/`: `RedisStore` is a facade over per-feature mixins), config loading, background loops (`*_loop.py`), LLM/HTTP clients, `bootstrap.py` (middleware and router registration).
- **Presentation** (`bot/presentation/`): aiogram handlers (`handlers/`, multi-file features are packages), middlewares, `utils.py`.

### DI (dishka)

Providers, re-exported from `bot/infrastructure/di.py`:
- `DatabaseProvider` — asyncpg pool (APP), transaction manager and repositories (REQUEST).
- `RedisProvider` — Redis client from container context, `RedisStore` (APP).
- `LlmProvider` — LLM/GigaChat clients.
- `AppServiceProvider` (`bot/application/provider.py`) — services (REQUEST).
- `PresentationProvider` (`bot/presentation/provider.py`) — settings, `AppConfig`, pluralizer, `MessageFormatter`, `ReactionRegistry`, help renderer.

Handlers get dependencies via `FromDishka[T]`, middlewares via `data["dishka_container"]`, background loops via `async with container() as scope`.

### Transactions

One Postgres transaction per update (or per loop scope), opened lazily by the first query: repositories get a `LazyConnection` from `PostgresTransactionManager`, and `DatabaseProvider.get_tx_manager` finishes the transaction when the scope closes. dishka hands the handler's exception to the generator as the value of `yield` (`asend(exc)`), not by raising it inside.

- Any exception except `TelegramAPIError` → rollback. Success or a Telegram API error → commit (an external effect such as a sent message or a mute may already have happened).
- To roll back on purpose, answer the user and raise `AbortUpdate` (`bot/presentation/errors.py`).
- `ITransactionManager.checkpoint()` commits what has been done and returns the connection to the pool; the next query opens a new transaction. The pool has 5 connections, so call it before every slow external call (LLM, web search) — the LLM services and `TrackMessageMiddleware` already do. Work before a checkpoint survives a later rollback: a debit made before it needs an explicit refund on failure that is itself committed (`GenAiService.refund`).
- LLM commands allow one request in flight per user (`OneLlmRequestPerUserMiddleware` on their routers); otherwise parallel requests slip past the daily limits.
- Handlers are never re-run on network errors: only idempotent Bot API requests are retried (`RetryIdempotentRequestsMiddleware`).
- Redis writes are not part of the transaction. Order the steps so that a rollback cannot leave Redis ahead of Postgres.
- An optional write after money has moved (stats, leaderboard, IQ) goes inside `async with tx.savepoint():` with its own `try/except` that logs. Without a savepoint a failed query aborts the whole transaction, and the prize rolls back together with it.

### Money and game state

- Debit only through atomic primitives: `ScoreService.spend_score` / `transfer_score`, `IScoreRepository.try_debit` (`UPDATE … WHERE value >= amount`) or `debit_up_to`. Never "read the balance, then `add_delta(-x)`".
- Every change to `scores.value` is journaled into `ledger` by a DB trigger (`V017__ledger.sql`). The operation kind (`LedgerKind`: reaction, transfer, purchase, stake, prize, refund, bonus, penalty, tax, admin, opening), `ref` (what exactly: `msg:<id>:<emoji>`, `mute`, `bj:<game_id>`…) and `counterparty_id` reach the trigger through transaction settings set by `PostgresScoreRepository` — that's why every balance-changing method of `IScoreRepository` and `ScoreService` requires `kind`. Both legs of one operation (buyer and house, sender and receiver) share the kind. A change made around the repository lands as kind `unknown`: the daily `ledger_audit_loop` reports it, and so does the teardown check after every integration test.
- Stakes of turn-based games (BJ, TTT, duels) go through `StakeService`: `hold` debits the player and puts the stake into the game's pot (`game_pots`, keyed by the game's ledger ref), `pay` takes prizes and refunds out of the pot and raises `PotOverdraft` when the pot is short, so a second settlement of the same game rolls back instead of minting money. New games carry `"pot": True`; games started before pots existed pay with `pot=False`. The daily audit reports pots that hold money a day after their last movement (a game lost without settlement).
- `score_events` holds only reactions (duplicate check and removal, unique per chat). The money history (`/history`, `/uhistory`) is read from `ledger`.
- Game state lives in Redis as JSON with a logical `expires_at` and a Redis TTL with a buffer on top, so cleanup loops see expired games before Redis drops the key.
- State transitions must be atomic:
  - turn-based games (BJ, TTT, duel invites) go through `GameStoreMixin` (`bot/infrastructure/redis/_games.py`): read a `GameSnapshot` with `game_load`, then save a move with `game_save(snapshot, …)` and finish with `game_claim(snapshot)` — both are compare-and-set against the snapshot, so a double tap or a move computed from a stale state neither writes nor pays; accept a lobby with an `NX` lock plus `game_replace_if_state(key, "lobby", …)`;
  - finishing or claiming elsewhere: `DEL`/`GETDEL` — only the caller whose delete succeeded pays out;
  - saving a move elsewhere: `SET … XX` (`KEEPTTL` where the TTL must not move) — never recreate a finished game;
  - multi-field compare-and-set: Lua or WATCH (`RedisStore.game_patch_if_state`, `lot_game_save_if_unchanged`).
- A game started from `/duel` keeps the invite's ledger ref (`data["ref"] = "duel:<invite_id>"`): stakes, prizes and refunds of one game share one ref.
- Cleanup loops take expired games one at a time (Lua "delete if expired"), settle each in its own short transaction and edit the Telegram message after the commit.
- Timeouts: a lobby nobody accepted → refund the creator; an abandoned started game → both stakes go to the bot.
- The bot's own balance in each chat (`bot.id`) is the house: purchases and fees go there, prizes are paid from it. `scores.user_id` references `users`, so `main()` upserts the bot into `users` at startup (`register_bot_user`).

### Moderation

- Every mute goes through `apply_mute()` and every unmute through `lift_mute()` (`bot/presentation/handlers/_admin_utils.py`). They keep the admin-rights snapshot in `active_mutes` (merged in SQL, so a repeated mute never loses it), never shorten a longer mute, and leave a restriction set by hand alone (`MuteOutcome.RESTRICTED_BY_ADMIN`).
- A title bought with `/buyop` (or given by `/op`) is not a moderator: check rights with `can_moderate()`: a bot admin, the chat owner, or an admin who can delete messages or restrict members.
- Only holders of mute rights mute and unmute other people: `/mute` (alias `/amute`) and `/unmute` (alias `/aunmute`) check `can_mute()` — a bot admin, the chat owner, or an admin who can restrict members. They are free. Anything else that mutes hits only the player themself or follows a bot rule: `/selfmute`, `/poker` (the mute debuff never lands on another member), «67», the `/rword` penalty, and `/mutegiveaway`, which only a bot admin starts and players join by choice.

### Configuration

- `configs/config.yaml` — features and their parameters (pydantic models in `config_loader.py`). Unknown keys are an error (`extra="forbid"`): a typo fails startup and `tests/unit/test_config.py`. Admins are `admin.users`: numeric Telegram ids (preferred: a username can be changed and taken by someone else) or usernames, matched by `is_admin()`; at startup the bot logs the id for every admin still listed by username. `admin.prefix` only filters admin messages out of chat summaries.
- `configs/messages.yaml` — user-facing texts. New texts go here (plenty of older code still has hardcoded strings). `tests/unit/test_smoke.py` checks that every `_t["key"]` used in code exists.
- `configs/help.yaml` — `/help` menu structure and texts.
- `configs/words_ru.txt` — word list for `/rword` and `/anagram`. It is gitignored and has to be put on the server by hand; without it the bot logs a warning at startup.
- `.env` — secrets and settings, see `.env.example`: `BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`, `AITUNNEL_API_KEY`, `OPENAI_API_KEY`, `GIGACHAT_API_KEY`, `OPENSERP_URL`, `LOG_CHAT_ID`, `LOG_LEVEL`; optionally `TELEGRAM_PROXY`.

### Database

PostgreSQL 16 with Flyway migrations in `migrations/` (naming: `V001__description.sql`; never edit an applied migration). Driver: asyncpg with raw SQL (no ORM).

### Background Tasks

19 loops in `bot/infrastructure/*_loop.py` plus the heartbeat, started in `bot/main.py` through `BackgroundTasks` (`bot/infrastructure/background.py`): a loop that crashes or returns is logged and restarted after 30 s. They cover event and message cleanup (`system.messages_retention_days`), unmute, giveaways, dice, mute roulette, BJ/TTT/duel cleanup, lot, anagram, word game, auto-delete, daily summary and leaderboard, chat modes, tax and the ledger audit. The heartbeat (`heartbeat.py`) checks Postgres and Redis every 30 s and touches a file; the Docker `HEALTHCHECK` (`python -m bot.infrastructure.heartbeat`) reads its age.

### Tests

- `tests/unit` — pure logic, no services.
- `tests/integration` — real PostgreSQL and Redis from `TEST_DATABASE_URL` / `TEST_REDIS_URL`; without them the tests are skipped. Every test truncates all tables and flushes the Redis db, so point both at a dedicated database and Redis db number. Migrations are applied automatically; `tests/integration/conftest.py` shows how to start throwaway containers.
- E2E tests use the `tg` fixture: a real `Dispatcher` assembled by the same `bootstrap.py` functions as `main.py`, the full DI container, and `FakeTelegramSession` (`tests/integration/fake_telegram.py`) in place of the Bot API. It answers like Telegram and records every request.
- Make races deterministic with a barrier or event inside a monkeypatched method (see `test_e2e.py`), not with sleeps, and check that the test fails when the protection it covers is removed.

## Conventions

- Python 3.12+, async throughout, uv, ruff (line length 120). Formatting-only commits go to `.git-blame-ignore-revs`.
- Messages are sent with HTML parse mode: escape anything user-provided (names, chat titles, free text, RSS fields) with `html.escape` or `user_link()`. Forward a user's own text with `message.html_text`. Text you don't control but want to keep formatted (LLM answers) goes through `sanitize_html` / `send_html_parts` (`bot/presentation/html.py`): only Telegram tags survive, tags are balanced, and long text is split without cutting a tag.
- Bets in game commands are validated with `check_bet()` (`bot/presentation/utils.py`).
- Don't silently swallow exceptions around money or game state — log them.
- Commit messages in Russian with `fix:` / `feat:` / `chore:` / `style:` prefixes.
