"""GenAiService — генерация картинок по описанию за кирчики (/genai)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.application.score_service import SPECIAL_EMOJI, ScoreService
from bot.domain.ledger import LedgerKind
from bot.infrastructure.config_loader import GenaiConfig
from bot.infrastructure.gigachat_client import GigaChatClient, GigaChatError, GigaChatNoImage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GenAiResult:
    success: bool
    image: bytes | None = None
    cost: int = 0
    new_balance: int = 0
    current_balance: int = 0  # баланс при отказе «недостаточно баллов»
    # Причина отказа: "not_enough" | "no_image" | "error"
    reason: str = ""


class GenAiService:
    """Оркестрирует /genai: списание баллов → запрос к GigaChat → возврат при сбое.

    Баллы списываются ДО обращения к API, поэтому в минус баланс не уходит
    и параллельные вызовы не могут нарисовать больше, чем оплачено.
    Если API не отдал картинку — списание полностью откатывается.
    """

    def __init__(
        self,
        client: GigaChatClient,
        score_service: ScoreService,
        config: GenaiConfig,
        system_prompt: str,
        tx: ITransactionManager,
    ) -> None:
        self._client = client
        self._score = score_service
        self._config = config
        self._system_prompt = system_prompt
        self._tx = tx

    async def generate(
        self,
        user_id: int,
        chat_id: int,
        prompt: str,
        bot_id: int,
    ) -> GenAiResult:
        cost = self._config.cost

        spend = await self._score.spend_score(
            actor_id=user_id,
            target_id=user_id,
            chat_id=chat_id,
            cost=cost,
            emoji=SPECIAL_EMOJI["genai"],
            kind=LedgerKind.PURCHASE,
            bot_id=bot_id,
        )
        if not spend.success:
            return GenAiResult(
                success=False,
                cost=cost,
                current_balance=spend.current_balance,
                reason="not_enough",
            )

        # Генерация идёт до двух минут: фиксируем списание и отпускаем соединение.
        # Поэтому при сбое возврат — отдельная зафиксированная операция (refund).
        await self._tx.checkpoint()
        try:
            image = await self._client.generate_image(prompt, self._system_prompt)
        except GigaChatNoImage as e:
            logger.warning("genai: модель не вернула картинку: %s", e.text[:200])
            await self.refund(user_id, chat_id, bot_id, cost)
            return GenAiResult(success=False, cost=cost, reason="no_image")
        except GigaChatError:
            logger.exception("genai: ошибка GigaChat API")
            await self.refund(user_id, chat_id, bot_id, cost)
            return GenAiResult(success=False, cost=cost, reason="error")
        except Exception:
            logger.exception("genai: непредвиденная ошибка")
            await self.refund(user_id, chat_id, bot_id, cost)
            return GenAiResult(success=False, cost=cost, reason="error")

        logger.info("genai: chat=%d user=%d bytes=%d", chat_id, user_id, len(image))
        return GenAiResult(
            success=True,
            image=image,
            cost=cost,
            new_balance=spend.new_balance,
        )

    async def refund(self, user_id: int, chat_id: int, bot_id: int, cost: int = 0) -> None:
        """Откатывает списание: баллы обратно пользователю, у бота — назад.

        Публичный метод: хендлер вызывает его, если картинку не удалось
        отправить в чат — платить за то, чего никто не увидел, нечестно.
        """
        cost = cost or self._config.cost
        await self._score.add_score_quiet(
            user_id, chat_id, cost, kind=LedgerKind.REFUND, ref="genai", counterparty_id=bot_id
        )
        await self._score.add_score_quiet(
            bot_id, chat_id, -cost, kind=LedgerKind.REFUND, ref="genai", counterparty_id=user_id
        )
        # Списание уже зафиксировано — возврат тоже фиксируем сразу, чтобы его
        # не откатила ошибка дальше по хендлеру
        await self._tx.checkpoint()
