"""RedisStore — фасад, объединяющий все доменные store-миксины."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from bot.infrastructure.redis._anagram import AnagramStoreMixin
from bot.infrastructure.redis._bonus import BonusStoreMixin
from bot.infrastructure.redis._giveaway import GiveawayStoreMixin
from bot.infrastructure.redis._lot import LotStoreMixin
from bot.infrastructure.redis._moderation import ModerationStoreMixin
from bot.infrastructure.redis._mute_roulette import MuteRouletteStoreMixin
from bot.infrastructure.redis._scan import ScanStoreMixin
from bot.infrastructure.redis._slots import SlotsStoreMixin
from bot.infrastructure.redis._tracker import TrackerStoreMixin
from bot.infrastructure.redis._wordgame import WordgameStoreMixin

logger = logging.getLogger(__name__)


class RedisStore(
    SlotsStoreMixin,
    ModerationStoreMixin,
    BonusStoreMixin,
    MuteRouletteStoreMixin,
    GiveawayStoreMixin,
    WordgameStoreMixin,
    AnagramStoreMixin,
    LotStoreMixin,
    TrackerStoreMixin,
    ScanStoreMixin,
):
    """Обёртка над Redis для хранения игрового состояния.

    На переходный период объединяет все доменные store-миксины.
    Потребители могут постепенно переходить на прямой импорт миксинов.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis
