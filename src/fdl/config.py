"""The user's settings: who makes the versions they commit.

Kept in ``$XDG_CONFIG_HOME/fdl/config.json`` (``~/.config/fdl/config.json``
by default), apart from any repository, as git keeps ``user.name``.
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
        return json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def get(key: str) -> str | None:
    _check(key)
    return os.environ.get(KEYS[key]) or read().get(key)


def set_value(key: str, value: str) -> None:
    _check(key)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({**read(), key: value}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def author(given: str | None) -> dict:
    """The author of a version: ``given`` (from --author), else the settings."""
    if given:
        return {"name": given}
    name, email = get("author.name"), get("author.email")
    if not name:
        raise ConfigError("who makes the version is not set: pass --author, or run fdl config author.name <name>")
    return {"name": name, **({"email": email} if email else {})}


def _check(key: str) -> None:
    if key not in KEYS:
        raise ConfigError(f"no setting {key}: there are {', '.join(KEYS)}")
