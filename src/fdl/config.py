"""The user's settings: who makes the versions they commit.

Kept in ``$XDG_CONFIG_HOME/fdl/config.json`` (``~/.config/fdl/config.json``
by default), apart from any dataset, as git keeps ``user.name``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: The settings there are, each with the environment variable that overrides it.
KEYS = {"author.name": "FDL_AUTHOR_NAME", "author.email": "FDL_AUTHOR_EMAIL"}


class ConfigError(Exception):
    pass


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "fdl" / "config.json"


def read() -> dict[str, str]:
    try:
        settings = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise ConfigError(f"{path()} is not JSON: {exc}") from exc
    if not isinstance(settings, dict):
        raise ConfigError(f"{path()} does not hold a JSON object")
    return settings


def get(key: str) -> str | None:
    _check(key)
    return os.environ.get(KEYS[key]) or read().get(key)


def set_value(key: str, value: str) -> None:
    _check(key)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**read(), key: value}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def author(given: str | None) -> dict:
    """The author of a version: ``given`` (from --author), else the environment, else the settings.

    The name and the email come from the same place, so a name set in the
    environment is never paired with the email of whoever wrote the settings.
    """
    if given:
        return {"name": given}
    if os.environ.get(KEYS["author.name"]):
        name, email = os.environ[KEYS["author.name"]], os.environ.get(KEYS["author.email"])
    else:
        settings = read()
        name, email = settings.get("author.name"), settings.get("author.email")
    if not name:
        raise ConfigError("who makes the version is not set: pass --author, or run fdl config author.name <name>")
    return {"name": name, **({"email": email} if email else {})}


def _check(key: str) -> None:
    if key not in KEYS:
        raise ConfigError(f"no setting {key}: there are {', '.join(KEYS)}")
