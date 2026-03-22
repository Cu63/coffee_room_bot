# ── Стадия сборки ────────────────────────────────────────────────────────────
# Устанавливаем зависимости и компилируем байткод.
# uv, uvx и build-артефакты остаются здесь и не попадают в финальный образ.
FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Зависимости отдельным слоем — кешируются пока не меняется lock-файл
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Код приложения
COPY . .
RUN uv sync --frozen --no-dev

# Компилируем .py -> .pyc прямо в образе.
# Без этого (и с PYTHONDONTWRITEBYTECODE=1) Python перекомпилировал бы
# все 187 файлов при каждом старте контейнера.
# -q подавляет вывод, -j0 использует все CPU.
RUN .venv/bin/python -m compileall -q -j0 bot/


# ── Финальный образ ───────────────────────────────────────────────────────────
# Только venv + код + jemalloc. uv/uvx (~40 MB) сюда не копируются.
FROM python:3.12-slim AS final

ENV PYTHONUNBUFFERED=1 \
    # Убирает docstrings при загрузке модулей (~1-2 MB)
    PYTHONOPTIMIZE=2 \
    # Ограничивает арены glibc malloc — без этого каждый поток
    # держит свою арену и не возвращает память ОС после пиков
    MALLOC_ARENA_MAX=2 \
    # jemalloc агрессивнее возвращает память ОС, меньше фрагментации.
    # Путь для linux/amd64; на arm64: /usr/lib/aarch64-linux-gnu/libjemalloc.so.2
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2

RUN apt-get update && apt-get install -y --no-install-recommends \
        libjemalloc2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app ./

# Запускаем Python напрямую из venv — без uv run (лишний процесс-посредник)
CMD [".venv/bin/python", "-m", "bot"]
