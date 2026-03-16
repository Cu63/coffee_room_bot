"""Трекер: привязка чатов, топики, репорты, ченджлог.

Команды:
  /settracker [tracker_chat_id]  — показать ID чата / привязать трекер
  /settopic <тип>                — назначить топик (bug|feature|report|changelog)
  /bug <текст>                   — баг-репорт
  /feature <текст>               — запрос фичи
  /report <текст>                — жалоба (или reply на сообщение)
  /updates                       — список обновлений бота
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.domain.tz import to_msk
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.redis_store import RedisStore
from bot.presentation.utils import NO_PREVIEW, reply_and_delete

logger = logging.getLogger(__name__)
router = Router(name="tracker")

_TOPIC_TYPES = {"bug", "feature", "report", "changelog"}
_TOPIC_LABELS = {
    "bug":       "🐛 Баги",
    "feature":   "💡 Фичи",
    "report":    "🚨 Репорты",
    "changelog": "📋 Changelog",
}
_TOPIC_ICONS = {
    "bug":     "🐛",
    "feature": "💡",
    "report":  "🚨",
}


# ── helpers ─────────────────────────────────────────────────────────────────

async def _is_admin(message: Message) -> bool:
    if message.from_user is None or message.bot is None:
        return False
    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in ("administrator", "creator")


async def _send_to_tracker(
    bot: Bot,
    tracker_chat_id: int,
    thread_id: int | None,
    text: str,
) -> int | None:
    """Отправить сообщение в трекер (в топик если есть). Возвращает message_id."""
    try:
        kwargs: dict = dict(chat_id=tracker_chat_id, text=text, parse_mode=ParseMode.HTML)
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        msg = await bot.send_message(**kwargs)
        return msg.message_id
    except Exception:
        logger.exception("tracker: не удалось отправить в чат %d топик %s", tracker_chat_id, thread_id)
        return None


async def _send_report(
    message: Message,
    store: RedisStore,
    config: AppConfig,
    report_type: str,
    text: str,
    target_line: str = "",
) -> None:
    """
    Общая логика отправки репорта/фичи/бага:
    1. Если трекер привязан → шлём в топик.
    2. Иначе (bug) → DM получателям из config.bug.recipients.
    """
    if message.from_user is None or message.bot is None:
        return

    sender = message.from_user
    username_part = f" @{sender.username}" if sender.username else ""
    chat_title = message.chat.title or str(message.chat.id)
    icon = _TOPIC_ICONS.get(report_type, "📩")
    label = _TOPIC_LABELS.get(report_type, report_type)

    report_id = await store.tracker_next_id()
    report_body = (
        f"{icon} <b>{label} #{report_id}</b>\n"
        f"От: <a href=\"tg://user?id={sender.id}\">{sender.full_name}</a>{username_part}\n"
        f"Чат: {chat_title} (<code>{message.chat.id}</code>)\n"
    )
    if target_line:
        report_body += f"Цель: {target_line}\n"
    report_body += f"\n{text}"

    tracker_chat_id = await store.tracker_get_tracker_id(message.chat.id)

    if tracker_chat_id:
        thread_id = await store.tracker_get_topic(tracker_chat_id, report_type)
        sent_id = await _send_to_tracker(message.bot, tracker_chat_id, thread_id, report_body)
        if sent_id:
            await reply_and_delete(message, f"✅ #{report_id} принят, спасибо!", delay=10)
            try:
                await message.delete()
            except Exception:
                pass
            return
        # Не удалось → логируем и пробуем DM-фоллбэк только для багов
        logger.error("tracker: не удалось отправить в трекер %d", tracker_chat_id)

    # DM-фоллбэк (только для bug, для feature/report молча логируем)
    if report_type == "bug" and config.bug.recipients:
        sent = 0
        for uid in config.bug.recipients:
            try:
                await message.bot.send_message(uid, report_body, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                logger.warning("tracker: не удалось отправить DM пользователю %d", uid)
        if sent:
            await reply_and_delete(message, f"✅ #{report_id} принят, спасибо!", delay=10)
        else:
            await reply_and_delete(message, "⚠️ Не удалось доставить репорт.", delay=10)
    elif not tracker_chat_id:
        logger.warning("tracker: трекер не настроен для чата %d, репорт потерян", message.chat.id)
        await reply_and_delete(message, "⚠️ Трекер не настроен. Обратитесь к администратору.")

    try:
        await message.delete()
    except Exception:
        pass


# ── /settracker ──────────────────────────────────────────────────────────────

@router.message(Command("settracker"), F.chat.type.in_({"group", "supergroup"}))
@inject
async def cmd_settracker(
    message: Message,
    command: CommandObject,
    store: FromDishka[RedisStore],
) -> None:
    """Без аргументов — показать ID этого чата.
    С аргументом <tracker_chat_id> — привязать трекер."""
    if not await _is_admin(message):
        await reply_and_delete(message, "❌ Только администраторы могут настраивать трекер.")
        return

    if not command.args:
        current = await store.tracker_get_tracker_id(message.chat.id)
        status = (
            f"Текущий трекер: <code>{current}</code>"
            if current
            else "Трекер <b>не</b> привязан."
        )
        await reply_and_delete(
            message,
            f"ℹ️ ID этого чата: <code>{message.chat.id}</code>\n"
            f"{status}\n\n"
            f"Привязать трекер: <code>/settracker &lt;tracker_chat_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        tracker_chat_id = int(command.args.strip())
    except ValueError:
        await reply_and_delete(message, "❌ Неверный ID. Пример: <code>/settracker -1001234567890</code>", parse_mode=ParseMode.HTML)
        return

    await store.tracker_set_source(message.chat.id, tracker_chat_id)
    await reply_and_delete(
        message,
        f"✅ Трекер привязан: <code>{tracker_chat_id}</code>",
        parse_mode=ParseMode.HTML,
    )
    logger.info("tracker: чат %d привязан к трекеру %d", message.chat.id, tracker_chat_id)


# ── /settopic ────────────────────────────────────────────────────────────────

@router.message(Command("settopic"), F.chat.type == "supergroup")
@inject
async def cmd_settopic(
    message: Message,
    command: CommandObject,
    store: FromDishka[RedisStore],
) -> None:
    """Назначить текущий топик для типа обращений.
    Использование (внутри топика): /settopic bug|feature|report|changelog"""
    if not await _is_admin(message):
        await reply_and_delete(message, "❌ Только администраторы могут настраивать топики.")
        return

    thread_id = message.message_thread_id
    if thread_id is None:
        await reply_and_delete(
            message,
            "❌ Команда должна быть отправлена <b>внутри топика</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    topic_type = (command.args or "").strip().lower()
    if topic_type not in _TOPIC_TYPES:
        types_list = " | ".join(sorted(_TOPIC_TYPES))
        await reply_and_delete(
            message,
            f"❌ Неверный тип. Допустимые: <code>{types_list}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await store.tracker_set_topic(message.chat.id, topic_type, thread_id)
    label = _TOPIC_LABELS[topic_type]
    await reply_and_delete(
        message,
        f"✅ Топик назначен: {label}\n"
        f"thread_id: <code>{thread_id}</code>",
        parse_mode=ParseMode.HTML,
    )
    logger.info(
        "tracker: чат %d топик %d назначен для '%s'",
        message.chat.id, thread_id, topic_type,
    )


# ── /bug ─────────────────────────────────────────────────────────────────────

@router.message(Command("bug"), F.chat.type.in_({"group", "supergroup"}))
@inject
async def cmd_bug(
    message: Message,
    command: CommandObject,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
) -> None:
    """Баг-репорт. Если трекер настроен — в топик bug, иначе DM получателям."""
    text = (command.args or "").strip()
    if not text:
        await reply_and_delete(
            message,
            "❗ Укажи описание: <code>/bug &lt;текст&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await _send_report(message, store, config, "bug", text)


# ── /feature ─────────────────────────────────────────────────────────────────

@router.message(Command("feature"), F.chat.type.in_({"group", "supergroup"}))
@inject
async def cmd_feature(
    message: Message,
    command: CommandObject,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
) -> None:
    """Запрос фичи."""
    text = (command.args or "").strip()
    if not text:
        await reply_and_delete(
            message,
            "❗ Укажи описание: <code>/feature &lt;текст&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    await _send_report(message, store, config, "feature", text)


# ── /report ──────────────────────────────────────────────────────────────────

@router.message(Command("report"), F.chat.type.in_({"group", "supergroup"}))
@inject
async def cmd_report(
    message: Message,
    command: CommandObject,
    store: FromDishka[RedisStore],
    config: FromDishka[AppConfig],
) -> None:
    """Жалоба на пользователя. Reply на сообщение + /report причина
    или /report @username причина."""
    text = (command.args or "").strip()
    target_line = ""

    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        uname = f" (@{ru.username})" if ru.username else ""
        target_line = f"<a href=\"tg://user?id={ru.id}\">{ru.full_name}</a>{uname}"
        reason = text or "(причина не указана)"
    elif text:
        # /report @username причина — берём первый токен как юзернейм
        parts = text.split(None, 1)
        if parts[0].startswith("@"):
            target_line = parts[0]
            reason = parts[1] if len(parts) > 1 else "(причина не указана)"
        else:
            reason = text
    else:
        await reply_and_delete(
            message,
            "❗ Ответь на сообщение пользователя или укажи: "
            "<code>/report @username причина</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await _send_report(message, store, config, "report", reason, target_line)


# ── /updates ─────────────────────────────────────────────────────────────────

@router.message(Command("updates"))
@inject
async def cmd_updates(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    """Показать список обновлений бота из ченджлог-топика трекера."""
    tracker_chat_id = await store.tracker_get_tracker_id(message.chat.id)

    # В личке или в непривязанном чате ищем любой ченджлог
    if tracker_chat_id is None:
        all_logs = await store.changelog_scan_all()
        if not all_logs:
            await reply_and_delete(message, "ℹ️ Список обновлений пока пуст.")
            return
        # Берём первый найденный
        tracker_chat_id, entries = all_logs[0]
    else:
        entries = await store.changelog_get_all(tracker_chat_id)

    if not entries:
        await reply_and_delete(message, "ℹ️ Список обновлений пока пуст.")
        return

    lines = ["📋 <b>Обновления бота</b>\n"]
    for entry in entries[:10]:
        date_str = entry.get("date", "")
        raw_text = entry.get("text", "").strip()
        # Первая строка — заголовок версии, остальное — тело
        parts = raw_text.split("\n", 1)
        title = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
        header = f"<b>{title}</b>"
        if date_str:
            header += f"  <i>· {date_str}</i>"
        lines.append(header)
        if body:
            lines.append(f"<blockquote>{body}</blockquote>")
        lines.append("")

    await reply_and_delete(
        message,
        "\n".join(lines).rstrip(),
        parse_mode=ParseMode.HTML,
        link_preview_options=NO_PREVIEW,
        delay=120,
    )


# ── Слушатель changelog-топика ───────────────────────────────────────────────
# Ловим новые и отредактированные сообщения в changelog-топике трекер-чата.
# Определяем по thread_id: если он совпадает с зарегистрированным changelog-топиком
# данного чата — сохраняем в Redis.

async def _handle_changelog_message(message: Message, store: RedisStore, is_edit: bool = False) -> None:
    """Общая логика обработки сообщения в changelog-топике."""
    if message.message_thread_id is None or not message.text:
        return

    chat_id = message.chat.id
    thread_id = message.message_thread_id

    changelog_thread = await store.tracker_get_topic(chat_id, "changelog")
    if changelog_thread != thread_id:
        return  # Не changelog-топик

    # Только сообщения от администраторов (фильтруем мусор)
    if message.from_user:
        member = await message.bot.get_chat_member(chat_id, message.from_user.id)
        if member.status not in ("administrator", "creator"):
            return

    date_str = to_msk(message.date).strftime("%d.%m.%Y") if message.date else ""
    await store.changelog_add(
        tracker_chat_id=chat_id,
        message_id=message.message_id,
        text=message.text,
        date=date_str,
    )
    action = "обновлена" if is_edit else "добавлена"
    logger.info(
        "changelog: запись %s в чате %d (message_id=%d)",
        action, chat_id, message.message_id,
    )


@router.message(F.chat.type == "supergroup", F.message_thread_id.is_(True), ~F.text.startswith("/"))
@inject
async def on_changelog_message(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    await _handle_changelog_message(message, store, is_edit=False)


@router.edited_message(F.chat.type == "supergroup", F.message_thread_id.is_(True))
@inject
async def on_changelog_edited(
    message: Message,
    store: FromDishka[RedisStore],
) -> None:
    await _handle_changelog_message(message, store, is_edit=True)