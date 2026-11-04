"""E2E: меню команд Telegram ставится при старте и содержит только команды, на которые бот отвечает."""

from __future__ import annotations

import logging

from aiogram.methods import SetMyCommands

from bot.infrastructure.bootstrap import registered_commands, setup_bot_commands
from bot.infrastructure.config_loader import load_help_config


def menus(tg) -> dict[str, list[str]]:
    return {type(r.scope).__name__: [c.command for c in r.commands] for r in tg.requests(SetMyCommands)}


async def test_menu_is_set_for_members_admins_and_private_chats(tg, caplog):
    menu = load_help_config()["bot_commands"]

    with caplog.at_level(logging.WARNING, logger="bot.infrastructure.bootstrap"):
        await setup_bot_commands(tg.bot, tg.dp, menu)

    # /genai регистрируется только с GIGACHAT_API_KEY — в тестах его нет, остальные команды меню на месте
    assert set(menu["members"]) - registered_commands(tg.dp) == {"genai"}
    members = [command for command in menu["members"] if command != "genai"]
    assert menus(tg) == {
        "BotCommandScopeAllGroupChats": members,
        "BotCommandScopeAllChatAdministrators": members + list(menu["admins"]),
        "BotCommandScopeAllPrivateChats": list(menu["private"]),
    }
    assert "genai" in caplog.text


async def test_menu_skips_commands_nobody_answers(tg, caplog):
    menu = {"members": {"score": "Мой счёт", "nosuchcommand": "Опечатка"}, "admins": {}, "private": {}}

    with caplog.at_level(logging.WARNING, logger="bot.infrastructure.bootstrap"):
        await setup_bot_commands(tg.bot, tg.dp, menu)

    assert menus(tg)["BotCommandScopeAllGroupChats"] == ["score"]
    assert "nosuchcommand" in caplog.text
