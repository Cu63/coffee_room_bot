# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot (aiogram 3) for group-chat gamification: an internal currency ("баллы") earned through emoji reactions, spent on paid actions (mute, tag, buyop, …), wager games (blackjack, tic-tac-toe, duels, dice, slots, word games, lot auctions) and LLM commands. The bot works in several chats; balances are per chat. Everything user-facing is in Russian (texts, comments, config).

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

CI (`.github/workflows/ci.yml`) runs three jobs: `lint` (ruff check + ruff format --check), `test` (pytest `tests/unit`) and `integration` (pytest `tests/integration` against Postgres 16 and Redis 7 service containers). Keep all three green: `scripts/deploy.sh` on the server deploys `master` only after they pass.

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

One Postgres transaction per update (or per loop scope): `DatabaseProvider.get_tx_manager` begins it on the first repository request and finishes it when the scope closes. dishka hands the handler's exception to the generator as the value of `yield` (`asend(exc)`), not by raising it inside.

- Any exception except `TelegramAPIError` → rollback. Success or a Telegram API error → commit (an external effect such as a sent message or a mute may already have happened).
- To roll back on purpose, answer the user and raise `AbortUpdate` (`bot/presentation/errors.py`).
- Handlers are never re-run on network errors: only idempotent Bot API requests are retried (`RetryIdempotentRequestsMiddleware`).
- Redis writes are not part of the transaction. Order the steps so that a rollback cannot leave Redis ahead of Postgres.
- The transaction stays open while the handler awaits Telegram or an LLM, and the pool has 5 connections — keep slow I/O out of money paths.

### Money and game state

- Debit only through atomic primitives: `ScoreService.spend_score` / `transfer_score`, `IScoreRepository.try_debit` (`UPDATE … WHERE value >= amount`) or `debit_up_to`. Never "read the balance, then `add_delta(-x)`".
- Game state lives in Redis as JSON with a logical `expires_at` and a Redis TTL with a buffer on top, so cleanup loops see expired games before Redis drops the key.
- State transitions must be atomic:
  - finishing or claiming a game: `DEL`/`GETDEL` — only the caller whose delete succeeded pays out;
  - saving a move: `SET … XX` (`KEEPTTL` where the TTL must not move) — never recreate a finished game;
  - accepting a lobby: an `NX` lock;
  - multi-field compare-and-set: Lua or WATCH (`RedisStore.game_patch_if_state`, `lot_game_save_if_unchanged`).
- Cleanup loops take expired games one at a time (Lua "delete if expired"), settle each in its own short transaction and edit the Telegram message after the commit.
- Timeouts: a lobby nobody accepted → refund the creator; an abandoned started game → both stakes go to the bot.
- The bot's own balance in each chat (`bot.id`) is the house: purchases and fees go there, prizes are paid from it. `scores.user_id` references `users`, so `main()` upserts the bot into `users` at startup (`register_bot_user`).

### Configuration

- `configs/config.yaml` — features and their parameters (pydantic models in `config_loader.py`). Unknown keys are an error (`extra="forbid"`): a typo fails startup and `tests/unit/test_config.py`. Admins are `admin.users` (Telegram usernames); `admin.prefix` only filters admin messages out of chat summaries.
- `configs/messages.yaml` — user-facing texts. New texts go here (plenty of older code still has hardcoded strings). `tests/unit/test_smoke.py` checks that every `_t["key"]` used in code exists.
- `configs/help.yaml` — `/help` menu structure and texts.
- `configs/words_ru.txt` — word list for `/rword` and `/anagram`. It is gitignored and has to be put on the server by hand.
- `.env` — secrets and settings, see `.env.example`: `BOT_TOKEN`, `DATABASE_URL`, `REDIS_URL`, `AITUNNEL_API_KEY`, `OPENAI_API_KEY`, `GIGACHAT_API_KEY`, `OPENSERP_URL`, `LOG_CHAT_ID`, `LOG_LEVEL`; optionally `TELEGRAM_PROXY`.

### Database

PostgreSQL 16 with Flyway migrations in `migrations/` (naming: `V001__description.sql`; never edit an applied migration). Driver: asyncpg with raw SQL (no ORM).

### Background Tasks

About 17 loops in `bot/infrastructure/*_loop.py`, started in `bot/main.py`: event cleanup, unmute, giveaways, dice, mute roulette, BJ/TTT/duel cleanup, lot, anagram, word game, auto-delete, daily summary and leaderboard, chat modes, tax.

### Tests

- `tests/unit` — pure logic, no services.
- `tests/integration` — real PostgreSQL and Redis from `TEST_DATABASE_URL` / `TEST_REDIS_URL`; without them the tests are skipped. Every test truncates all tables and flushes the Redis db, so point both at a dedicated database and Redis db number. Migrations are applied automatically; `tests/integration/conftest.py` shows how to start throwaway containers.
- E2E tests use the `tg` fixture: a real `Dispatcher` assembled by the same `bootstrap.py` functions as `main.py`, the full DI container, and `FakeTelegramSession` (`tests/integration/fake_telegram.py`) in place of the Bot API. It answers like Telegram and records every request.
- Make races deterministic with a barrier or event inside a monkeypatched method (see `test_e2e.py`), not with sleeps, and check that the test fails when the protection it covers is removed.

## Conventions

- Python 3.12+, async throughout, uv, ruff (line length 120). Formatting-only commits go to `.git-blame-ignore-revs`.
- Messages are sent with HTML parse mode: escape anything user-provided (names, chat titles, free text) with `html.escape` or `user_link()`.
- Don't silently swallow exceptions around money or game state — log them.
- Commit messages in Russian with `fix:` / `feat:` / `chore:` / `style:` prefixes.
