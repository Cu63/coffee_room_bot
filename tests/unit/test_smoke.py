"""Smoke-тесты: роутеры регистрируются, тексты из кода есть в messages.yaml."""

import ast
import re
import string
from pathlib import Path

from aiogram import Dispatcher

from bot.infrastructure.bootstrap import register_routers
from bot.infrastructure.config_loader import BotSettings, load_config, load_messages

BOT_DIR = Path(__file__).resolve().parents[2] / "bot"


# Все ключи заданы — регистрируются и роутеры, которые зависят от .env
SETTINGS = BotSettings(bot_token="42:TEST", aitunnel_api_key="test", openai_api_key="test", gigachat_api_key="test")


def test_all_routers_register():
    dp = Dispatcher()
    register_routers(dp, load_config(), SETTINGS)
    assert dp.sub_routers


def test_routers_move_to_a_new_dispatcher():
    # Роутеры — синглтоны модулей. Тесты собирают Dispatcher на каждый тест в одном процессе:
    # раньше второй падал с «Router is already attached», и юнит-тесты нельзя было пускать до интеграционных
    first, second = Dispatcher(), Dispatcher()
    register_routers(first, load_config(), SETTINGS)
    register_routers(second, load_config(), SETTINGS)
    assert second.sub_routers
    assert not first.sub_routers


def test_message_keys_used_in_code_exist():
    messages = load_messages()
    pattern = re.compile(r"""_t\[\s*["']([A-Za-z0-9_]+)["']\s*\]""")
    used: dict[str, str] = {}
    for path in BOT_DIR.rglob("*.py"):
        for key in pattern.findall(path.read_text(encoding="utf-8")):
            used.setdefault(key, str(path.relative_to(BOT_DIR.parent)))
    missing = {key: where for key, where in used.items() if key not in messages}
    assert not missing, f"Ключи из кода отсутствуют в configs/messages.yaml: {missing}"


def _format_calls(tree: ast.AST):
    """Вызовы вида <...>["key"].format(...): ключ и имена аргументов (None — есть *args/**kwargs)."""
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and isinstance(node.func.value, ast.Subscript)
            and isinstance(node.func.value.slice, ast.Constant)
            and isinstance(node.func.value.slice.value, str)
        ):
            continue
        target = node.func.value.value
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
        if name not in ("_t", "t", "templates"):
            continue
        dynamic = bool(node.args) or any(kw.arg is None for kw in node.keywords)
        yield node.func.value.slice.value, None if dynamic else {kw.arg for kw in node.keywords}, node.lineno


def test_message_placeholders_are_passed():
    # Плейсхолдер шаблона, который код не передаёт, — KeyError в момент, когда текст понадобится
    messages = load_messages()
    missing = []
    for path in BOT_DIR.rglob("*.py"):
        for key, passed, line in _format_calls(ast.parse(path.read_text(encoding="utf-8"))):
            if passed is None or key not in messages:
                continue
            needed = {
                field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(messages[key]) if field
            }
            if needed - passed:
                missing.append(f"{path.relative_to(BOT_DIR.parent)}:{line} {key}: нет {sorted(needed - passed)}")
    assert not missing, "\n".join(missing)
