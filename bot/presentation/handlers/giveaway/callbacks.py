from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka.integrations.aiogram import FromDishka, inject

from bot.application.giveaway_service import GiveawayService
from bot.application.interfaces.user_repository import IUserRepository
from bot.domain.entities import User
from bot.presentation.handlers.giveaway.helpers import _join_kb

router = Router(name="giveaway_callbacks")


# ─── Callback: кнопка «Участвовать» ─────────────────────────────────────────


@router.callback_query(F.data.startswith("giveaway:join:"))
@inject
async def cb_join(
    cb: CallbackQuery,
    service: FromDishka[GiveawayService],
    user_repo: FromDishka[IUserRepository],
) -> None:
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
        await cb.answer("Ты уже участвуешь или розыгрыш завершён.", show_alert=False)
        return

    count = await service.count_participants(giveaway_id)
    await cb.answer("✅ Ты в игре!", show_alert=False)

    try:
        await cb.message.edit_reply_markup(reply_markup=_join_kb(giveaway_id, count))
    except Exception:
        pass
