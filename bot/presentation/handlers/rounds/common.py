"""Раунд с вариантами ответа — общий для /quiz и /whosaid.

Вопрос публикуется с четырьмя кнопками. У каждого участника одна попытка; первый правильный
ответ забирает раунд (GETDEL) и получает приз из кассы бота. Не ответили за отведённое время —
цикл истечения показывает верный ответ. В чате одновременно идёт один раунд.
"""

from __future__ import annotations

import html
import logging
import secrets
import time

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.application.iq_service import IqService
from bot.application.score_service import ScoreService
from bot.domain.entities import RoundQuestion
from bot.domain.tz import now_msk
from bot.infrastructure.config_loader import AppConfig, RoundConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.handlers._iq_utils import award_game_iq
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, safe_callback_answer

logger = logging.getLogger(__name__)
router = Router(name="rounds")

_PREFIX = "round:"
_TTL_BUFFER = 120  # Redis хранит раунд дольше его срока: цикл истечения увидит его первым
_PREPARE_SECONDS = 120  # на подготовку вопроса (ответ LLM)


def _keyboard(round_id: str, options: tuple[str, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=option, callback_data=f"{_PREFIX}{round_id}:{i}")]
            for i, option in enumerate(options)
        ]
    )


async def reserve_round(
    message: Message, store: RedisStore, formatter: MessageFormatter, kind: str, cfg: RoundConfig
) -> str | None:
    """Проверить паузу и дневной лимит и занять чат под раунд. None — нельзя, ответ уже отправлен."""
    chat_id = message.chat.id
    left = await store.round_cooldown_left(kind, chat_id)
    if left:
        await reply_and_delete(message, formatter._t["round_cooldown"].format(minutes=max(1, -(-left // 60))))
        return None
    today = now_msk().strftime("%Y%m%d")
    if cfg.daily_limit > 0 and await store.round_daily_count(kind, chat_id, today) >= cfg.daily_limit:
        await reply_and_delete(message, formatter._t["round_daily_limit"].format(limit=cfg.daily_limit))
        return None
    round_id = secrets.token_hex(4)
    if not await store.round_claim_chat(chat_id, round_id, cfg.answer_seconds + _PREPARE_SECONDS):
        await reply_and_delete(message, formatter._t["round_busy"])
        return None
    return round_id


async def post_round(
    message: Message,
    store: RedisStore,
    formatter: MessageFormatter,
    kind: str,
    cfg: RoundConfig,
    round_id: str,
    question: RoundQuestion,
    placeholder: Message | None = None,
) -> None:
    """Опубликовать вопрос с кнопками (в placeholder, если он есть) и сохранить раунд."""
    title = formatter._t[f"{kind}_title"]
    text = formatter._t["round_question"].format(
        title=title, question=question.text, seconds=cfg.answer_seconds, prize=cfg.prize
    )
    keyboard = _keyboard(round_id, question.options)
    if placeholder is not None:
        posted = await placeholder.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=keyboard, link_preview_options=NO_PREVIEW
        )
        # edit_text отдаёт True вместо сообщения, если правили не своё (inline) — тогда id прежний
        message_id = posted.message_id if isinstance(posted, Message) else placeholder.message_id
    else:
        sent = await message.answer(
            text, parse_mode=ParseMode.HTML, reply_markup=keyboard, link_preview_options=NO_PREVIEW
        )
        message_id = sent.message_id
    data = {
        "id": round_id,
        "kind": kind,
        "chat_id": message.chat.id,
        "message_id": message_id,
        "title": title,
        "question": question.text,
        "options": list(question.options),
        "correct": question.correct,
        "explanation": question.explanation,
        "author_id": question.author_id,
        "prize": cfg.prize,
        "expires_at": time.time() + cfg.answer_seconds,
    }
    await store.round_save(data, cfg.answer_seconds + _TTL_BUFFER)
    await store.round_started(kind, message.chat.id, now_msk().strftime("%Y%m%d"), cfg.cooldown_minutes * 60)


def _explanation(formatter: MessageFormatter, data: dict) -> str:
    return formatter._t["round_explanation"].format(text=data["explanation"]) if data.get("explanation") else ""


async def _edit(bot: Bot, data: dict, text: str) -> None:
    try:
        await bot.edit_message_text(
            text, chat_id=data["chat_id"], message_id=data["message_id"], parse_mode=ParseMode.HTML
        )
    except TelegramAPIError:
        logger.warning("раунд %s: не удалось обновить сообщение", data["id"], exc_info=True)


async def finish_expired(bot: Bot, formatter: MessageFormatter, data: dict) -> None:
    """Время вышло: показать верный ответ."""
    answer = html.escape(data["options"][data["correct"]])
    text = formatter._t["round_expired"].format(title=data["title"], question=data["question"], answer=answer)
    await _edit(bot, data, text + _explanation(formatter, data))


@router.callback_query(F.data.startswith(_PREFIX))
@inject
async def cb_round_answer(
    cb: CallbackQuery,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
    iq_service: FromDishka[IqService],
    tx: FromDishka[ITransactionManager],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Ответ кнопкой: одна попытка на участника, приз — первому правильному."""
    bot = cb.bot
    if bot is None:
        await safe_callback_answer(cb)
        return
    try:
        _, round_id, raw_index = (cb.data or "").split(":")
        index = int(raw_index)
    except ValueError:
        await safe_callback_answer(cb)
        return
    data = await store.round_get(round_id)
    if data is None or data["expires_at"] <= time.time():
        await safe_callback_answer(cb, formatter._t["round_over"], show_alert=True)
        return
    user = cb.from_user
    if user.id == data.get("author_id"):
        await safe_callback_answer(cb, formatter._t["whosaid_own_quote"], show_alert=True)
        return
    if not await store.round_mark_answered(round_id, user.id, int(data["expires_at"] - time.time()) + _TTL_BUFFER):
        await safe_callback_answer(cb, formatter._t["round_already_answered"], show_alert=True)
        return
    if index != data["correct"]:
        await safe_callback_answer(cb, formatter._t["round_wrong"], show_alert=True)
        return
    # Забрать раунд: при одновременных верных ответах приз получит только один
    won = await store.round_claim(round_id)
    if won is None:
        await safe_callback_answer(cb, formatter._t["round_over"], show_alert=True)
        return
    await store.round_release_chat(won["chat_id"], round_id)

    chat_id = won["chat_id"]
    paid = await score_service.pay_from_house(bot.id, user.id, chat_id, won["prize"], ref=f"{won['kind']}:{round_id}")
    reward = (
        formatter._t["round_prize"].format(prize=won["prize"], score_word=formatter._p.pluralize(won["prize"]))
        if paid
        else formatter._t["round_no_prize"]
    )
    # IQ за победу не критичен: его ошибка не должна откатить приз
    try:
        async with tx.savepoint():
            iq = await award_game_iq(store, iq_service, config.iq, user.id, chat_id)
        if iq is not None:
            reward += formatter._t["round_iq"].format(iq=iq)
    except Exception:
        logger.exception("раунд %s: IQ за победу не начислен", round_id)

    text = formatter._t["round_won"].format(
        title=won["title"],
        question=won["question"],
        winner=user_link(user.username, user.full_name, user.id),
        answer=html.escape(won["options"][won["correct"]]),
        reward=reward,
    )
    await _edit(bot, won, text + _explanation(formatter, won))
    await safe_callback_answer(cb, formatter._t["round_right"])
