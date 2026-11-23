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

#: The ``[author]`` table and its lines, up to the next table. The header may
#: be spaced or quoted (``[ author ]``, ``["author"]``), as TOML allows.
_AUTHOR_TABLE = re.compile(
    r"""^[ \t]*\[[ \t]*(?:author|"author"|'author')[ \t]*\][ \t]*(?:#.*)?(?:\n|$)"""
    r"(?:(?![ \t]*\[).*(?:\n|$))*",
    re.MULTILINE,
)


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
    found = settings.get(table)
    value = found.get(name) if isinstance(found, dict) else None
    return value if isinstance(value, str) else None


def get(key: str) -> str | None:
    _check(key)
    return os.environ.get(KEYS[key]) or _value(read(), key)


def set_value(key: str, value: str) -> None:
    """Write ``key`` into the ``[author]`` table, leaving the rest of the file as it is.

    The table is written where it was, or appended when there was none: put
    first, it would take in the keys the file sets before its first table.
    """
    _check(key)
    text = _text()
    author = {name: _value(_parse(text), f"author.{name}") for name in ("name", "email")}
    author[key.split(".")[1]] = value
    found = _AUTHOR_TABLE.search(text)
    before, after = (text[: found.start()], text[found.end() :]) if found else (text, "")
    if "author" in _parse(before + after):
        raise ConfigError(f"{path()} sets author outside an [author] table: edit it by hand")
    # Comments and blank lines in the table stay; they often belong to the next one.
    kept = [line for line in found.group().splitlines()[1:] if not line.strip() or line.lstrip().startswith("#")] if found else []
    # A TOML basic string escapes as JSON does.
    lines = ["[author]", *(f"{name} = {json.dumps(v, ensure_ascii=False)}" for name, v in author.items() if v), *kept]
    table = "\n".join(lines) + "\n"
    if not found and before.strip():
        before = before.rstrip("\n") + "\n\n"
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(before + table + after, encoding="utf-8")


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
