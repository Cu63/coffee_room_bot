"""Админы бота из admin.users: по числовому id или по username."""

import pytest

from bot.domain.bot_utils import is_admin, is_admin_account
from bot.domain.entities import User
from bot.infrastructure.config_loader import AdminConfig

ADMINS = AdminConfig.model_validate({"users": [123456, "@Coffee_Admin", " 777 "]}).users


def test_config_keeps_ids_as_strings_and_normalizes_usernames():
    assert ADMINS == ["123456", "coffee_admin", "777"]


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (User(id=123456, username="renamed", full_name="Сменил имя"), True),
        (User(id=123456, username=None, full_name="Без username"), True),
        (User(id=1, username="COFFEE_ADMIN", full_name="По username"), True),
        (User(id=1, username="someone", full_name="Не админ"), False),
        (User(id=1, username=None, full_name="Не админ без username"), False),
        (None, False),
    ],
    ids=["id-after-rename", "id-without-username", "username-any-case", "stranger", "stranger-no-username", "nobody"],
)
def test_is_admin(user, expected):
    assert is_admin(user, ADMINS) is expected


def test_is_admin_account_matches_like_is_admin():
    assert is_admin_account(777, None, ADMINS)
    assert is_admin_account(5, "Coffee_Admin", ADMINS)
    assert not is_admin_account(5, "", ADMINS)
