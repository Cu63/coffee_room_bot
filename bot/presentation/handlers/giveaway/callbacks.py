from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.giveaway_service import GiveawayService
from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.entities import User
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.handlers.giveaway.helpers import _join_kb
from bot.presentation.utils import callback_message

router = Router(name="giveaway_callbacks")


# ─── Callback: кнопка «Участвовать» ─────────────────────────────────────────


@router.callback_query(F.data.startswith("giveaway:join:"))
@inject
async def cb_join(
    cb: CallbackQuery,
    service: FromDishka[GiveawayService],
    user_repo: FromDishka[IUserRepository],
    formatter: FromDishka[MessageFormatter],
) -> None:
    if cb.data is None:
        await cb.answer()
        return
    giveaway_id = int(cb.data.split(":")[2])
    user_id = cb.from_user.id

    # Upsert пользователя — иначе FK на scores/users упадёт при начислении баллов
    await user_repo.upsert(
        User(
            id=user_id,
            username=cb.from_user.username,
            full_name=cb.from_user.full_name,
        )
    )

    joined = await service.join(giveaway_id, user_id)
    if not joined:
        await cb.answer(formatter._t["giveaway_already_joined"], show_alert=False)
        return

    count = await service.count_participants(giveaway_id)
    await cb.answer(formatter._t["giveaway_joined"], show_alert=False)

    # Розыгрыш может идти дольше 48 часов: такое сообщение Telegram отдаёт без кнопок — счётчик не обновить
    message = callback_message(cb)
    if message is not None:
        try:
            await message.edit_reply_markup(reply_markup=_join_kb(giveaway_id, count, formatter._t))
        except Exception:
            pass
