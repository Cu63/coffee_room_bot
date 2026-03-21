from __future__ import annotations

import datetime

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from cachetools import TTLCache
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.history_service import HistoryService
from bot.application.interfaces.daily_limits_repository import IDailyLimitsRepository
from bot.application.interfaces.score_repository import IScoreRepository
from bot.application.interfaces.user_repository import IUserRepository
from bot.application.interfaces.user_stats_repository import IUserStatsRepository
from bot.application.leaderboard_service import LeaderboardService
from bot.application.score_service import ScoreService
from bot.application.xp_service import XpService
from bot.domain.tz import to_msk
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter, user_link
from bot.presentation.utils import NO_PREVIEW, reply_and_delete, safe_callback_answer, schedule_delete

router = Router(name="commands")

# TTLCache: страницы истории живут 2 минуты, max 100 записей (chat_id, user_id)
_history_pages: TTLCache = TTLCache(maxsize=100, ttl=120)


def _history_kb(page: int, total: int, chat_id: int, uid: int) -> InlineKeyboardMarkup | None:
    """Кнопки пагинации. Возвращает None если страница одна."""
    if total <= 1:
        return None
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"hist:{chat_id}:{uid}:{page - 1}"))
    buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="hist:noop"))
    if page < total - 1:
        buttons.append(InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"hist:{chat_id}:{uid}:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


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
        target_user = await user_repo.get_by_username(command.args.strip().lstrip("@"))
        if target_user is None:
            await reply_and_delete(message, formatter._t["error_user_not_found"])
            return
        display_name = user_link(target_user.username, target_user.full_name, target_user.id)
    else:
        if message.from_user is None:
            return
        display_name = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)
    user_id = target_user.id if target_user else message.from_user.id  # type: ignore[union-attr]
    score = await score_service.get_score(user_id, chat_id)
    await reply_and_delete(message,
        formatter.score_info(display_name, score.value), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
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
    """Топ участников чата. Отрицательное число — антирейтинг (последние места)."""
    n = 10
    if command.args:
        try:
            n = int(command.args.strip())
        except ValueError:
            n = 10

    is_bottom = n < 0
    limit = max(1, min(50, abs(n)))

    if is_bottom:
        scores = await leaderboard_service.get_bottom(message.chat.id, limit)
    else:
        scores = await leaderboard_service.get_top(message.chat.id, limit)

    users = await user_repo.get_by_ids([s.user_id for s in scores])
    rows: list[tuple[int, str, int]] = []
    for rank, score in enumerate(scores, start=1):
        user = users.get(score.user_id)
        name = user_link(user.username, user.full_name, user.id) if user else str(score.user_id)
        rows.append((rank, name, score.value))

    if is_bottom:
        p = formatter._p
        lines = ["🔻 <b>Антирейтинг</b>"]
        for rank, name, value in rows:
            lines.append(f"{rank}. {name} — {value} {p.pluralize(value)}")
        text = "\n".join(lines) if rows else "🔻 <b>Антирейтинг</b>\n<i>Нет данных</i>"
        await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
    else:
        await reply_and_delete(message, formatter.leaderboard(rows), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


@router.message(Command("stats"))
@inject
async def cmd_stats(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    score_repo: FromDishka[IScoreRepository],
    stats_repo: FromDishka[IUserStatsRepository],
    limits_repo: FromDishka[IDailyLimitsRepository],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    xp_service: FromDishka[XpService],
    config: FromDishka[AppConfig],
) -> None:
    """Подробная статистика пользователя: счёт, лимиты, реакции, игры."""
    chat_id = message.chat.id

    # Определяем цель: реплай → @username → сам
    target_user = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_user = await user_repo.get_by_id(message.reply_to_message.from_user.id)
    elif command.args:
        target_user = await user_repo.get_by_username(command.args.strip().lstrip("@"))
        if target_user is None:
            await reply_and_delete(message, formatter._t.get("error_user_not_found", "Пользователь не найден."))
            return

    if target_user is not None:
        user_id = target_user.id
        display_name = user_link(target_user.username, target_user.full_name, target_user.id)
    else:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        display_name = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)

    # Параллельно тянем все данные
    today = datetime.date.today()
    score_obj = await score_service.get_score(user_id, chat_id)
    rank = await score_repo.get_rank(user_id, chat_id)
    stats = await stats_repo.get(user_id, chat_id)
    daily = await limits_repo.get(user_id, chat_id, today)
    xp_val = await xp_service.get_xp(user_id, chat_id) if config.xp.enabled else 0

    p = formatter._p
    lc = config.limits
    icon = config.score.icon

    # ── Счёт + место ────────────────────────────────────────────
    score_val = score_obj.value
    rank_str = f"  · #{rank} в чате" if rank else ""
    score_line = f"{icon} <b>{score_val} {p.pluralize(score_val)}</b>{rank_str}"

    # ── Лимиты сегодня ──────────────────────────────────────────
    neg_used = daily.reactions_given
    neg_left = max(0, lc.daily_negative_given - neg_used)
    recv_used = daily.score_received
    recv_left = max(0, lc.daily_score_received - recv_used)

    limits_block = (
        f"<b>Сегодня:</b>\n"
        f"  ➖ Негативных реакций: {neg_used} / {lc.daily_negative_given}"
        f"  <i>(осталось {neg_left})</i>\n"
        f"  📥 Получено баллов: {recv_used} / {lc.daily_score_received}"
        f"  <i>(осталось {recv_left})</i>"
    )

    # ── Реакции за всё время ─────────────────────────────────────
    netto = stats.score_given - stats.score_taken
    netto_sign = "+" if netto >= 0 else ""
    reactions_block = (
        f"<b>Реакции (всё время):</b>\n"
        f"  🎁 Подарено: {stats.score_given} {p.pluralize(stats.score_given)}\n"
        f"  💀 Отнято: {stats.score_taken} {p.pluralize(stats.score_taken)}\n"
        f"  ∑ Нетто: {netto_sign}{netto} {p.pluralize(abs(netto))}"
    )

    # ── Игры ────────────────────────────────────────────────────
    total_wins = stats.wins_blackjack + stats.wins_slots + stats.wins_dice + stats.wins_giveaway + stats.wins_ttt
    games_block = (
        f"<b>Победы в играх:</b>\n"
        f"  🃏 Блекджек: {stats.wins_blackjack}\n"
        f"  🎰 Слоты: {stats.wins_slots}\n"
        f"  🎲 Кубики: {stats.wins_dice}\n"
        f"  🎟 Розыгрыши: {stats.wins_giveaway}\n"
        f"  🎮 Крестики-нолики: {stats.wins_ttt}\n"
        f"  <i>Итого: {total_wins} {_wins_plural(total_wins)}</i>"
    )

    # ── XP / Уровень ────────────────────────────────────────────
    xp_block = ""
    if config.xp.enabled:
        level = xp_service.compute_level(xp_val)
        max_level = config.xp.levels.max_level
        xp_per_level = config.xp.levels.xp_per_level
        xp_in_level = xp_val % xp_per_level
        bar_total = 10
        bar_filled = round(xp_in_level / xp_per_level * bar_total) if xp_per_level > 0 else bar_total
        bar = "█" * bar_filled + "░" * (bar_total - bar_filled)
        to_next = xp_service.xp_for_next_level(xp_val)
        if to_next is None:
            next_str = "<i>максимальный уровень</i>"
        else:
            next_str = f"до уровня {level + 1}: {to_next} XP"
        xp_block = (
            f"\n\n<b>Уровень:</b> {level}"
            + (f" / {max_level}" if level >= max_level else "")
            + f"\n  <code>[{bar}]</code> {xp_in_level} / {xp_per_level} XP"
            + f"\n  <i>Всего XP: {xp_val} · {next_str}</i>"
        )

    text = (
        f"📊 <b>Статистика</b> {display_name}\n\n"
        f"{score_line}"
        f"{xp_block}\n\n"
        f"{limits_block}\n\n"
        f"{reactions_block}\n\n"
        f"{games_block}"
    )
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


def _wins_plural(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "побед"
    r = n % 10
    if r == 1:
        return "победа"
    if 2 <= r <= 4:
        return "победы"
    return "побед"


@router.message(Command("history"))
@inject
async def cmd_history(
    message: Message,
    history_service: FromDishka[HistoryService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """История начислений за последние N дней (с пагинацией)."""
    if message.from_user is None:
        return
    chat_id = message.chat.id
    uid = message.from_user.id
    events = await history_service.get_history(chat_id)
    all_ids = list({e.actor_id for e in events} | {e.target_id for e in events})
    users = await user_repo.get_by_ids(all_ids)
    event_dicts: list[dict] = []
    for e in events:
        actor = users.get(e.actor_id)
        target = users.get(e.target_id)
        event_dicts.append(
            {
                "date": to_msk(e.created_at).strftime("%d.%m %H:%M") if e.created_at else "",
                "actor": user_link(actor.username, actor.full_name, actor.id) if actor else str(e.actor_id),
                "target": user_link(target.username, target.full_name, target.id) if target else str(e.target_id),
                "emoji": e.emoji,
                "delta": e.delta,
            }
        )
    page_size = config.system.history_page_size
    pages = [event_dicts[i : i + page_size] for i in range(0, max(1, len(event_dicts)), page_size)]
    _history_pages[(chat_id, uid)] = pages
    text = formatter.history(pages[0], config.history.retention_days)
    kb = _history_kb(0, len(pages), chat_id, uid)
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW, reply_markup=kb)


@router.callback_query(F.data.startswith("hist:"))
@inject
async def cb_history(
    callback: CallbackQuery,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if callback.data == "hist:noop":
        await safe_callback_answer(callback)
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await safe_callback_answer(callback)
        return
    _, chat_id_str, uid_str, page_str = parts
    try:
        chat_id, uid, page = int(chat_id_str), int(uid_str), int(page_str)
    except ValueError:
        await safe_callback_answer(callback)
        return
    if callback.from_user.id != uid:
        await safe_callback_answer(callback, "Это не твоя история.", show_alert=True)
        return
    pages = _history_pages.get((chat_id, uid))
    if not pages or page < 0 or page >= len(pages):
        await safe_callback_answer(callback)
        return
    text = formatter.history(pages[page], config.history.retention_days)
    kb = _history_kb(page, len(pages), chat_id, uid)
    try:
        await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, link_preview_options=NO_PREVIEW
        )
    except Exception:
        pass
    await safe_callback_answer(callback)


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
        f"{icon} <b>Текущие лимиты бота</b>\n\n"
        f"<b>Реакции:</b>\n"
        f"  ➕ Положительных одному участнику в сутки: {lc.daily_positive_per_target}\n"
        f"  ➖ Отрицательных в сутки (всего): {lc.daily_negative_given}\n"
        f"  Макс. кирчиков получателю в сутки: {lc.daily_score_received}\n"
        f"  Возраст сообщения: не старше {lc.max_message_age_hours} ч.\n\n"
        f"<b>История:</b>\n"
        f"  Хранится: {config.history.retention_days} дн.\n\n"
        f"<b>Мут:</b>\n"
        f"  Стоимость: {mc.cost_per_minute} {p.pluralize(mc.cost_per_minute)} / мин\n"
        f"  Диапазон: {mc.min_minutes}–{mc.max_minutes} мин\n"
        f"  Лимит: {mc.daily_limit} мутов в сутки\n"
        f"  Кулдаун на цель: {mc.target_cooldown_hours} ч.\n\n"
        f"<b>Тег:</b>\n"
        f"  Себе: {tc.cost_self} {p.pluralize(tc.cost_self)}\n"
        f"  Участнику: {tc.cost_member} {p.pluralize(tc.cost_member)}\n"
        f"  Админу: {tc.cost_admin} {p.pluralize(tc.cost_admin)}\n"
        f"  Создателю: {tc.cost_owner} {p.pluralize(tc.cost_owner)}\n"
        f"  Макс. длина: {tc.max_length} символов\n\n"
        f"<b>Блекджек:</b>\n"
        f"  Ставка: {bjc.min_bet}–{bjc.max_bet} {p.pluralize(bjc.max_bet)}"
    )
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


# ── Кэш для пагинации истории пользователя ───────────────────────
_uhistory_pages: TTLCache = TTLCache(maxsize=100, ttl=120)


def _uhistory_kb(page: int, total: int, chat_id: int, uid: int, target_id: int) -> InlineKeyboardMarkup | None:
    if total <= 1:
        return None
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"uhist:{chat_id}:{uid}:{target_id}:{page - 1}"))
    buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="uhist:noop"))
    if page < total - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"uhist:{chat_id}:{uid}:{target_id}:{page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


