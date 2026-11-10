"""Smoke-тесты: роутеры регистрируются, тексты из кода есть в messages.yaml."""

import ast
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


def _template_lookups(tree: ast.AST):
    """Обращения к шаблонам messages.yaml: (ключ, вызов .format(...) или None, строка).

    Шаблоны — это <...>._t, а в функции ещё и t/templates: параметр или имя, которому присвоено <...>._t
    (у /lot, /giveaway и других — «t = formatter._t»). Прочие t вроде переменной цикла не в счёт.
    """
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    names_in: dict[ast.AST, set[str]] = {}
    for func in ast.walk(tree):
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            names = {a.arg for a in func.args.args + func.args.kwonlyargs if a.arg in ("t", "templates")}
            for node in ast.walk(func):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) and node.value.attr == "_t":
                    names |= {target.id for target in node.targets if isinstance(target, ast.Name)}
            names_in[func] = names

    def scope_names(node: ast.AST) -> set[str]:
        while node in parents:
            node = parents[node]
            if node in names_in:
                return names_in[node]
        return set()

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            continue
        target = node.value
        is_template = (isinstance(target, ast.Attribute) and target.attr == "_t") or (
            isinstance(target, ast.Name) and target.id in scope_names(node)
        )
        if not is_template:
            continue
        call = None
        attr = parents.get(node)
        if isinstance(attr, ast.Attribute) and attr.attr == "format" and isinstance(parents.get(attr), ast.Call):
            call = parents[attr]
        yield node.slice.value, call, node.lineno


def _lookups_in_code():
    for path in BOT_DIR.rglob("*.py"):
        for key, call, line in _template_lookups(ast.parse(path.read_text(encoding="utf-8"))):
            yield f"{path.relative_to(BOT_DIR.parent)}:{line}", key, call


def test_message_keys_used_in_code_exist():
    messages = load_messages()
    missing = {key: where for where, key, _ in _lookups_in_code() if key not in messages}
    assert not missing, f"Ключи из кода отсутствуют в configs/messages.yaml: {missing}"


def test_message_placeholders_are_passed():
    # Плейсхолдер шаблона, который код не передаёт, — KeyError в момент, когда текст понадобится
    messages = load_messages()
    missing = []
    for where, key, call in _lookups_in_code():
        if call is None or key not in messages or call.args or any(kw.arg is None for kw in call.keywords):
            continue  # без .format или с *args/**kwargs — статически не проверить
        passed = {kw.arg for kw in call.keywords}
        needed = {
            field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(messages[key]) if field
        }
        if needed - passed:
            missing.append(f"{where} {key}: нет {sorted(needed - passed)}")
    assert not missing, "\n".join(missing)
