"""Вспомогательные функции для игры «Угадайка»."""

from __future__ import annotations

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain.pluralizer import ScorePluralizer
from bot.domain.tz import TZ_MSK
from bot.domain.wordgame_entities import WordGame


def open_dm_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✏️ Написать слово в ЛС",
                url=f"https://t.me/{bot_username}",
            )
        ]]
    )


def game_text(game: WordGame, pluralizer: ScorePluralizer) -> str:
    ends_dt = datetime.fromtimestamp(game.ends_at, tz=TZ_MSK)
    ends_str = ends_dt.strftime("%H:%M")
    bet_str = pluralizer.pluralize(game.bet)
    return (
        f"🔤 <b>Угадайка!</b>\n\n"
        f"Слово из <b>{len(game.word)}</b> букв: {game.masked}\n"
        f"Открыто: <b>{game.revealed_count}/{len(game.word)}</b>\n\n"
        f"💰 Ставка: <b>{game.bet} {bet_str}</b>  ⏰ До: <b>{ends_str}</b>\n\n"
        f"<i>Ответь на это сообщение словом, чтобы угадать</i>"
    )


def raw_to_game(raw: dict) -> WordGame:
    return WordGame(
        game_id=raw["game_id"],
        chat_id=raw["chat_id"],
        creator_id=raw["creator_id"],
        word=raw["word"],
        bet=raw["bet"],
        ends_at=raw["ends_at"],
        revealed=raw.get("revealed", []),
        guesses=raw.get("guesses", []),
        message_id=raw.get("message_id", 0),
        finished=raw.get("finished", False),
        winner_id=raw.get("winner_id"),
        is_random=raw.get("is_random", False),
    )


def game_to_raw(game: WordGame) -> dict:
    return {
        "game_id": game.game_id,
        "chat_id": game.chat_id,
        "creator_id": game.creator_id,
        "word": game.word,
        "bet": game.bet,
        "ends_at": game.ends_at,
        "revealed": game.revealed,
        "guesses": game.guesses,
        "message_id": game.message_id,
        "finished": game.finished,
        "winner_id": game.winner_id,
        "is_random": game.is_random,
    }
