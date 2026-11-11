"""Merging: the three-way comparison of tables and descriptions.

Ours is the version the working catalog was checked out at, theirs the version
being merged, and the base their common ancestor (see `repo.merge_bases`). A
table, a row or a value one side changed takes that side's change; one both
sides changed differently is a conflict. Rows are matched only by a primary
key that both sides declare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .catalog import CatalogError, attach_sql, working_data_path

#: A value one version does not hold.
MISSING: Any = type("Missing", (), {"__repr__": lambda self: "MISSING"})()

#: Lists whose entries are matched by a key rather than taken whole.
KEYED_LISTS: dict[str, Callable[[dict], Any]] = {
    "tables": lambda entry: (entry.get("schema"), entry.get("name")),
    "fields": lambda entry: entry.get("name"),
    "entries": lambda entry: entry.get("path"),
}

class Conflicts(Exception):
    """The two sides changed the same thing differently, so no version is made."""

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__(f"{len(conflicts)} conflict(s)")
        self.conflicts = conflicts


#: How many keys of a conflicting table are listed.
LISTED_KEYS = 10


# -- descriptions ----------------------------------------------------------------


def values(base: Any, ours: Any, theirs: Any, where: str = "") -> tuple[Any, list[str]]:
    """Merge one value of `dataset.json` or `tree.json`: the result and the conflicts.

    Objects are merged member by member, lists in `KEYED_LISTS` entry by
    entry, and anything else is taken whole. `MISSING` stands for a value a
    version does not hold, and comes back when the merge drops it.
    """
    if ours == theirs:
        return ours, []
    if ours == base:
        return theirs, []
    if theirs == base:
        return ours, []
    name = where.rsplit(".", 1)[-1]
    if _all(dict, base, ours, theirs):
        return _objects(base, ours, theirs, where)
    if name in KEYED_LISTS and _all(list, base, ours, theirs) and _entries(base, ours, theirs):
        return _lists(KEYED_LISTS[name], base, ours, theirs, where)
    return ours, [where or "(the whole file)"]


def _all(kind: type, base: Any, ours: Any, theirs: Any) -> bool:
    return isinstance(ours, kind) and isinstance(theirs, kind) and (base is MISSING or isinstance(base, kind))


def _entries(*lists: Any) -> bool:
    return all(isinstance(entry, dict) for items in lists if items is not MISSING for entry in items)


def _objects(base: Any, ours: dict, theirs: dict, where: str) -> tuple[dict, list[str]]:
    base = base if isinstance(base, dict) else {}
    merged = {
        key: values(base.get(key, MISSING), ours.get(key, MISSING), theirs.get(key, MISSING), _at(where, key))
        for key in dict.fromkeys([*ours, *theirs])
    }
    return (
        {key: value for key, (value, _) in merged.items() if value is not MISSING},
        [conflict for _, conflicts in merged.values() for conflict in conflicts],
    )


def _lists(key_of: Callable[[dict], Any], base: Any, ours: list, theirs: list, where: str) -> tuple[list, list[str]]:
    def by_key(items: Any) -> dict:
        return {key_of(entry): entry for entry in (items if isinstance(items, list) else [])}

    old, mine, other = by_key(base), by_key(ours), by_key(theirs)
    merged = {
        key: values(old.get(key, MISSING), mine.get(key, MISSING), other.get(key, MISSING), f"{where}[{_label(key)}]")
        for key in dict.fromkeys([*mine, *other])
    }
    return (
        [value for value, _ in merged.values() if value is not MISSING],
        [conflict for _, conflicts in merged.values() for conflict in conflicts],
    )


def _at(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


def _label(key: Any) -> str:
    return ".".join(map(str, key)) if isinstance(key, tuple) else str(key)


def declared(described: dict | None) -> Any:
    """A `dataset.json` without what the catalog fills in (rows, types, nulls).

    Those are written again from the merge version's catalog, not merged.
    """
    if described is None:
        return MISSING
    return {
        **described,
        "tables": [
            {
                **{key: value for key, value in table.items() if key not in ("rows", "kind")},
                **(
                    {"fields": [
                        {key: value for key, value in column.items() if key not in ("type", "nullable")}
                        for column in table["fields"]
                    ]}
                    if "fields" in table
                    else {}
                ),
            }
            for table in described.get("tables", [])
        ],
    }


def primary_keys(base: dict | None, ours: dict | None, theirs: dict | None) -> dict[tuple[str, str], list[str]]:
    """The tables whose rows may be merged, each with its primary key.

    Ours and theirs declare the same key, and the base does as well or did not
    hold the table.
    """

    def keys(described: dict | None) -> dict[tuple[str, str], Any]:
        return {
            (table.get("schema"), table.get("name")): table.get("primary_key")
            for table in (described or {}).get("tables", [])
        }

    old, mine, other = keys(base), keys(ours), keys(theirs)
    return {
        table: list(key)
        for table, key in mine.items()
        if isinstance(key, list)
        and key
        and other.get(table) == key
        and old.get(table, key) == key
    }


# -- tables ------------------------------------------------------------------------


@dataclass
class Plan:
    """What a merge does to the working catalog's tables, worked out before it writes."""

    conflicts: list[str] = field(default_factory=list)
    #: Tables and views written as theirs holds them.
    taken: list[tuple[str, str]] = field(default_factory=list)
    #: Tables and views dropped because theirs dropped them.
    dropped: list[tuple[str, str]] = field(default_factory=list)
    #: Tables whose rows were merged by primary key: the keys of rows from theirs.
    rows: dict[tuple[str, str], int] = field(default_factory=dict)


