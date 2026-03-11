"""Хендлеры блекджека: /bj <ставка>, кнопки Hit / Stand."""

from __future__ import annotations

import logging

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

from bot.application.blackjack_service import (
    BlackjackRound,
    format_hand,
    hand_score,
)
from bot.application.score_service import ScoreService
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter

logger = logging.getLogger(__name__)

router = Router(name="blackjack")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

# Активные раунды: (user_id, chat_id) -> BlackjackRound
_active_games: dict[tuple[int, int], BlackjackRound] = {}

# ── Inline-клавиатура ────────────────────────────────────────────

_BJ_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🃏 Ещё", callback_data="bj_hit"),
            InlineKeyboardButton(text="🛑 Хватит", callback_data="bj_stand"),
        ]
    ]
)


# ── Вспомогательные ─────────────────────────────────────────────

def _render_table(
    rnd: BlackjackRound,
    *,
    reveal: bool = False,
    result_line: str = "",
) -> str:
    """Отрисовка стола: карты и очки."""
    dealer_cards = format_hand(rnd.dealer_hand, hide_second=not reveal)
    player_cards = format_hand(rnd.player_hand)

    dealer_score = hand_score(rnd.dealer_hand) if reveal else "?"
    player_score = hand_score(rnd.player_hand)

    lines = [
        f"<b>Дилер</b> [{dealer_score}]:  {dealer_cards}",
        f"<b>Ты</b>    [{player_score}]:  {player_cards}",
    ]
    if result_line:
        lines.append("")
        lines.append(result_line)
    return "\n".join(lines)


_RESULT_TEXT = {
    "player_blackjack": "🎰 Блекджек! Ты получаешь {win} {sw}!",
    "player_win":       "🏆 Ты выиграл {win} {sw}!",
    "dealer_bust":      "🏆 Дилер перебрал! Ты выиграл {win} {sw}!",
    "dealer_win":       "😔 Дилер выиграл. Ты потерял {loss} {sw}.",
    "player_bust":      "💥 Перебор! Ты потерял {loss} {sw}.",
    "push":             "🤝 Ничья. Ставка возвращена.",
}


def _result_line(rnd: BlackjackRound, pluralizer) -> str:
    delta = rnd.payout_delta()
    tpl = _RESULT_TEXT[rnd.result.value]
    return tpl.format(
        win=abs(delta),
        loss=abs(delta),
        sw=pluralizer.pluralize(abs(delta)) if delta != 0 else "",
    )


# ── /help_bj ─────────────────────────────────────────────────────

