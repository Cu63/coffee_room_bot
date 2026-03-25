"""/anon — анонимные сообщения в группы.

Сценарий (только в личке с ботом):
  1. Пользователь отправляет /anon
  2. Бот показывает список доступных чатов (инлайн-кнопки)
  3. Пользователь выбирает чат
  4. Бот просит написать текст сообщения
  5. Пользователь пишет текст
  6. Бот отправляет анонимное сообщение в выбранный чат
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dishka import AsyncContainer
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import ScoreService
from bot.infrastructure.redis_store import RedisStore

ANON_MESSAGE_PAYMENT = 100


class HasAnonState(BaseFilter):
    """True когда у пользователя есть активное anon-состояние в Redis."""

    async def __call__(self, message: Message, dishka_container: AsyncContainer) -> bool:
        if message.from_user is None:
            return False
        store: RedisStore = await dishka_container.get(RedisStore)
        state = await store.anon_get_state(message.from_user.id)
        return state is not None


class NoAnonState(BaseFilter):
    """True когда у пользователя НЕТ активного anon-состояния — пропускает wordgame."""

    async def __call__(self, message: Message, dishka_container: AsyncContainer) -> bool:
        if message.from_user is None:
            return True
        store: RedisStore = await dishka_container.get(RedisStore)
        state = await store.anon_get_state(message.from_user.id)
        return state is None

logger = logging.getLogger(__name__)
router = Router(name="anon")

_ANON_LABEL = "😶 Аноним"
_CB_PREFIX = "anon_chat:"
_CB_CANCEL = "anon_cancel"

# ── helpers ──────────────────────────────────────────────────────────────────


def _chat_keyboard(chats: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура с кнопками выбора чата."""
    buttons = [
        [InlineKeyboardButton(text=title, callback_data=f"{_CB_PREFIX}{chat_id}")]
        for chat_id, title in chats
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=_CB_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _ask_for_text(target: Message | CallbackQuery, chat_title: str) -> None:
    """Просит пользователя написать текст анонимного сообщения."""
    text = (
        f"✏️ Отправь мне текст, который я перешлю анонимно в <b>{chat_title}</b>.\n\n"
        "<i>Напиши /cancel, чтобы отменить.</i>"
    )
    if isinstance(target, CallbackQuery) and target.message:
        await target.message.edit_text(text, parse_mode=ParseMode.HTML)
    elif isinstance(target, Message):
        await target.answer(text, parse_mode=ParseMode.HTML)


# ── /anon — точка входа ──────────────────────────────────────────────────────


@router.message(Command("anon"), F.chat.type == "private")
@inject
async def cmd_anon(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    """Запускает флоу анонимного сообщения."""
    if message.from_user is None:
        return

    user_id = message.from_user.id

    # Сбрасываем предыдущее состояние, если было
    await store.anon_clear_state(user_id)

    chats = await store.anon_get_chats()

    if not chats:
        await message.answer(
            "😶 Пока нет ни одного чата для анонимных сообщений.\n"
            "Бот должен быть активным участником хотя бы одной группы."
        )
        return

    if len(chats) == 1:
        # Один чат — сразу переходим к вводу текста
        chat_id, chat_title = chats[0]
        await store.anon_set_state(user_id, {"step": "write_message", "chat_id": chat_id, "chat_title": chat_title})
        await _ask_for_text(message, chat_title)
    else:
        # Несколько чатов — показываем выбор
        await store.anon_set_state(user_id, {"step": "select_chat"})
        chats_sorted = sorted(chats, key=lambda c: c[1])
        await message.answer(
            "👇 Выбери чат, в который хочешь отправить анонимное сообщение:",
            reply_markup=_chat_keyboard(chats_sorted),
        )


# ── Выбор чата (callback) ────────────────────────────────────────────────────


@router.callback_query(F.data.startswith(_CB_PREFIX))
@inject
async def cb_select_chat(
    callback: CallbackQuery,
    store: FromDishka[RedisStore],
) -> None:
    """Пользователь выбрал чат из инлайн-меню."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    user_id = callback.from_user.id
    state = await store.anon_get_state(user_id)

    if state is None or state.get("step") != "select_chat":
        await callback.answer("Сессия устарела. Начни заново: /anon", show_alert=True)
        try:
            await callback.message.delete()
        except Exception:
            pass
        return

    chat_id_str = (callback.data or "").removeprefix(_CB_PREFIX)
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        await callback.answer("Ошибка выбора.", show_alert=True)
        return

    # Находим название выбранного чата
    chats = await store.anon_get_chats()
    chat_title = next((t for cid, t in chats if cid == chat_id), str(chat_id))

    await store.anon_set_state(user_id, {"step": "write_message", "chat_id": chat_id, "chat_title": chat_title})
    await callback.answer()
    await _ask_for_text(callback, chat_title)


# ── Отмена ────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == _CB_CANCEL)
@inject
async def cb_cancel(
    callback: CallbackQuery,
    store: FromDishka[RedisStore],
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    await store.anon_clear_state(callback.from_user.id)
    await callback.answer("Отменено.")
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass


@router.message(Command("cancel"), F.chat.type == "private")
@inject
async def cmd_cancel(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    if message.from_user is None:
        return
    state = await store.anon_get_state(message.from_user.id)
    if state:
        await store.anon_clear_state(message.from_user.id)
        await message.answer("❌ Анонимное сообщение отменено.")
    else:
        await message.answer("Нечего отменять.")


# ── Приём текста и отправка ──────────────────────────────────────────────────


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), HasAnonState())
@inject
async def on_private_text(
    message: Message,
    bot: Bot,
    store: FromDishka[RedisStore],
    score_service: FromDishka[ScoreService],
) -> None:
    """Ловит текст в личке когда пользователь в шаге write_message."""
    if message.from_user is None or not message.text:
        return

    user_id = message.from_user.id
    state = await store.anon_get_state(user_id)

    if state is None or state.get("step") != "write_message":
        # Не в режиме ввода — игнорируем
        return
    

    chat_id: int = state["chat_id"]
    chat_title: str = state.get("chat_title", str(chat_id))

    if (await score_service.get_score(user_id, chat_id)).value < ANON_MESSAGE_PAYMENT:
        await message.answer(f"❌ Не удалось отправить сообщение.", parse_mode=ParseMode.HTML)
        return


    await store.anon_clear_state(user_id)

    # Отправляем анонимное сообщение в групповой чат
    message_sent = False
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=f"😶 <b>{_ANON_LABEL}</b>\n\n{message.text}",
            parse_mode=ParseMode.HTML,
        )
        message_sent = True
        await message.answer(f"✅ Сообщение анонимно отправлено в <b>{chat_title}</b>.", parse_mode=ParseMode.HTML)
        logger.info("anon: user %d sent anonymous message to chat %d", user_id, chat_id)
    except Exception:
        logger.exception("anon: failed to send message to chat %d", chat_id)
        await message.answer(
            "❌ Не удалось отправить сообщение. Возможно, бот был удалён из чата.\n"
            "Попробуй снова: /anon"
        )
    finally:
        if message_sent:
            await score_service.add_score_quiet(user_id, chat_id, -ANON_MESSAGE_PAYMENT)