@router.message(Command("uhistory"))
@inject
async def cmd_uhistory(
    message: Message,
    command: CommandObject,
    history_service: FromDishka[HistoryService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """История всех операций с конкретным пользователем."""
    if message.from_user is None:
        return
    chat_id = message.chat.id
    uid = message.from_user.id

    # Определяем цель: reply или @username
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        target = await user_repo.get_by_id(ru.id)
    elif command.args:
        username = command.args.strip().lstrip("@")
        target = await user_repo.get_by_username(username)

    if target is None:
        await reply_and_delete(message,
            "Использование: <code>/uhistory @username</code> или реплай на сообщение.",
            parse_mode=ParseMode.HTML,
        )
        return

    events = await history_service.get_user_history(chat_id, target.id)
    target_link = user_link(target.username, target.full_name, target.id)

    if not events:
        await reply_and_delete(message,
            f"Нет событий для {target_link} за последние {config.history.retention_days} дн.",
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    all_ids = list({e.actor_id for e in events} | {e.target_id for e in events})
    users = await user_repo.get_by_ids(all_ids)
    event_dicts: list[dict] = []
    for e in events:
        actor = users.get(e.actor_id)
        tgt = users.get(e.target_id)
        event_dicts.append(
            {
                "date": to_msk(e.created_at).strftime("%d.%m %H:%M") if e.created_at else "",
                "actor": user_link(actor.username, actor.full_name, actor.id) if actor else str(e.actor_id),
                "target": user_link(tgt.username, tgt.full_name, tgt.id) if tgt else str(e.target_id),
                "emoji": e.emoji,
                "delta": e.delta,
            }
        )

    page_size = config.system.history_page_size
    pages = [event_dicts[i : i + page_size] for i in range(0, max(1, len(event_dicts)), page_size)]
    _uhistory_pages[(chat_id, uid, target.id)] = pages

    rows = [formatter._t["history_row"].format(**e) for e in pages[0]]
    title = (
        f"📋 История операций: {target_link}\n"
        f"<i>За последние {config.history.retention_days} дн., всего {len(events)} событий</i>"
    )
    body = "\n".join(rows)
    text = f"{title}\n<blockquote expandable>{body}</blockquote>"

    kb = _uhistory_kb(0, len(pages), chat_id, uid, target.id)
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW, reply_markup=kb)


@router.callback_query(F.data.startswith("uhist:"))
@inject
async def cb_uhistory(
    callback: CallbackQuery,
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if callback.data == "uhist:noop":
        await safe_callback_answer(callback)
        return

    parts = callback.data.split(":")
    if len(parts) != 5:
        await safe_callback_answer(callback)
        return
    _, chat_id_str, uid_str, target_id_str, page_str = parts
    try:
        chat_id, uid, target_id, page = int(chat_id_str), int(uid_str), int(target_id_str), int(page_str)
    except ValueError:
        await safe_callback_answer(callback)
        return

    if callback.from_user.id != uid:
        await safe_callback_answer(callback, "Это не твой запрос.", show_alert=True)
        return

    pages = _uhistory_pages.get((chat_id, uid, target_id))
    if not pages or page < 0 or page >= len(pages):
        await safe_callback_answer(callback)
        return

    target = await user_repo.get_by_id(target_id)
    target_link = user_link(target.username, target.full_name, target.id) if target else str(target_id)
    total_events = sum(len(p) for p in pages)

    rows = [formatter._t["history_row"].format(**e) for e in pages[page]]
    title = (
        f"📋 История операций: {target_link}\n"
        f"<i>За последние {config.history.retention_days} дн., всего {total_events} событий</i>"
    )
    body = "\n".join(rows)
    text = f"{title}\n<blockquote expandable>{body}</blockquote>"

    kb = _uhistory_kb(page, len(pages), chat_id, uid, target_id)
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            link_preview_options=NO_PREVIEW,
        )
    except Exception:
        pass
    await safe_callback_answer(callback)

@router.message(Command("level"))
@inject
async def cmd_level(
    message: Message,
    command: CommandObject,
    xp_service: FromDishka[XpService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    """Показывает уровень и XP вызвавшего или указанного пользователя."""
    if not config.xp.enabled:
        await reply_and_delete(message, "XP-система отключена.")
        return

    chat_id = message.chat.id
    target_user = None
    if command.args:
        target_user = await user_repo.get_by_username(command.args.strip().lstrip("@"))
        if target_user is None:
            await reply_and_delete(message, formatter._t.get("error_user_not_found", "Пользователь не найден."))
            return
        display_name = user_link(target_user.username, target_user.full_name, target_user.id)
        user_id = target_user.id
    else:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        display_name = user_link(message.from_user.username, message.from_user.full_name or "", message.from_user.id)

    xp_val = await xp_service.get_xp(user_id, chat_id)
    level = xp_service.compute_level(xp_val)
    max_level = config.xp.levels.max_level
    xp_per_level = config.xp.levels.xp_per_level
    xp_in_level = xp_val % xp_per_level
    bar_total = 10
    bar_filled = round(xp_in_level / xp_per_level * bar_total) if xp_per_level > 0 else bar_total
    bar = "█" * bar_filled + "░" * (bar_total - bar_filled)
    to_next = xp_service.xp_for_next_level(xp_val)
    if to_next is None:
        next_str = "<i>максимальный уровень 🏆</i>"
    else:
        next_str = f"до уровня {level + 1}: {to_next} XP"

    level_display = f"{level}" + (f" / {max_level}" if level >= max_level else f" / {max_level}")
    text = (
        f"⚡ <b>Уровень</b> {display_name}\n\n"
        f"  Уровень: <b>{level_display}</b>\n"
        f"  <code>[{bar}]</code> {xp_in_level} / {xp_per_level} XP\n"
        f"  Всего XP: <b>{xp_val}</b>\n"
        f"  <i>{next_str}</i>"
    )
    await reply_and_delete(message, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


@router.message(Command("toplevel"))
@inject
async def cmd_toplevel(
    message: Message,
    xp_service: FromDishka[XpService],
    user_repo: FromDishka[IUserRepository],
    config: FromDishka[AppConfig],
) -> None:
    """Топ участников чата по уровню XP."""
    if not config.xp.enabled:
        await reply_and_delete(message, "XP-система отключена.")
        return

    chat_id = message.chat.id
    entries = await xp_service.get_top(chat_id, limit=10)

    if not entries:
        await reply_and_delete(
            message,
            "⚡ <b>Топ по уровню</b>\n\n<i>Нет данных</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    users = await user_repo.get_by_ids([e.user_id for e in entries])
    lines = ["⚡ <b>Топ по уровню</b>"]
    for rank, entry in enumerate(entries, start=1):
        user = users.get(entry.user_id)
        name = user_link(user.username, user.full_name, user.id) if user else str(entry.user_id)
        level = xp_service.compute_level(entry.xp)
        lines.append(f"{rank}. {name} — Ур. <b>{level}</b> ({entry.xp} XP)")

    await reply_and_delete(
        message, "\n".join(lines), parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )
