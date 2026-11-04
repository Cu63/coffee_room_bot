"""QuizService — вопрос викторины от LLM: четыре варианта, сложность — по IQ вызвавшего."""

from __future__ import annotations

import html
import json
import logging
import random

from bot.application.interfaces.llm_repository import ILlmRepository
from bot.application.interfaces.transaction_manager import ITransactionManager
from bot.domain.entities import RoundQuestion
from bot.infrastructure.config_loader import QuizConfig
from bot.infrastructure.message_formatter import MessageFormatter
from bot.infrastructure.openai_client import OpenAiClient

logger = logging.getLogger(__name__)

COMMAND = "quiz"
OPTIONS = 4
_MAX_QUESTION = 300
_MAX_OPTION = 60


def quiz_level(iq: int) -> str:
    """Сложность вопроса по IQ: ключ quiz_level_<уровень> в messages.yaml."""
    if iq < 80:
        return "easy"
    if iq < 140:
        return "medium"
    return "hard"


def parse_quiz(raw: str, rng: random.Random | None = None) -> RoundQuestion:
    """Разобрать JSON модели и перемешать варианты (модели любят ставить верный первым).

    Raises ValueError, если ответ не по формату.
    """
    try:
        data = json.loads(raw)
        question = str(data["question"]).strip()
        options = [str(option).strip() for option in data["options"]]
        answer = int(data["answer"])
        explanation = str(data.get("explanation") or "").strip()
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"викторина: ответ модели не по формату: {raw[:200]!r}") from e
    if not question or len(question) > _MAX_QUESTION:
        raise ValueError(f"викторина: плохой вопрос {question!r}")
    if len(options) != OPTIONS or not all(options) or any(len(option) > _MAX_OPTION for option in options):
        raise ValueError(f"викторина: плохие варианты {options!r}")
    if len(set(options)) != OPTIONS or not 0 <= answer < OPTIONS:
        raise ValueError(f"викторина: варианты повторяются или нет верного: {options!r}, {answer}")
    correct = options[answer]
    (rng or random).shuffle(options)
    return RoundQuestion(
        text=html.escape(question),
        options=tuple(options),
        correct=options.index(correct),
        explanation=html.escape(explanation),
    )


class QuizService:
    def __init__(
        self,
        client: OpenAiClient,
        llm_repo: ILlmRepository,
        config: QuizConfig,
        formatter: MessageFormatter,
        tx: ITransactionManager,
    ) -> None:
        self._client = client
        self._llm_repo = llm_repo
        self._config = config
        self._fmt = formatter
        self._tx = tx

    async def generate(self, chat_id: int, user_id: int, iq: int, topic: str) -> RoundQuestion:
        """Вопрос от LLM. Raises ValueError, если модель ответила не по формату."""
        prompt = self._fmt._t["quiz_user_prompt"].format(
            topic=topic or self._fmt._t["quiz_topic_any"],
            level=self._fmt._t[f"quiz_level_{quiz_level(iq)}"],
        )
        await self._tx.checkpoint()  # не держим соединение из пула, пока ждём LLM
        resp = await self._client.chat(
            [
                {"role": "system", "content": self._fmt._t["quiz_system_prompt"]},
                {"role": "user", "content": prompt},
            ],
            json_mode=True,
            model=self._config.model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
        )
        await self._llm_repo.log_request(
            user_id=user_id,
            chat_id=chat_id,
            command=COMMAND,
            query=f"iq={iq} topic={topic[:100]}",
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        return parse_quiz(resp.text)