def tables(
    working: Path,
    base: Path,
    theirs: Path,
    *,
    keys: dict[tuple[str, str], list[str]],
    data_path: str | None = None,
    repository_data: str,
    setup: Iterable[str] = (),
    write: bool = True,
) -> Plan:
    """Merge into the working catalog, in one new snapshot, what theirs changed since the base.

    ``working`` must hold ours at its last snapshot. ``base`` and ``theirs``
    are frozen catalogs, whose data files are under ``repository_data``.
    ``keys`` are the tables whose rows may be merged (see `primary_keys`).

    Nothing is written when there is a conflict, or when ``write`` is false
    (the descriptions conflict, say): the plan says what would be done.
    """
    import duckdb

    con = duckdb.connect()
    try:
        for statement in [
            *setup,
            *attach_sql(working, alias="lake", data_path=data_path or working_data_path(working)),
            *attach_sql(base, alias="base", data_path=repository_data, read_only=True)[2:],
            *attach_sql(theirs, alias="theirs", data_path=repository_data, read_only=True)[2:],
        ]:
            if statement:
                con.execute(statement)
        merging = _Merging(con, keys, repository_data)
        plan = merging.plan()
        if write and not plan.conflicts and (plan.taken or plan.dropped or plan.rows):
            merging.apply(plan)
        return plan
    except CatalogError:
        raise
    except Exception as exc:  # noqa: BLE001 - duckdb raises several types
        raise CatalogError(f"could not merge into {working}: {exc}") from exc
    finally:
        con.close()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class _Merging:
    """One merge on one connection, with ``lake`` (ours), ``base`` and ``theirs`` attached."""

    def __init__(self, con, keys: dict[tuple[str, str], list[str]], repository_data: str) -> None:
        self.con = con
        self.keys = keys
        self.repository_data = repository_data
        self.staged: dict[tuple[str, str], str] = {}

    # What each catalog holds at its last snapshot.

    def _meta(self, alias: str) -> str:
        return f"__ducklake_metadata_{alias}.main"

    def tables_of(self, alias: str) -> dict[tuple[str, str], int]:
        meta = self._meta(alias)
        rows = self.con.execute(
            f"""
            SELECT s.schema_name, t.table_name, t.table_id
            FROM {meta}.ducklake_table t JOIN {meta}.ducklake_schema s USING (schema_id)
            WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL
            """
        ).fetchall()
        return {(schema, name): table_id for schema, name, table_id in rows}

    def views_of(self, alias: str) -> dict[tuple[str, str], str]:
        meta = self._meta(alias)
        rows = self.con.execute(
            f"""
            SELECT s.schema_name, v.view_name, v.sql
            FROM {meta}.ducklake_view v JOIN {meta}.ducklake_schema s USING (schema_id)
            WHERE v.end_snapshot IS NULL AND s.end_snapshot IS NULL
            """
        ).fetchall()
        return {(schema, name): sql for schema, name, sql in rows}

    def schemas_of(self, alias: str) -> set[str]:
        meta = self._meta(alias)
        rows = self.con.execute(f"SELECT schema_name FROM {meta}.ducklake_schema WHERE end_snapshot IS NULL").fetchall()
        return {schema for (schema,) in rows}

    def files(self, alias: str, table_id: int) -> frozenset[tuple[str, str | None]]:
        """The table's data and delete files, by path under the repository's data folder."""
        meta = self._meta(alias)
        rows = self.con.execute(
            f"""
            SELECT d.path, d.path_is_relative, x.path, x.path_is_relative
            FROM {meta}.ducklake_data_file d
            LEFT JOIN {meta}.ducklake_delete_file x
              ON x.data_file_id = d.data_file_id AND x.end_snapshot IS NULL
            WHERE d.table_id = $t AND d.end_snapshot IS NULL
            """,
            {"t": table_id},
        ).fetchall()

        def relative(path: str | None, is_relative: bool | None) -> str | None:
            if path is None or is_relative:
                return path
            return path.removeprefix(self.repository_data)

        return frozenset((relative(d, dr), relative(x, xr)) for d, dr, x, xr in rows)

    @staticmethod
    def name(alias: str, key: tuple[str, str]) -> str:
        return f"{alias}.{_quoted(key[0])}.{_quoted(key[1])}"

    def shape(self, alias: str, key: tuple[str, str]) -> list[tuple]:
        return self.con.execute(
            f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM {self.name(alias, key)})"
        ).fetchall()

    def same(self, one: str, other: str, key: tuple[str, str], ids: dict[str, dict]) -> bool:
        """Whether two catalogs hold the same table: columns, types and rows as a multiset."""
        held = [key in ids[alias] for alias in (one, other)]
        if not all(held):
            return not any(held)
        if self.shape(one, key) != self.shape(other, key):
            return False
        if self.files(one, ids[one][key]) == self.files(other, ids[other][key]):
            return True
        # Compared as values, as `catalog.same_contents` does.
        first, second = (f"SELECT * FROM {self.name(alias, key)}" for alias in (one, other))
        counts = self.con.execute(f"SELECT (SELECT count(*) FROM ({first})), (SELECT count(*) FROM ({second}))").fetchone()
        if counts[0] != counts[1]:
            return False
        return bool(self.con.execute(f"SELECT NOT EXISTS ({first} EXCEPT ALL {second})").fetchone()[0])

    # Working out the merge.

    def plan(self) -> Plan:
        plan = Plan()
        ids = {alias: self.tables_of(alias) for alias in ("base", "lake", "theirs")}
        for key in sorted(ids["base"].keys() | ids["lake"].keys() | ids["theirs"].keys()):
            label = ".".join(key)
            ours_changed = not self.same("base", "lake", key, ids)
            theirs_changed = not self.same("base", "theirs", key, ids)
            if not theirs_changed or (ours_changed and self.same("lake", "theirs", key, ids)):
                continue
            if not ours_changed:
                (plan.taken if key in ids["theirs"] else plan.dropped).append(key)
            elif key not in ids["lake"] or key not in ids["theirs"]:
                plan.conflicts.append(f"{label}: one side dropped the table and the other changed it")
            elif key not in self.keys:
                plan.conflicts.append(f"{label}: both sides changed the table, and it has no primary key both declare")
            else:
                self._plan_rows(plan, key, has_base=key in ids["base"])
        views = {alias: self.views_of(alias) for alias in ("base", "lake", "theirs")}
        for key in sorted(views["base"].keys() | views["lake"].keys() | views["theirs"].keys()):
            old, mine, other = (views[alias].get(key) for alias in ("base", "lake", "theirs"))
            if other == old or other == mine:
                continue
            if mine == old:
                (plan.taken if other is not None else plan.dropped).append(key)
            else:
                plan.conflicts.append(f"{'.'.join(key)}: both sides changed the view")
        self.views = views
        self.ids = ids
        return plan

    def _plan_rows(self, plan: Plan, key: tuple[str, str], *, has_base: bool) -> None:
        label = ".".join(key)
        columns = self.shape("lake", key)
        if columns != self.shape("theirs", key) or (has_base and self.shape("base", key) != columns):
            plan.conflicts.append(f"{label}: both sides changed the table, and its columns differ")
            return
        primary = self.keys[key]
        if not set(primary) <= {column for column, _ in columns}:
            plan.conflicts.append(f"{label}: the primary key names a column the table does not have")
            return
        keys = ", ".join(map(_quoted, primary))
        whole = "row(" + ", ".join(_quoted(column) for column, _ in columns) + ")"
        sources = {alias: self.name(alias, key) for alias in ("lake", "theirs")}
        sources["base"] = self.name("base", key) if has_base else f"(SELECT * FROM {self.name('lake', key)} LIMIT 0)"
        for alias, source in sources.items():
            nulls = " OR ".join(f"{_quoted(column)} IS NULL" for column in primary)
            bad = self.con.execute(
                f"SELECT count(*) FILTER (WHERE {nulls}), count(*) - count(DISTINCT row({keys})) FROM {source}"
            ).fetchone()
            if bad[0] or bad[1]:
                plan.conflicts.append(f"{label}: the primary key ({', '.join(primary)}) holds NULL or repeats in {_SIDES[alias]}")
                return
        staged = f"__fdl_merge_{len(self.staged)}"
        joined = f"""
            WITH b AS (SELECT {keys}, {whole} AS r FROM {sources['base']}),
                 o AS (SELECT {keys}, {whole} AS r FROM {sources['lake']}),
                 t AS (SELECT {keys}, {whole} AS r FROM {sources['theirs']})
            SELECT {keys}, b.r AS b, o.r AS o, t.r AS t
            FROM b FULL JOIN o USING ({keys}) FULL JOIN t USING ({keys})
        """
        self.con.execute(
            f"""
            CREATE TEMP TABLE {staged} AS
            SELECT {keys},
                   o IS DISTINCT FROM b AND t IS DISTINCT FROM b AND o IS DISTINCT FROM t AS conflict,
                   t IS NOT NULL AS present
            FROM ({joined})
            WHERE t IS DISTINCT FROM b AND (o IS NOT DISTINCT FROM b OR o IS DISTINCT FROM t)
            """
        )
        clashes = self.con.execute(f"SELECT {keys} FROM {staged} WHERE conflict ORDER BY ALL LIMIT {LISTED_KEYS + 1}").fetchall()
        if clashes:
            total = self.con.execute(f"SELECT count(*) FROM {staged} WHERE conflict").fetchone()[0]
            listed = "; ".join(", ".join(f"{column}={value!r}" for column, value in zip(primary, row)) for row in clashes[:LISTED_KEYS])
            more = f" and {total - LISTED_KEYS} more" if total > LISTED_KEYS else ""
            plan.conflicts.append(f"{label}: both sides changed {total} row(s) differently: {listed}{more}")
            return
        taken = self.con.execute(f"SELECT count(*) FROM {staged}").fetchone()[0]
        if taken:
            self.staged[key] = f"temp.main.{staged}"
            plan.rows[key] = taken

    # Writing it.

    def apply(self, plan: Plan) -> None:
        con = self.con
        # A view's SQL names its tables as the writer did, from inside the catalog.
        con.execute("USE lake")
        con.execute("BEGIN")
        for key in plan.dropped:
            kind = "VIEW" if key in self.views["base"] or key in self.views["lake"] else "TABLE"
            con.execute(f"DROP {kind} {self.name('lake', key)}")
        for key in plan.taken:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS lake.{_quoted(key[0])}")
            if key in self.ids["theirs"]:
                self._take_table(key)
        for key, staged in self.staged.items():
            table = _quoted(key[1])
            same = " AND ".join(f"s.{_quoted(column)} = {{0}}.{_quoted(column)}" for column in self.keys[key])
            con.execute(
                f"DELETE FROM {self.name('lake', key)} "
                f"WHERE EXISTS (SELECT 1 FROM {staged} s WHERE {same.format(table)})"
            )
            con.execute(
                f"INSERT INTO {self.name('lake', key)} SELECT x.* FROM {self.name('theirs', key)} x "
                f"WHERE EXISTS (SELECT 1 FROM {staged} s WHERE s.present AND {same.format('x')})"
            )
        for key in plan.taken:
            if key in self.views["theirs"]:
                body = self.views["theirs"][key].replace("{DUCKLAKE_CATALOG}", "lake")
                con.execute(f"CREATE OR REPLACE VIEW {self.name('lake', key)} AS {body}")
        kept = {schema for schema, _ in (self.ids["lake"].keys() | self.views["lake"].keys()) - set(plan.dropped)}
        kept |= {schema for schema, _ in plan.taken}
        gone = (self.schemas_of("base") & self.schemas_of("lake")) - self.schemas_of("theirs") - kept
        for schema in sorted(gone):
            con.execute(f"DROP SCHEMA lake.{_quoted(schema)}")
        con.execute("COMMIT")

    def _take_table(self, key: tuple[str, str]) -> None:
        """Write the table as theirs holds it, beside ours, then put it in its place."""
        con = self.con
        columns = con.execute(
            """
            SELECT column_name, data_type, is_nullable, comment FROM duckdb_columns()
            WHERE database_name = 'theirs' AND schema_name = $s AND table_name = $t
            ORDER BY column_index
            """,
            {"s": key[0], "t": key[1]},
        ).fetchall()
        comment = con.execute(
            "SELECT comment FROM duckdb_tables() WHERE database_name = 'theirs' AND schema_name = $s AND table_name = $t",
            {"s": key[0], "t": key[1]},
        ).fetchone()
        types = dict(self.shape("theirs", key))
        body = ", ".join(
            f"{_quoted(column)} {types[column]}" + ("" if nullable else " NOT NULL")
            for column, _, nullable, _ in columns
        )
        staging = self.name("lake", (key[0], "__fdl_merge"))
        con.execute(f"CREATE TABLE {staging} ({body})")
        con.execute(f"INSERT INTO {staging} SELECT * FROM {self.name('theirs', key)}")
        if key in self.ids["lake"]:
            con.execute(f"DROP TABLE {self.name('lake', key)}")
        con.execute(f"ALTER TABLE {staging} RENAME TO {_quoted(key[1])}")
        for column, _, _, text in columns:
            if text:
                con.execute(f"COMMENT ON COLUMN {self.name('lake', key)}.{_quoted(column)} IS {_literal(text)}")
        if comment and comment[0]:
            con.execute(f"COMMENT ON TABLE {self.name('lake', key)} IS {_literal(comment[0])}")


_SIDES = {"base": "the base", "lake": "ours", "theirs": "theirs"}
