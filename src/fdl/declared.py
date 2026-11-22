"""What a version declares of its tables and views beyond what DuckLake records:
primary keys, asset columns, which get no profile, and the other datasets its
views read (see the specification's `tables.json`).

A version's `tables.json` holds them, and a commit takes them from its parent:
`fdl clone` keeps them beside the working metadata, `fdl declare` changes them,
and `fdl commit` checks them against the tables and views and writes them into
the new version's `tables.json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DeclarationError(ValueError):
    """A declaration the tables and views refuse."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Facts:
    """The declarations, by table or view ``schema.name``, and the dependencies by name."""

    primary_keys: dict[str, list[str]] = field(default_factory=dict)
    assets: dict[str, list[str]] = field(default_factory=dict)
    #: Each dataset the views read: ``{"url": ..., "version": ...}``, ``version`` optional.
    dependencies: dict[str, dict[str, str]] = field(default_factory=dict)
    unprofiled: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.primary_keys or self.assets or self.dependencies or self.unprofiled)

    def union(self, other: Facts) -> Facts:
        """These, with what ``other`` says where these say nothing."""
        return Facts(
            primary_keys={**other.primary_keys, **self.primary_keys},
            assets={**other.assets, **self.assets},
            dependencies={**other.dependencies, **self.dependencies},
            unprofiled=self.unprofiled | other.unprofiled,
        )

    def only(self, names: set[str]) -> Facts:
        """What these say of the tables and views ``names`` (and every dependency)."""
        return Facts(
            primary_keys={k: v for k, v in self.primary_keys.items() if k in names},
            assets={k: v for k, v in self.assets.items() if k in names},
            dependencies=dict(self.dependencies),
            unprofiled=frozenset(self.unprofiled & names),
        )

    def of(self, name: str) -> dict[str, Any]:
        """What these declare of one table or view, as `tables.json` writes it."""
        return {
            **({"primary_key": self.primary_keys[name]} if name in self.primary_keys else {}),
            **({"assets": self.assets[name]} if name in self.assets else {}),
            **({"profile": False} if name in self.unprofiled else {}),
        }

    def to_json(self) -> dict[str, Any]:
        """As the state beside a working metadata keeps them."""
        return {
            "primary_keys": self.primary_keys,
            "assets": self.assets,
            "dependencies": self.dependencies,
            "unprofiled": sorted(self.unprofiled),
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> Facts:
        return cls(
            primary_keys={k: list(v) for k, v in value.get("primary_keys", {}).items()},
            assets={k: list(v) for k, v in value.get("assets", {}).items()},
            dependencies={k: dict(v) for k, v in value.get("dependencies", {}).items()},
            unprofiled=frozenset(value.get("unprofiled", [])),
        )


def _names(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)


def from_index(index: dict | None) -> Facts:
    """What a `tables.json` declares."""
    index = index or {}
    tables = index.get("tables", {})
    views = index.get("views", {})
    dependencies = index.get("dependencies", {})
    return Facts(
        primary_keys={n: list(t["primary_key"]) for n, t in tables.items() if _names(t.get("primary_key"))},
        assets={n: list(t["assets"]) for n, t in tables.items() if _names(t.get("assets"))},
        dependencies={
            k: {key: v[key] for key in ("url", "version") if isinstance(v.get(key), str)}
            for k, v in dependencies.items()
            if isinstance(v, dict) and isinstance(v.get("url"), str)
        },
        unprofiled=frozenset(n for n, t in {**tables, **views}.items() if isinstance(t, dict) and t.get("profile") is False),
    )


def changed(old: Facts, new: Facts, names: set[str]) -> list[str]:
    """Of the tables and views ``names``, those whose declarations differ (the
    ``declarations`` of a version's diff)."""
    return sorted(name for name in names if old.of(name) != new.of(name))


def merged(base: Facts, ours: Facts, theirs: Facts) -> tuple[Facts, list[str]]:
    """Ours and theirs merged three-way against the base: what one side changed
    takes that side's change, and what both changed differently is a conflict.
    Returns the merged declarations and the conflicts."""
    conflicts: list[str] = []

    def pick(what: str, name: str, old: Any, mine: Any, other: Any) -> Any:
        if mine == other or other == old:
            return mine
        if mine == old:
            return other
        conflicts.append(f"{name}: both sides changed {what} differently")
        return mine

    def each(what: str, old: dict, mine: dict, other: dict) -> dict:
        return {
            name: value
            for name in sorted(set(old) | set(mine) | set(other))
            if (value := pick(what, name, old.get(name), mine.get(name), other.get(name)))
        }

    def as_sets(found: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
        return {name: tuple(sorted(columns)) for name, columns in found.items()}

    assets = each("the asset columns", as_sets(base.assets), as_sets(ours.assets), as_sets(theirs.assets))
    unprofiled = frozenset(
        name
        for name in base.unprofiled | ours.unprofiled | theirs.unprofiled
        if pick("whether it gets a profile", name, name in base.unprofiled, name in ours.unprofiled, name in theirs.unprofiled)
    )
    return (
        Facts(
            primary_keys={k: list(v) for k, v in each("the primary key", base.primary_keys, ours.primary_keys, theirs.primary_keys).items()},
            assets={k: list(v) for k, v in assets.items()},
            dependencies=each("the dependency", base.dependencies, ours.dependencies, theirs.dependencies),
            unprofiled=unprofiled,
        ),
        conflicts,
    )


# -- checking against the tables and views ----------------------------------------------


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@dataclass
class Objects:
    """The tables of a metadata, ``schema.name`` to each column's type, and its views."""

    tables: dict[str, dict[str, str]]
    views: set[str]


def objects(con, alias: str = "lake") -> Objects:
    rows = con.execute(
        """
        SELECT t.schema_name, t.table_name, c.column_name, c.data_type
        FROM duckdb_tables() t
        LEFT JOIN duckdb_columns() c
          ON c.database_name = t.database_name AND c.schema_name = t.schema_name AND c.table_name = t.table_name
        WHERE t.database_name = ?
        ORDER BY t.schema_name, t.table_name, c.column_index
        """,
        [alias],
    ).fetchall()
    tables: dict[str, dict[str, str]] = {}
    for schema, table, column, kind in rows:
        columns = tables.setdefault(f"{schema}.{table}", {})
        if column is not None:
            columns[column] = kind
    views = {
        f"{schema}.{name}"
        for schema, name in con.execute(
            "SELECT schema_name, view_name FROM duckdb_views() WHERE database_name = ? AND NOT internal", [alias]
        ).fetchall()
    }
    return Objects(tables=tables, views=views)


def check(facts: Facts, found: Objects) -> list[str]:
    """What the tables and views refuse in ``facts``, as messages. Empty when none."""
    problems: list[str] = []
    for name, key in sorted(facts.primary_keys.items()):
        if name in found.views:
            problems.append(f"{name} is a view, which has no primary key")
            continue
        if name not in found.tables:
            problems.append(f"{name}: no such table")
            continue
        if len(set(key)) != len(key):
            problems.append(f"{name}: the primary key names a column twice")
        missing = [c for c in key if c not in found.tables[name]]
        if missing:
            problems.append(f"{name}: the primary key names columns it lacks: {', '.join(missing)}")
    for name, columns in sorted(facts.assets.items()):
        if name in found.views:
            problems.append(f"{name} is a view, which has no asset columns")
            continue
        if name not in found.tables:
            problems.append(f"{name}: no such table")
            continue
        for column in columns:
            kind = found.tables[name].get(column)
            if kind is None:
                problems.append(f"{name}: no column {column}")
            elif kind.upper() != "VARCHAR":
                problems.append(f"{name}.{column} is an asset column, so its type has to be VARCHAR (a sha256 in hex)")
    for name in sorted(facts.unprofiled):
        if name not in found.tables and name not in found.views:
            problems.append(f"{name}: no such table or view")
    from .version import is_id

    for name, entry in sorted(facts.dependencies.items()):
        url = entry.get("url") or ""
        if "://" not in url or not url.endswith("/"):
            problems.append(f"the dependency {name}: url has to be an absolute URL ending in /, not {url!r}")
        if "version" in entry and not is_id(entry["version"]):
            problems.append(f"the dependency {name}: {entry['version']!r} is not a version ID")
    return problems


def renamed(facts: Facts, renames: dict[str, dict[str, str]], found: Objects) -> Facts:
    """``facts`` with each column a table no longer has under the name declared, but has
    under a new one (``renames``: by table, an earlier name to the name now), renamed."""

    def follow(name: str, columns: list[str]) -> list[str]:
        held = found.tables.get(name, {})
        moved = renames.get(name, {})
        return [moved.get(c, c) if c not in held else c for c in columns]

    return Facts(
        primary_keys={n: follow(n, c) for n, c in facts.primary_keys.items()},
        assets={n: follow(n, c) for n, c in facts.assets.items()},
        dependencies=facts.dependencies,
        unprofiled=facts.unprofiled,
    )


def _renames(con) -> dict[str, dict[str, str]]:
    """By table, each earlier name of a top-level column to its name now: the same
    column ID in the same table, as DuckLake keeps it across a rename."""
    meta = "__ducklake_metadata_lake.main"
    found = con.execute(
        f"""
        SELECT DISTINCT s.schema_name || '.' || t.table_name, old.column_name, now.column_name
        FROM {meta}.ducklake_table t
        JOIN {meta}.ducklake_schema s USING (schema_id)
        JOIN {meta}.ducklake_column now ON now.table_id = t.table_id
        JOIN {meta}.ducklake_column old ON old.table_id = t.table_id AND old.column_id = now.column_id
        WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL AND now.end_snapshot IS NULL
          AND now.parent_column IS NULL AND old.column_name <> now.column_name
        """
    ).fetchall()
    renames: dict[str, dict[str, str]] = {}
    for table, old, now in found:
        renames.setdefault(table, {})[old] = now
    return renames


# -- what a commit reads from the working metadata --------------------------------------


@dataclass
class Described:
    """What a commit records of a working metadata: the declarations of the tables and
    views it holds, and each view's SQL and, when it could be bound, its columns."""

    facts: Facts
    views: dict[str, dict[str, Any]]
    #: The tables and views it holds, ``schema.name``.
    names: set[str] = field(default_factory=set)


def _attach(working, data_path: str, setup=()):
    import duckdb

    from . import catalog

    con = duckdb.connect()
    try:
        for statement in [*setup, *catalog.attach_sql(working, alias="lake", data_path=data_path, read_only=True)]:
            if statement:
                con.execute(statement)
    except BaseException:
        con.close()
        raise
    return con


def found_in(working, *, data_path: str, setup=()) -> Objects:
    """The tables and views of a working metadata."""
    con = _attach(working, data_path, setup)
    try:
        return objects(con)
    finally:
        con.close()


def describe(working, facts: Facts, *, data_path: str, setup=()) -> Described:
    """Check ``facts`` against the working metadata's tables and views, and bind its views.

    Declarations of tables and views no longer there are left out: a table dropped
    takes its declarations with it. A column renamed since it was declared is
    declared under its new name. Raises `DeclarationError` for what the rest refuses.
    """
    import duckdb

    con = _attach(working, data_path, setup)
    try:
        found = objects(con)
        kept = renamed(facts.only(set(found.tables) | found.views), _renames(con), found)
        problems = check(kept, found)
        if problems:
            raise DeclarationError(problems)
        views = _views(con)
        for name, entry in kept.dependencies.items():
            _attach_dependency(con, name, entry)
        con.execute("USE lake")
        for key, view in views.items():
            schema, name = key.split(".", 1)
            try:
                described = con.execute(f"DESCRIBE SELECT * FROM lake.{_q(schema)}.{_q(name)}").fetchall()
            except duckdb.Error:
                continue
            view["columns"] = [{"name": row[0], "type": row[1]} for row in described]
        return Described(facts=kept, views=views, names=set(found.tables) | found.views)
    finally:
        con.close()


def _views(con) -> dict[str, dict[str, Any]]:
    """Each live view and its SQL, without the placeholder for the metadata's own name."""
    meta = "__ducklake_metadata_lake.main"
    found = con.execute(
        f"""
        SELECT s.schema_name, v.view_name, v.sql
        FROM {meta}.ducklake_view v JOIN {meta}.ducklake_schema s USING (schema_id)
        WHERE v.end_snapshot IS NULL AND s.end_snapshot IS NULL
        ORDER BY 1, 2
        """
    ).fetchall()
    # DuckLake writes the catalog's own name as a placeholder; tables.json leaves it out.
    return {f"{schema}.{name}": {"sql": sql.replace("{DUCKLAKE_CATALOG}.", "")} for schema, name, sql in found}


def _attach_dependency(con, name: str, entry: dict[str, str]) -> bool:
    """Attach the version of another dataset its entry names (else what its main holds)
    under ``name``. False when it cannot be read."""
    from . import layout, repo
    from .stores import open_store

    try:
        store = open_store(entry["url"])
        version = (
            repo.read_version(store, entry["version"]) if entry.get("version") else repo.readable(store, "refs/heads/main")
        )
        where = store.url.rstrip("/") + "/" + layout.version_metadata(version)
        con.execute(
            f"ATTACH 'ducklake:{where.replace(chr(39), chr(39) * 2)}' AS {_q(name)} "
            f"(DATA_PATH {_lit(repo.data_url(store))}, OVERRIDE_DATA_PATH true, READ_ONLY)"
        )
        return True
    except Exception:  # noqa: BLE001 - a view that cannot be bound gets no columns
        return False
