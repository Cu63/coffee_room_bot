"""Smoke-тесты: роутеры регистрируются, тексты из кода есть в messages.yaml."""

import re
from pathlib import Path

from aiogram import Dispatcher

from bot.infrastructure.bootstrap import register_routers
from bot.infrastructure.config_loader import BotSettings, load_config, load_messages

BOT_DIR = Path(__file__).resolve().parents[2] / "bot"


def test_all_routers_register():
    # Все ключи заданы — регистрируются и роутеры, которые зависят от .env
    settings = BotSettings(
        bot_token="42:TEST",
        aitunnel_api_key="test",
        openai_api_key="test",
        gigachat_api_key="test",
    )
    dp = Dispatcher()
    register_routers(dp, load_config(), settings)
    assert dp.sub_routers


def test_message_keys_used_in_code_exist():
    messages = load_messages()
    pattern = re.compile(r"""_t\[\s*["']([A-Za-z0-9_]+)["']\s*\]""")
    used: dict[str, str] = {}
    for path in BOT_DIR.rglob("*.py"):
        for key in pattern.findall(path.read_text(encoding="utf-8")):
            used.setdefault(key, str(path.relative_to(BOT_DIR.parent)))
    missing = {key: where for key, where in used.items() if key not in messages}
    assert not missing, f"Ключи из кода отсутствуют в configs/messages.yaml: {missing}"
