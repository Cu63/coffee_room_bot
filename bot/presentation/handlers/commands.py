from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.score_service import ScoreService
from bot.application.leaderboard_service import LeaderboardService
from bot.application.history_service import HistoryService
from bot.application.interfaces.user_repository import IUserRepository
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.domain.tz import to_msk

router = Router(name="commands")

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


@router.message(Command("score"))
@inject
async def cmd_score(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Показывает счёт вызвавшего или указанного пользователя."""
    chat_id = message.chat.id
    target_user = None

    if command.args:
        username = command.args.strip().lstrip("@")
        target_user = await user_repo.get_by_username(username)
        if target_user is None:
            await message.reply(formatter._t["error_user_not_found"])
            return
        display_name = user_link(target_user.username, target_user.full_name, target_user.id)
    else:
        if message.from_user is None:
            return
        display_name = user_link(
            message.from_user.username,
            message.from_user.full_name or "",
            message.from_user.id,
        )

    user_id = target_user.id if target_user else message.from_user.id  # type: ignore[union-attr]
    score = await score_service.get_score(user_id, chat_id)
    await message.reply(
        formatter.score_info(display_name, score.value),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


@router.message(Command("top"))
@inject
async def cmd_top(
    message: Message,
    command: CommandObject,
    leaderboard_service: FromDishka[LeaderboardService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    """Топ участников чата."""
    limit = 10
    if command.args:
        try:
            limit = max(1, min(50, int(command.args.strip())))
        except ValueError:
            limit = 10

    chat_id = message.chat.id
    top_scores = await leaderboard_service.get_top(chat_id, limit)

    rows: list[tuple[int, str, int]] = []
    for rank, score in enumerate(top_scores, start=1):
        user = await user_repo.get_by_id(score.user_id)
        if user:
            name = user_link(user.username, user.full_name, user.id)
        else:
            name = str(score.user_id)
        rows.append((rank, name, score.value))

    await message.reply(
        formatter.leaderboard(rows),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
    )


# Хранилище страниц истории: (chat_id, user_id) -> list[list[dict]]
_history_pages: dict[tuple[int, int], list[list[dict]]] = {}

HISTORY_PAGE_SIZE = 30  # строк на страницу


def _history_kb(page: int, total: int, chat_id: int, uid: int) -> InlineKeyboardMarkup | None:
    """Кнопки пагинации. Возвращает None если страница одна."""
    if total <= 1:
        return None
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"hist:{chat_id}:{uid}:{page - 1}",
        ))
    buttons.append(InlineKeyboardButton(
        text=f"{page + 1}/{total}",
        callback_data="hist:noop",
    ))
    if page < total - 1:
        buttons.append(InlineKeyboardButton(
            text="Вперёд ➡️",
            callback_data=f"hist:{chat_id}:{uid}:{page + 1}",
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


@router.message(Command("history"))
@inject
async def cmd_history(
    message: Message,
    history_service: FromDishka[HistoryService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """История начислений за последние N дней."""
    if message.from_user is None:
        return
    chat_id = message.chat.id
    uid = message.from_user.id
    events = await history_service.get_history(chat_id)

    event_dicts: list[dict] = []
    for e in events:
        actor = await user_repo.get_by_id(e.actor_id)
        target = await user_repo.get_by_id(e.target_id)
        actor_name = user_link(actor.username, actor.full_name, actor.id) if actor else str(e.actor_id)
        target_name = user_link(target.username, target.full_name, target.id) if target else str(e.target_id)
        event_dicts.append({
            "date": to_msk(e.created_at).strftime("%d.%m %H:%M") if e.created_at else "",
            "actor": actor_name,
            "target": target_name,
            "emoji": e.emoji,
            "delta": e.delta,
        })

    # Разбиваем на страницы
    pages = [event_dicts[i:i + HISTORY_PAGE_SIZE] for i in range(0, max(1, len(event_dicts)), HISTORY_PAGE_SIZE)]
    _history_pages[(chat_id, uid)] = pages

    text = formatter.history(pages[0], config.history.retention_days)
    kb = _history_kb(0, len(pages), chat_id, uid)

    await message.reply(
        text,
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("hist:"))
@inject
async def cb_history(
    callback: CallbackQuery,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    async def safe_answer() -> None:
        try:
            await callback.answer()
        except Exception:
            pass

    if callback.data == "hist:noop":
        await safe_answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await safe_answer()
        return

    _, chat_id_str, uid_str, page_str = parts
    try:
        chat_id = int(chat_id_str)
        uid = int(uid_str)
        page = int(page_str)
    except ValueError:
        await safe_answer()
        return

    # Только тот, кто вызвал /history
    if callback.from_user.id != uid:
        try:
            await callback.answer("Это не твоя история.", show_alert=True)
        except Exception:
            pass
        return

    pages = _history_pages.get((chat_id, uid))
    if not pages or page < 0 or page >= len(pages):
        await safe_answer()
        return

    text = formatter.history(pages[page], config.history.retention_days)
    kb = _history_kb(page, len(pages), chat_id, uid)

    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            link_preview_options=NO_PREVIEW,
        )
    except Exception:
        pass

    await safe_answer()

@router.message(Command("limits"))
@inject
async def cmd_limits(
    message: Message,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Показывает текущие лимиты бота."""
    lc = config.limits
    mc = config.mute
    tc = config.tag
    bjc = config.blackjack
    p = formatter._p
    icon = config.score.icon

    text = (
        f"{icon} <b>Текущие лимиты бота</b>\n"
        f"\n"
        f"<b>Реакции:</b>\n"
        f"  В сутки от одного участника: {lc.daily_reactions_given}\n"
        f"  Макс. баллов получателю в сутки: {lc.daily_score_received}\n"
        f"  Возраст сообщения: не старше {lc.max_message_age_hours} ч.\n"
        f"\n"
        f"<b>История:</b>\n"
        f"  Хранится: {config.history.retention_days} дн.\n"
        f"\n"
        f"<b>Мут:</b>\n"
        f"  Стоимость: {mc.cost_per_minute} {p.pluralize(mc.cost_per_minute)} / мин\n"
        f"  Диапазон: {mc.min_minutes}–{mc.max_minutes} мин\n"
        f"\n"
        f"<b>Тег:</b>\n"
        f"  Себе: {tc.cost_self} {p.pluralize(tc.cost_self)}\n"
        f"  Участнику: {tc.cost_member} {p.pluralize(tc.cost_member)}\n"
        f"  Админу: {tc.cost_admin} {p.pluralize(tc.cost_admin)}\n"
        f"  Создателю: {tc.cost_owner} {p.pluralize(tc.cost_owner)}\n"
        f"  Макс. длина: {tc.max_length} символов\n"
        f"\n"
        f"<b>Блекджек:</b>\n"
        f"  Ставка: {bjc.min_bet}–{bjc.max_bet} {p.pluralize(bjc.max_bet)}"
    )

    await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
