FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Убирает docstrings из загружаемых модулей (~1-2 MB)
    PYTHONOPTIMIZE=2 \
    # Ограничивает арены glibc malloc: без этого каждый поток
    # держит свою арену и не отдаёт память ОС после пиков
    MALLOC_ARENA_MAX=2 \
    # jemalloc агрессивнее возвращает память ОС, меньше фрагментация
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2

WORKDIR /app

# Install uv + jemalloc
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
        libjemalloc2 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Copy the rest of the application
COPY . .

# Install the project itself
RUN uv sync --frozen --no-dev

# Запускаем Python напрямую из venv — без обёртки uv run,
# которая форкает лишний процесс-посредник
CMD [".venv/bin/python", "-m", "bot"]
