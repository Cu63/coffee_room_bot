"""/help: каждый раздел собирается без сырых плейсхолдеров и упоминает каждую команду из меню."""

from __future__ import annotations

import re

import pytest

from bot.domain.pluralizer import ScorePluralizer
from bot.infrastructure.config_loader import load_config, load_help_config, load_messages
from bot.infrastructure.message_formatter import MessageFormatter
from bot.presentation.handlers.help_renderer import HelpRenderer

CONFIG = load_config()
HELP = load_help_config()


def _render(section: str) -> str:
    score = CONFIG.score
    pluralizer = ScorePluralizer(singular=score.singular, plural_few=score.plural_few, plural_many=score.plural_many)
    return HelpRenderer(HELP).section_text(section, CONFIG, MessageFormatter(load_messages(), pluralizer))


@pytest.mark.parametrize("section", list(HELP["sections"]))
def test_section_has_no_raw_placeholders(section):
    text = _render(section)
    assert text, f"раздел {section} пустой"
    # Раньше раздел админов выводил «{warn_limit}» вместо числа
    assert not re.findall(r"\{[a-z_]+\}", text)


def test_help_mentions_every_menu_command():
    mentioned = set(re.findall(r"/([a-z_]+)", "\n".join(_render(section) for section in HELP["sections"])))
    missing = {command for items in HELP["bot_commands"].values() for command in items} - mentioned
    assert not missing, f"команды из меню, о которых молчит /help: {sorted(missing)}"