@router.message(Command("help_bj"))
@inject
async def cmd_help_bj(
    message: Message,
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    p = formatter._p
    bjc = config.blackjack

    text = (
        "🃏 <b>Блекджек — правила</b>\n"
        "\n"
        "<b>Цель:</b> набрать больше очков, чем дилер, но не больше 21.\n"
        "\n"
        "<b>Стоимость карт:</b>\n"
        "  2–10 — по номиналу\n"
        "  J, Q, K — 10 очков\n"
        "  A (туз) — 11 или 1 (автоматически снижается при переборе)\n"
        "\n"
        "<b>Ход игры:</b>\n"
        "  1. Напиши /bj &lt;ставка&gt; — получишь две карты\n"
        "  2. Жми «🃏 Ещё» чтобы взять карту\n"
        "  3. Жми «🛑 Хватит» чтобы остановиться\n"
        "  4. Дилер доберёт карты до 17+ и вскроется\n"
        "\n"
        "<b>Выплаты:</b>\n"
        "  Блекджек (21 с двух карт) — ×1.5 ставки\n"
        "  Обычный выигрыш — ×1 ставки\n"
        "  Ничья — ставка возвращается\n"
        "  Проигрыш или перебор — ставка сгорает\n"
        "\n"
        f"<b>Ставка:</b> от {bjc.min_bet} до {bjc.max_bet} {p.pluralize(bjc.max_bet)}\n"
        "\n"
        "Удачи! 🍀"
    )

    await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


# ── /bj <ставка> ────────────────────────────────────────────────

@router.message(Command("bj"))
@inject
async def cmd_blackjack(
    message: Message,
    command: CommandObject,
    score_service: FromDishka[ScoreService],
    formatter: FromDishka[MessageFormatter],
    config: FromDishka[AppConfig],
) -> None:
    if message.from_user is None:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    key = (user_id, chat_id)

    if key in _active_games:
        await message.reply("У тебя уже есть активная игра. Доиграй текущую!")
        return

    # Парсим ставку
    bj_cfg = getattr(config, "blackjack", None)
    min_bet = bj_cfg.min_bet if bj_cfg else 1
    max_bet = bj_cfg.max_bet if bj_cfg else 500

    if not command.args:
        await message.reply(
            f"Использование: /bj &lt;ставка&gt;\n"
            f"Ставка: от {min_bet} до {max_bet} {formatter._p.pluralize(max_bet)}.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        bet = int(command.args.strip())
    except ValueError:
        await message.reply("Ставка должна быть числом.")
        return

    if bet < min_bet or bet > max_bet:
        await message.reply(
            f"Ставка: от {min_bet} до {max_bet} {formatter._p.pluralize(max_bet)}."
        )
        return

    # Проверяем баланс
    score = await score_service.get_score(user_id, chat_id)
    if score.value < bet:
        await message.reply(
            f"Недостаточно баллов. У тебя: {score.value} {formatter._p.pluralize(score.value)}."
        )
        return

    # Создаём раунд
    rnd = BlackjackRound(player_id=user_id, chat_id=chat_id, bet=bet)
    rnd.deal()
    _active_games[key] = rnd

    if rnd.finished:
        # Натуральный блекджек — сразу результат
        delta = rnd.payout_delta()
        if delta != 0:
            await score_service.add_score(
                user_id, chat_id, delta, admin_id=user_id,
            )
        del _active_games[key]
        text = _render_table(
            rnd,
            reveal=True,
            result_line=_result_line(rnd, formatter._p),
        )
        await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
        return

    # Обычное начало — показываем стол с кнопками
    text = (
        f"🃏 <b>Блекджек</b> — ставка {bet} {formatter._p.pluralize(bet)}\n\n"
        + _render_table(rnd)
    )
    await message.reply(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_BJ_KB,
        link_preview_options=NO_PREVIEW,
    )


# ── Callback: Hit ────────────────────────────────────────────────

@router.callback_query(F.data == "bj_hit")
@inject
async def cb_hit(
    callback: CallbackQuery,
    score_service: FromDishka[ScoreService],
    formatter: FromDishka[MessageFormatter],
) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    key = (user_id, chat_id)

    rnd = _active_games.get(key)
    if rnd is None:
        await callback.answer("Нет активной игры.", show_alert=True)
        return

    rnd.hit()

    if rnd.finished:
        delta = rnd.payout_delta()
        if delta != 0:
            await score_service.add_score(
                user_id, chat_id, delta, admin_id=user_id,
            )
        del _active_games[key]
        text = _render_table(
            rnd,
            reveal=True,
            result_line=_result_line(rnd, formatter._p),
        )
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=None,
            link_preview_options=NO_PREVIEW,
        )
        await callback.answer()
        return

    # Продолжаем
    text = (
        f"🃏 <b>Блекджек</b> — ставка {rnd.bet} {formatter._p.pluralize(rnd.bet)}\n\n"
        + _render_table(rnd)
    )
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_BJ_KB,
        link_preview_options=NO_PREVIEW,
    )
    await callback.answer()


# ── Callback: Stand ──────────────────────────────────────────────

@router.callback_query(F.data == "bj_stand")
@inject
async def cb_stand(
    callback: CallbackQuery,
    score_service: FromDishka[ScoreService],
    formatter: FromDishka[MessageFormatter],
) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    key = (user_id, chat_id)

    rnd = _active_games.get(key)
    if rnd is None:
        await callback.answer("Нет активной игры.", show_alert=True)
        return

    rnd.stand()

    delta = rnd.payout_delta()
    if delta != 0:
        await score_service.add_score(
            user_id, chat_id, delta, admin_id=user_id,
        )
    del _active_games[key]

    text = _render_table(
        rnd,
        reveal=True,
        result_line=_result_line(rnd, formatter._p),
    )
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=None,
        link_preview_options=NO_PREVIEW,
    )
    await callback.answer()
