"""Хендлер слотов: /slots <ставка>"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import LinkPreviewOptions, Message
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.slots_service import SlotsService, SlotsConfig, SpinOutcome
from bot.infrastructure.config_loader import AppConfig
from bot.infrastructure.message_formatter import MessageFormatter

logger = logging.getLogger(__name__)
router = Router(name="slots")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def _render_reels(reels: list[str]) -> str:
    return " | ".join(reels)


_OUTCOME_TEXT = {
    SpinOutcome.JACKPOT:   "🎰 ДЖЕКПОТ! Ты выиграл {win} {sw}!",
    SpinOutcome.WIN:       "🏆 Выигрыш! +{win} {sw}!",
    SpinOutcome.NEAR_MISS: "😬 Почти! Потерял {loss} {sw}.",
    SpinOutcome.LOSS:      "💸 Мимо. Потерял {loss} {sw}.",
}


@router.message(Command("slots"))
@inject
async def cmd_slots(
    message: Message,
    command: CommandObject,
    slots_service: FromDishka[SlotsService],
    slots_config: FromDishka[SlotsConfig],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if message.from_user is None:
        return

    p = formatter._p
    cfg = slots_config

    if not command.args:
        await message.reply(
            f"🎰 <b>Слоты</b>\n\n"
            f"Использование: /slots &lt;ставка&gt;\n"
            f"Ставка: от {cfg.min_bet} до {cfg.max_bet} {p.pluralize(cfg.max_bet)}\n\n"
            f"Символы:\n" +
            "\n".join(
                f"  {s.emoji} × {s.multiplier:.0f} (три в ряд)"
                for s in sorted(cfg.symbols, key=lambda x: x.multiplier)
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
        return

    try:
        bet = int(command.args.strip())
    except ValueError:
        await message.reply("Ставка должна быть числом.")
        return

    result = await slots_service.spin(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        bet=bet,
    )

    # Обработка ошибок
    if result == "invalid_bet":
        await message.reply(
            f"Ставка: от {cfg.min_bet} до {cfg.max_bet} {p.pluralize(cfg.max_bet)}."
        )
        return
    if result == "not_enough":
        await message.reply("Недостаточно баллов.")
        return
    if result == "guard_rejected":
        remaining = slots_service.cooldown_remaining(message.from_user.id, message.chat.id)
        if remaining:
            minutes = int(remaining.total_seconds() // 60)
            seconds = int(remaining.total_seconds() % 60)
            await message.reply(f"Следующее кручение через {minutes}м {seconds}с.")
        return
        # Рендерим результат
    reels_str = _render_reels(result.reels)
    outcome_tpl = _OUTCOME_TEXT[result.outcome]
    outcome_str = outcome_tpl.format(
        win=abs(result.delta),
        loss=abs(result.delta),
        sw=p.pluralize(abs(result.delta)),
    )

    text = (
        f"🎰 <b>Слоты</b> — ставка {bet} {p.pluralize(bet)}\n\n"
        f"┌ {reels_str} ┐\n\n"
        f"{outcome_str}\n"
        f"Баланс: {result.new_balance} {p.pluralize(result.new_balance)}"
    )

    await message.reply(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)