"""configs/config.yaml: валиден целиком, а опечатка в ключе не проходит молча."""

import re

import pytest
import yaml
from pydantic import ValidationError

from bot.infrastructure.config_loader import _CONFIGS_DIR, AppConfig, load_config


def test_repo_config_loads():
    load_config()


@pytest.mark.parametrize(
    "path",
    [("lott",), ("lot", "min_start_prise"), ("tax", "brackets", 0, "procent")],
    ids=["section", "key", "list-item"],
)
def test_unknown_key_is_an_error(path):
    with open(_CONFIGS_DIR / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    *parents, key = path
    node = raw
    for part in parents:
        node = node[part]
    node[key] = 1
    # Раньше лишние ключи отбрасывались, и параметр с опечаткой тихо брал значение по умолчанию
    with pytest.raises(ValidationError, match=re.escape(".".join(map(str, path)))):
        AppConfig.model_validate(raw)
