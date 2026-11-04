"""Меню команд Telegram (help.yaml → bot_commands): формат по правилам Telegram и только существующие команды."""

from __future__ import annotations

import pathlib
import re

import pytest

from bot.infrastructure.config_loader import load_help_config

MENU = load_help_config()["bot_commands"]
SECTIONS = ("members", "admins", "private")
_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _handled_commands() -> set[str]:
    """Все команды из Command(...) в хендлерах."""
    source = "\n".join(path.read_text(encoding="utf-8") for path in (_ROOT / "bot" / "presentation").rglob("*.py"))
    return {name for args in re.findall(r"Command\(([^)]*)\)", source) for name in re.findall(r'"([a-z0-9_]+)"', args)}


def test_menu_has_all_sections():
    assert set(MENU) == set(SECTIONS)


@pytest.mark.parametrize("section", SECTIONS)
def test_menu_entries_follow_telegram_rules(section):
    for command, description in MENU[section].items():
        assert re.fullmatch(r"[a-z0-9_]{1,32}", command), command
        assert 1 <= len(description) <= 256, command


def test_menu_fits_telegram_limit():
    # Админы видят команды участников и свои; в одной области Telegram принимает до 100 команд
    assert len(MENU["members"]) + len(MENU["admins"]) <= 100
    assert not set(MENU["members"]) & set(MENU["admins"])


@pytest.mark.parametrize("section", SECTIONS)
def test_menu_lists_only_existing_commands(section):
    missing = set(MENU[section]) - _handled_commands()
    assert not missing, f"в меню команды, на которые бот не отвечает: {missing}"
