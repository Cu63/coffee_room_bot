"""Бизнес-логика режимов чата (silence / gif)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import ChatPermissions

from bot.application.interfaces.chatmode_repository import ChatmodeEntry, IChatmodeRepository
from bot.application.score_service import ScoreService
from bot.domain.tz import TZ_MSK

# Права для каждого режима
# silence: запрещаем всё, кроме реакций (реакции не входят в ChatPermissions — они всегда доступны)
_SILENCE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_polls=False,
    can_send_other_messages=False,  # стикеры + гифки
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

# gif: разрешаем ТОЛЬКО стикеры+гифки (can_send_other_messages)
_GIF_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_polls=False,
    can_send_other_messages=True,   # стикеры + гифки разрешены
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

_MODE_PERMS: dict[str, ChatPermissions] = {
    "silence": _SILENCE_PERMS,
    "gif": _GIF_PERMS,
}

# Поля ChatPermissions которые мы сохраняем/восстанавливаем
_PERM_FIELDS = [
    "can_send_messages",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_audios",
    "can_send_documents",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
]


@dataclass(slots=True)
class ActivateResult:
    success: bool
    error: str | None = None          # причина отказа
    cost: int = 0
    new_balance: int = 0


def perms_to_dict(perms: ChatPermissions) -> dict:
    return {f: getattr(perms, f, None) for f in _PERM_FIELDS}


def dict_to_perms(d: dict) -> ChatPermissions:
    return ChatPermissions(**{f: d.get(f) for f in _PERM_FIELDS})


class ChatmodeService:
    def __init__(
        self,
        repo: IChatmodeRepository,
        score_service: ScoreService,
    ) -> None:
        self._repo = repo
        self._score = score_service

    async def get_active(self, chat_id: int) -> ChatmodeEntry | None:
        return await self._repo.get(chat_id)

    async def activate(
        self,
        bot: Bot,
        chat_id: int,
        user_id: int,
        mode: str,
        minutes: int,
        cost_per_minute: int,
    ) -> ActivateResult:
        """Активировать режим: снять баллы, сохранить права, выставить ограничения."""
        if mode not in _MODE_PERMS:
            return ActivateResult(success=False, error=f"Неизвестный режим: {mode!r}")

        # Проверяем, нет ли уже активного режима
        existing = await self._repo.get(chat_id)
        if existing is not None:
            return ActivateResult(success=False, error="already_active")

        total_cost = cost_per_minute * minutes

        # Снимаем баллы
        from bot.application.score_service import SPECIAL_EMOJI
        spend = await self._score.spend_score(
            actor_id=user_id,
            target_id=user_id,
            chat_id=chat_id,
            cost=total_cost,
            emoji=SPECIAL_EMOJI.get("chatmode", "🔒"),
        )
        if not spend.success:
            return ActivateResult(
                success=False,
                error="not_enough",
                cost=total_cost,
                new_balance=spend.current_balance,
            )

        # Сохраняем текущие права чата
        chat = await bot.get_chat(chat_id)
        current_perms = chat.permissions or ChatPermissions()
        saved = perms_to_dict(current_perms)

        now = datetime.now(TZ_MSK)
        entry = ChatmodeEntry(
            chat_id=chat_id,
            mode=mode,
            activated_by=user_id,
            activated_at=now,
            expires_at=now + timedelta(minutes=minutes),
            saved_perms=saved,
        )
        await self._repo.save(entry)

        # Выставляем ограничения
        await bot.set_chat_permissions(
            chat_id,
            _MODE_PERMS[mode],
            use_independent_chat_permissions=True,
        )

        return ActivateResult(success=True, cost=total_cost, new_balance=spend.new_balance)

    async def deactivate(self, bot: Bot, entry: ChatmodeEntry) -> None:
        """Восстановить права чата и удалить запись."""
        try:
            restored = dict_to_perms(entry.saved_perms)
            await bot.set_chat_permissions(
                entry.chat_id,
                restored,
                use_independent_chat_permissions=True,
            )
        except Exception:
            pass
        await self._repo.delete(entry.chat_id)

    async def get_expired(self) -> list[ChatmodeEntry]:
        return await self._repo.get_expired(datetime.now(TZ_MSK))