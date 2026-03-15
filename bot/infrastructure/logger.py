"""Настройка structlog для всего приложения."""

from __future__ import annotations

import logging

import structlog

from bot.infrastructure.config_loader import LoggingConfig


def setup_logger(config: LoggingConfig) -> None:
    """Инициализирует structlog + stdlib logging.

    В режиме human_readable_logs (dev) — цветной консольный вывод.
    В продакшне — JSON, один объект на строку.
    """
    common_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    handler = logging.StreamHandler()
    handler.setLevel(config.level)

    if config.human_readable_logs:
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
            foreign_pre_chain=common_processors,
        )
    else:
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=common_processors,
        )

    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Убираем хендлеры добавленные basicConfig ранее
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(config.level)

    structlog.configure(
        processors=[
            *common_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )