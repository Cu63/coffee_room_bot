"""Основа store-миксинов: клиент Redis, который им задаёт фасад RedisStore."""

from __future__ import annotations

from typing import Any


class RedisMixin:
    # redis.asyncio.Redis. Аннотации redis-py общие для синхронного и асинхронного клиентов:
    # команды объявлены как «Awaitable[T] | T», и pyright не даёт их await-ить. Поэтому Any.
    _r: Any
