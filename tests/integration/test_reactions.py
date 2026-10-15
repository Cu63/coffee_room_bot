"""Правила начисления баллов за реакции (configs/config.yaml: reactions, limits)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from bot.application.interfaces.message_repository import MessageInfo
from bot.domain.entities import IgnoreReason
from bot.domain.tz import TZ_MSK
from bot.infrastructure.db.postgres_message_repository import PostgresMessageRepository
from tests.integration.helpers import CHAT, balance, seed_users

AUTHOR, FAN, HATER = 401, 402, 403


@pytest.fixture
async def svc(conn, score_service_factory):
    await seed_users(conn, AUTHOR, FAN, HATER)
    return score_service_factory(conn)


async def post(conn, message_id: int, *, user_id: int = AUTHOR, age: timedelta = timedelta(0), chat_id: int = CHAT):
    sent_at = datetime.now(TZ_MSK) - age
    await PostgresMessageRepository(conn).save(
        MessageInfo(message_id=message_id, chat_id=chat_id, user_id=user_id, sent_at=sent_at)
    )


async def test_positive_reaction_credits_author(conn, svc):
    await post(conn, 1)
    result = await svc.apply_reaction(FAN, CHAT, 1, "🔥")
    assert result.applied and result.delta == 3 and result.new_value == 3
    assert await balance(conn, AUTHOR) == 3


async def test_negative_reaction_debits_author(conn, svc):
    await post(conn, 1)
    result = await svc.apply_reaction(HATER, CHAT, 1, "🤡")
    assert result.applied and result.delta == -3
    assert await balance(conn, AUTHOR) == -3


@pytest.mark.parametrize(
    ("actor", "message_id", "emoji", "reason"),
    [
        (FAN, 1, "🦄", IgnoreReason.UNKNOWN_REACTION),
        (FAN, 999, "🔥", IgnoreReason.UNKNOWN_MESSAGE),
        (AUTHOR, 1, "🔥", IgnoreReason.SELF_REACTION),
    ],
)
async def test_ignored_reactions(conn, svc, actor, message_id, emoji, reason):
    await post(conn, 1)
    result = await svc.apply_reaction(actor, CHAT, message_id, emoji)
    assert not result.applied and result.reason == reason
    assert await balance(conn, AUTHOR) == 0


async def test_duplicate_reaction_is_ignored(conn, svc):
    await post(conn, 1)
    assert (await svc.apply_reaction(FAN, CHAT, 1, "🔥")).applied
    assert (await svc.apply_reaction(FAN, CHAT, 1, "🔥")).reason == IgnoreReason.DUPLICATE
    assert await balance(conn, AUTHOR) == 3


async def test_reaction_to_old_message_is_ignored(conn, svc, config):
    await post(conn, 1, age=timedelta(hours=config.limits.max_message_age_hours, minutes=1))
    assert (await svc.apply_reaction(FAN, CHAT, 1, "🔥")).reason == IgnoreReason.MESSAGE_TOO_OLD


async def test_negative_reaction_from_actor_in_debt_is_ignored(conn, svc):
    await seed_users(conn, HATER, balance=-1)
    await post(conn, 1)
    assert (await svc.apply_reaction(HATER, CHAT, 1, "👎")).reason == IgnoreReason.NEGATIVE_SCORE_ACTOR


async def test_positive_per_target_daily_limit(conn, svc, config):
    limit = config.limits.daily_positive_per_target
    for message_id in range(1, limit + 2):
        await post(conn, message_id)
    for message_id in range(1, limit + 1):
        assert (await svc.apply_reaction(FAN, CHAT, message_id, "👍")).applied
    result = await svc.apply_reaction(FAN, CHAT, limit + 1, "👍")
    assert result.reason == IgnoreReason.PER_TARGET_POSITIVE_LIMIT
    assert await balance(conn, AUTHOR) == limit


async def test_negative_given_daily_limit(conn, svc, config):
    limit = config.limits.daily_negative_given
    for message_id in range(1, limit + 2):
        await post(conn, message_id)
    for message_id in range(1, limit + 1):
        assert (await svc.apply_reaction(HATER, CHAT, message_id, "👎")).applied
    assert (await svc.apply_reaction(HATER, CHAT, limit + 1, "👎")).reason == IgnoreReason.DAILY_REACTIONS_LIMIT


async def test_daily_received_cap(conn, svc, config):
    cap = config.limits.daily_score_received
    reactions_to_cap = -(-cap // 3)  # 🔥 = +3
    for message_id in range(1, reactions_to_cap + 2):
        await post(conn, message_id)
    for message_id in range(1, reactions_to_cap + 1):
        assert (await svc.apply_reaction(FAN, CHAT, message_id, "🔥")).applied
    assert (await svc.apply_reaction(FAN, CHAT, reactions_to_cap + 1, "🔥")).reason == IgnoreReason.DAILY_SCORE_LIMIT


async def test_remove_reaction_reverses_exactly_once(conn, svc):
    await post(conn, 1)
    await svc.apply_reaction(FAN, CHAT, 1, "🔥")
    removed = await svc.remove_reaction(FAN, CHAT, 1, "🔥")
    assert removed.applied and removed.delta == -3
    assert await balance(conn, AUTHOR) == 0
    assert (await svc.remove_reaction(FAN, CHAT, 1, "🔥")).reason == IgnoreReason.EVENT_NOT_FOUND
    assert await balance(conn, AUTHOR) == 0
    # Лимит откатился — реакцию можно поставить снова
    assert (await svc.apply_reaction(FAN, CHAT, 1, "🔥")).applied


async def test_same_message_id_in_two_chats_counts_separately(conn, svc):
    other_chat = CHAT - 1
    await post(conn, 1)
    await post(conn, 1, chat_id=other_chat)
    assert (await svc.apply_reaction(FAN, CHAT, 1, "🔥")).applied
    assert (await svc.apply_reaction(FAN, other_chat, 1, "🔥")).applied
    # Снятие реакции в одном чате не трогает реакцию в другом
    assert (await svc.remove_reaction(FAN, other_chat, 1, "🔥")).applied
    assert (await balance(conn, AUTHOR), await balance(conn, AUTHOR, chat_id=other_chat)) == (3, 0)
