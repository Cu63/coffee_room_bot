"""Раунды с вариантами ответа: /quiz, /whosaid и ответ кнопкой."""

from bot.presentation.handlers.rounds.common import router as answer_router
from bot.presentation.handlers.rounds.quiz import router as quiz_router
from bot.presentation.handlers.rounds.whosaid import router as whosaid_router

__all__ = ["answer_router", "quiz_router", "whosaid_router"]
