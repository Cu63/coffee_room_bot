"""Разбор вопроса викторины от модели, уровни сложности и подписи кнопок /whosaid."""

from __future__ import annotations

import json
import random

import pytest

from bot.application.quiz_service import parse_quiz, quiz_level
from bot.application.whosaid_service import _names
from bot.domain.entities import User


def raw(**overrides) -> str:
    data = {
        "question": "Столица <Франции>?",
        "options": ["Париж", "Лион", "Ницца", "Марсель"],
        "answer": 0,
        "explanation": "Париж & только Париж",
    }
    return json.dumps(data | overrides, ensure_ascii=False)


@pytest.mark.parametrize("seed", range(5))
def test_shuffled_options_keep_the_right_answer(seed):
    question = parse_quiz(raw(), random.Random(seed))
    assert question.options[question.correct] == "Париж"
    assert sorted(question.options) == sorted(["Париж", "Лион", "Ницца", "Марсель"])
    assert question.text == "Столица &lt;Франции&gt;?"  # текст модели — только текст
    assert question.explanation == "Париж &amp; только Париж"


@pytest.mark.parametrize(
    "bad",
    [
        "не json",
        raw(options=["a", "b", "c"]),
        raw(options=["a", "a", "b", "c"]),
        raw(answer=4),
        raw(answer="первый"),
        raw(question=""),
        raw(options=["a" * 61, "b", "c", "d"]),
        json.dumps({"question": "?", "answer": 0}),
    ],
    ids=[
        "not-json",
        "three-options",
        "duplicates",
        "answer-out-of-range",
        "answer-not-int",
        "empty",
        "long",
        "no-options",
    ],
)
def test_bad_model_answers_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_quiz(bad)


@pytest.mark.parametrize(("iq", "level"), [(0, "easy"), (79, "easy"), (80, "medium"), (139, "medium"), (140, "hard")])
def test_level_follows_iq(iq, level):
    assert quiz_level(iq) == level


def test_whosaid_names_are_distinct():
    people = [User(1, "kot", "Кот"), User(2, None, "Иван"), User(3, None, "Иван"), User(4, None, "")]
    assert _names(people) == ["@kot", "Иван", "Иван (2)", "4"]
