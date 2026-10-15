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

# Компилируем .py -> .pyc прямо в образе — и код бота, и зависимости.
# -o 2: финальный образ работает с PYTHONOPTIMIZE=2 и ищет *.opt-2.pyc; обычные .pyc
# он не использует, а записать свои не сможет (процесс не root) и компилировал бы
# всё заново при каждом старте.
# -q подавляет вывод, -j0 использует все CPU. В зависимостях бывают файлы, которые
# не компилируются (шаблоны, код под другие версии Python), — их ошибки не валят сборку.
RUN .venv/bin/python -m compileall -q -j0 -o 2 bot/ \
    && (.venv/bin/python -m compileall -q -j0 -o 2 .venv/lib/ > /dev/null || true)


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

# Бот ничего не пишет на диск, кроме пульса в /tmp: root ему не нужен
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin bot
USER bot

# Пульс обновляется раз в 30 с, пока живы цикл событий, Postgres и Redis
# (bot/infrastructure/heartbeat.py); deploy.sh ждёт healthy после пересборки
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD [".venv/bin/python", "-m", "bot.infrastructure.heartbeat"]

# Запускаем Python напрямую из venv — без uv run (лишний процесс-посредник)
CMD [".venv/bin/python", "-m", "bot"]
