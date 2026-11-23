"""The user's settings, apart from any dataset, as git keeps ``~/.gitconfig``.

One TOML file: ``FDL_CONFIG``, else ``$XDG_CONFIG_HOME/fdl/config.toml``
(``~/.config/fdl/config.toml`` by default). ``[author]`` says who makes the
versions they commit; ``[credential."<URL>"]`` names the helper that gives a
token for a dataset (see `fdl.stores.credentials_for`).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

#: The settings `fdl config` sets, each with the environment variable that overrides it.
KEYS = {"author.name": "FDL_AUTHOR_NAME", "author.email": "FDL_AUTHOR_EMAIL"}

#: The ``[author]`` table and its lines, up to the next table.
_AUTHOR_TABLE = re.compile(r"^\[author\][ \t]*(?:#.*)?\n(?:(?!\[).*(?:\n|$))*", re.MULTILINE)


class ConfigError(Exception):
    pass


def path() -> Path:
    if os.environ.get("FDL_CONFIG"):
        return Path(os.environ["FDL_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "fdl" / "config.toml"


def _text() -> str:
    try:
        return path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _parse(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path()} is not TOML: {exc}") from exc


def read() -> dict:
    """Every setting in the file, as TOML reads it."""
    return _parse(_text())


def _value(settings: dict, key: str) -> str | None:
    table, name = key.split(".")
    value = (settings.get(table) or {}).get(name)
    return value if isinstance(value, str) else None


def get(key: str) -> str | None:
    _check(key)
    return os.environ.get(KEYS[key]) or _value(read(), key)


def set_value(key: str, value: str) -> None:
    """Write ``key`` into the ``[author]`` table, leaving the rest of the file as it is."""
    _check(key)
    text = _text()
    author = {name: _value(_parse(text), f"author.{name}") for name in ("name", "email")}
    author[key.split(".")[1]] = value
    rest = _AUTHOR_TABLE.sub("", text)
    if "author" in _parse(rest):
        raise ConfigError(f"{path()} sets author outside an [author] table: edit it by hand")
    # A TOML basic string escapes as JSON does.
    table = "[author]\n" + "".join(f"{name} = {json.dumps(v, ensure_ascii=False)}\n" for name, v in author.items() if v)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(table + ("\n" + rest.lstrip("\n") if rest.strip() else ""), encoding="utf-8")


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
        name, email = _value(settings, "author.name"), _value(settings, "author.email")
    if not name:
        raise ConfigError("who makes the version is not set: pass --author, or run fdl config author.name <name>")
    return {"name": name, **({"email": email} if email else {})}


def _check(key: str) -> None:
    if key not in KEYS:
        raise ConfigError(f"no setting {key}: there are {', '.join(KEYS)}")
