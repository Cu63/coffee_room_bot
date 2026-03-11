# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot (aiogram 3) implementing a gamification system via emoji reactions in group chats. Users gain/lose points when others react to their messages. Written in Russian (UI texts, comments, config).

## Key Commands

```bash
# Install dependencies
uv sync

# Run the bot locally (requires PostgreSQL + .env with BOT_TOKEN and DATABASE_URL)
uv run python -m bot

# Full stack via Docker
docker compose up --build -d

# Apply DB migrations (Flyway)
docker run --rm --network=host -v $(pwd)/migrations:/flyway/sql flyway/flyway:11 \
  -url=jdbc:postgresql://localhost:5432/scorebot -user=scorebot -password=scorebot migrate
```

No test suite exists yet (tests/ directories are empty).

## Architecture

Layered architecture with strict dependency direction: Domain ← Application ← Infrastructure ← Presentation.

- **Domain** (`bot/domain/`): Pure business logic — entities, emoji utilities, reaction-to-score mapping (`ReactionRegistry`), Russian pluralization. No framework imports.
- **Application** (`bot/application/`): Service layer (use cases) + repository interfaces in `interfaces/`. Services: `ScoreService` (reaction processing with limits), `LeaderboardService`, `HistoryService`, `CleanupService`, `MuteService`.
- **Infrastructure** (`bot/infrastructure/`): PostgreSQL repositories (raw SQL via asyncpg), config loading (YAML + pydantic-settings), DI container setup (`di.py` using dishka), message formatting.
- **Presentation** (`bot/presentation/`): aiogram handlers (`handlers/`) and middlewares (`middlewares/`). Only layer that knows about aiogram.

### DI (dishka)

`di.py` defines two providers:
- `AppProvider` (Scope.APP) — singletons: settings, config, asyncpg pool, reaction registry, message formatter.
- `RequestProvider` (Scope.REQUEST) — per-handler: transaction manager (auto commit/rollback), all repositories, all services. Dependencies injected via `FromDishka[T]` in handlers.

### Transaction Management

Transactions are managed exclusively in `RequestProvider.get_tx_manager()` — begins on entry, commits on success, rolls back on exception. Repositories receive the connection, never manage transactions themselves.

### Configuration

- `config.yaml` — reactions with weights, score pluralization, daily limits, admin settings, mute/tag costs. Adding a new reaction = edit YAML + restart.
- `messages.yaml` — all user-facing text templates (Russian). Separated from code for easy localization.
- `.env` — secrets only: `BOT_TOKEN`, `DATABASE_URL`.

### Database

PostgreSQL 16 with Flyway migrations in `migrations/` (naming: `V001__description.sql`). Driver: asyncpg with raw SQL (no ORM).

### Background Tasks

Two async loops in `main.py`: cleanup of expired events (every 6h) and unmute check (every 60s).

## Conventions

- Language: Python 3.12+, async throughout
- Package manager: uv
- All user-facing strings live in `messages.yaml`, not in code
- Admin commands use a configurable prefix from `config.yaml` (e.g., `/coffee_add`, `/coffee_mute`)
