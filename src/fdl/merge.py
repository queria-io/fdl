"""Merging: the three-way comparison of tables.

Ours is the version the working catalog was checked out at, theirs the version
being merged, and the base their common ancestor (see `repo.merge_bases`). A
table, a row or a cell (a row's value in one column) one side changed takes that
side's change; a cell both sides changed differently, or a row one side dropped
and the other changed, is a conflict. Rows are matched only by a primary key that
both sides declare for the table (see `declared`). Columns are merged by name:
a column only one side added, dropped or gave another type takes that change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import declared
from .catalog import CatalogError, attach_sql, working_data_path


class Conflicts(Exception):
    """The two sides changed the same thing differently, so no version is made."""

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__(f"{len(conflicts)} conflict(s)")
        self.conflicts = conflicts


#: How many keys of a conflicting table are listed.
LISTED_KEYS = 10

Declared = tuple[declared.Facts, set[str]]


def primary_keys(base: Declared, ours: Declared, theirs: Declared) -> dict[tuple[str, str], list[str]]:
    """The tables whose rows may be merged, each with its primary key.

    Each side is what it declares and the tables it holds (`repo.declarations`,
    `repo.tables_of`). Ours and theirs declare the same primary key, and the base
    declares it as well or did not hold the table.
    """
    (old, held), (mine, _), (other, _) = base, ours, theirs
    found: dict[tuple[str, str], list[str]] = {}
    for name, key in mine.primary_keys.items():
        if other.primary_keys.get(name) == key and (name not in held or old.primary_keys.get(name) == key):
            schema, table = name.split(".", 1)
            found[(schema, table)] = list(key)
    return found


# -- tables ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Column:
    """A top-level column of a table on one side: DuckLake's ID, its name, DuckDB's type."""

    id: int
    name: str
    type: str
    nullable: bool
    default: str | None


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
    #: Comments both sides changed differently: ours is kept.
    comments: list[str] = field(default_factory=list)


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
        if write and not plan.conflicts and (plan.taken or plan.dropped or plan.rows or merging.theirs_commented()):
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


def _same_key(primary: list[str], outer: str) -> str:
    """The condition that the staged row ``s`` has the key of ``outer``'s row."""
    return " AND ".join(f"s.{_quoted(column)} = {outer}.{_quoted(column)}" for column in primary)


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class _Merging:
    """One merge on one connection, with ``lake`` (ours), ``base`` and ``theirs`` attached."""

    def __init__(self, con, keys: dict[tuple[str, str], list[str]], repository_data: str) -> None:
        self.con = con
        self.keys = keys
        self.repository_data = repository_data
        #: By table: the staged rows, the columns to write and the ALTER TABLE statements first.
        self.staged: dict[tuple[str, str], tuple[str, list[str], list[str]]] = {}
        #: Temp tables made so far, kept or not, so each gets a name of its own.
        self.made = 0

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
        """The table's data and delete files, by path under the repository's data folder.

        A relative path is relative to the table's folder, so the schema's and
        the table's paths come first: a working metadata that pinned the files
        (see `catalog.pin_files`) records the same file as the whole URL.
        """
        meta = self._meta(alias)
        rows = self.con.execute(
            f"""
            SELECT coalesce(s.path, '') || coalesce(t.path, ''),
                   d.path, d.path_is_relative, x.path, x.path_is_relative
            FROM {meta}.ducklake_data_file d
            JOIN {meta}.ducklake_table t ON t.table_id = d.table_id AND t.end_snapshot IS NULL
            JOIN {meta}.ducklake_schema s ON s.schema_id = t.schema_id AND s.end_snapshot IS NULL
            LEFT JOIN {meta}.ducklake_delete_file x
              ON x.data_file_id = d.data_file_id AND x.end_snapshot IS NULL
            WHERE d.table_id = $t AND d.end_snapshot IS NULL
            """,
            {"t": table_id},
        ).fetchall()

        def relative(prefix: str, path: str | None, is_relative: bool | None) -> str | None:
            if path is None:
                return None
            return prefix + path if is_relative else path.removeprefix(self.repository_data)

        return frozenset((relative(p, d, dr), relative(p, x, xr)) for p, d, dr, x, xr in rows)

    def columns(self, alias: str, table_id: int) -> list[tuple]:
        """The table's columns as DuckLake records them: names, types, defaults and NOT NULL."""
        return self.con.execute(
            f"""
            SELECT column_id, column_order, column_name, column_type, default_value, nulls_allowed, parent_column
            FROM {self._meta(alias)}.ducklake_column
            WHERE table_id = $t AND end_snapshot IS NULL
            ORDER BY column_id
            """,
            {"t": table_id},
        ).fetchall()

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
        self.ids = ids
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
        self.comments = {alias: self.comments_of(alias) for alias in ("base", "lake", "theirs")}
        return plan

    def comments_of(self, alias: str) -> dict[tuple[str, str, str | None], str | None]:
        """DuckLake's ``comment`` of each table, view (column ``None``) and column."""
        params = {"d": alias}
        objects = self.con.execute(
            "SELECT schema_name, table_name, comment FROM duckdb_tables() WHERE database_name = $d "
            "UNION ALL SELECT schema_name, view_name, comment FROM duckdb_views() WHERE database_name = $d",
            params,
        ).fetchall()
        columns = self.con.execute(
            "SELECT schema_name, table_name, column_name, comment FROM duckdb_columns() WHERE database_name = $d",
            params,
        ).fetchall()
        held = {(schema, name, None): text for schema, name, text in objects}
        held |= {(schema, name, column): text for schema, name, column, text in columns}
        return held

    def theirs_commented(self) -> bool:
        """Whether theirs changed a comment since the base, which a merge with no table to write still brings."""
        base, theirs = self.comments["base"], self.comments["theirs"]
        return any(base.get(slot) != text for slot, text in theirs.items())

    def described(self, alias: str, key: tuple[str, str], table_id: int) -> list[_Column]:
        """The table's top-level columns, in order, with DuckLake's IDs and DuckDB's types."""
        rows = self.con.execute(
            f"""
            SELECT c.column_id, c.column_name, d.data_type, d.is_nullable, d.column_default
            FROM {self._meta(alias)}.ducklake_column c
            JOIN duckdb_columns() d
              ON d.database_name = $d AND d.schema_name = $s AND d.table_name = $t AND d.column_name = c.column_name
            WHERE c.table_id = $id AND c.end_snapshot IS NULL AND c.parent_column IS NULL
            ORDER BY c.column_order
            """,
            {"d": alias, "s": key[0], "t": key[1], "id": table_id},
        ).fetchall()
        # DuckDB writes a column without a default as the text NULL.
        return [_Column(i, name, kind, nullable, None if default in (None, "NULL") else default) for i, name, kind, nullable, default in rows]

    def _slots(self, key: tuple[str, str], has_base: bool) -> list[dict[str, _Column | None]]:
        """Each column of any side, as what each side holds of it. A column the base
        holds is found by its ID on a side that kept the table (by name on one that
        made it again); a column added since is found by name."""
        held = {
            alias: self.described(alias, key, self.ids[alias][key]) if alias != "base" or has_base else []
            for alias in ("base", "lake", "theirs")
        }
        same_table = {alias: has_base and self.ids[alias][key] == self.ids["base"][key] for alias in ("lake", "theirs")}
        slots: list[dict[str, _Column | None]] = []
        taken: dict[str, set[int]] = {"lake": set(), "theirs": set()}
        for old in held["base"]:
            slot: dict[str, _Column | None] = {"base": old}
            for alias in ("lake", "theirs"):
                found = next(
                    (c for c in held[alias] if (c.id == old.id if same_table[alias] else c.name == old.name)),
                    None,
                )
                slot[alias] = found
                if found is not None:
                    taken[alias].add(found.id)
            slots.append(slot)
        added: dict[str, dict[str, _Column | None]] = {}
        for alias in ("lake", "theirs"):
            for column in held[alias]:
                if column.id not in taken[alias]:
                    added.setdefault(column.name, {"base": None, "lake": None, "theirs": None})[alias] = column
        # In ours' order, then what only theirs added.
        order = {c.name: n for n, c in enumerate(held["lake"])}
        slots.sort(key=lambda slot: order.get(slot["lake"].name, len(order)) if slot["lake"] else len(order))
        return slots + list(added.values())

    @staticmethod
    def _merged(slot: dict[str, _Column | None]) -> tuple[_Column | None, bool]:
        """The column a slot takes in the merge, and whether it is a conflict."""
        b, o, t = slot["base"], slot["lake"], slot["theirs"]

        def pick(old, mine, other):
            if mine == other or other == old:
                return mine, True
            if mine == old:
                return other, True
            return mine, False

        if o is None or t is None or b is None:
            present = (b is not None, o is not None, t is not None)
            keeps, ok = pick(*present)
            if not ok:
                return None, True
            if not keeps:
                # Dropped by one side: a conflict when the other renamed or retyped it.
                kept = o or t
                return None, kept is not None and b is not None and (kept.name, kept.type) != (b.name, b.type)
            if b is None and o is not None and t is not None:
                # Both added it: the same type, or a conflict.
                return o, o.type != t.type
            return o or t, False
        name, named = pick(b.name, o.name, t.name)
        kind, typed = pick(b.type, o.type, t.type)
        ours = o if (o.name, o.type) == (name, kind) else t if (t.name, t.type) == (name, kind) else o
        return _Column(ours.id, name, kind, ours.nullable, ours.default), not (named and typed)

    def _plan_rows(self, plan: Plan, key: tuple[str, str], *, has_base: bool) -> None:
        label = ".".join(key)
        slots = self._slots(key, has_base)
        merged: list[_Column | None] = []
        clashes: list[str] = []
        for slot in slots:
            column, clash = self._merged(slot)
            merged.append(column)
            if clash:
                clashes.append(next(c.name for c in (slot["base"], slot["lake"], slot["theirs"]) if c))
        if clashes:
            plan.conflicts.append(f"{label}: both sides changed the column(s) {', '.join(clashes)} differently")
            return
        primary = self.keys[key]
        by_name = {column.name: n for n, column in enumerate(merged) if column}
        sides = ("base", "lake", "theirs") if has_base else ("lake", "theirs")
        if not all(name in by_name and all(slots[by_name[name]][alias] for alias in sides) for name in primary):
            plan.conflicts.append(f"{label}: the primary key names a column the table does not have")
            return
        for n, (slot, column) in enumerate(zip(slots, merged)):
            added = [alias for alias in ("lake", "theirs") if slot["base"] is None and slot[alias] and column]
            if len(added) == 1 and not slot[added[0]].nullable and slot[added[0]].default is None:
                other = "theirs" if added[0] == "lake" else "lake"
                if self._added_rows(key, primary, slots, other, has_base):
                    plan.conflicts.append(
                        f"{label}: {_SIDES[added[0]]} added the column {column.name} NOT NULL with no default, "
                        f"and {_SIDES[other]} added rows"
                    )
                    return
        keys = ", ".join(map(_quoted, primary))
        sources = {alias: self.name(alias, key) for alias in ("lake", "theirs")}
        for alias in sides:
            listed = ", ".join(_quoted(slots[by_name[name]][alias].name) for name in primary)
            nulls = " OR ".join(f"{_quoted(slots[by_name[name]][alias].name)} IS NULL" for name in primary)
            bad = self.con.execute(
                f"SELECT count(*) FILTER (WHERE {nulls}), count(*) - count(DISTINCT row({listed})) FROM {self.name(alias, key)}"
            ).fetchone()
            if bad[0] or bad[1]:
                plan.conflicts.append(f"{label}: the primary key ({', '.join(primary)}) holds NULL or repeats in {_SIDES[alias]}")
                return
        staged = f"__fdl_merge_{self.made}"
        self.made += 1
        key_slots = {by_name[name] for name in primary}
        cells = [n for n in range(len(slots)) if n not in key_slots]
        # Compared in the type the merge gives the column; a dropped one in the base's.
        typed = {n: (merged[n] or slots[n]["base"]).type for n in cells}
        kept = [n for n in cells if merged[n]]
        both = [n for n in cells if slots[n]["lake"] and slots[n]["theirs"]]

        def default(n: int) -> str:
            found = next((c.default for c in (slots[n]["lake"], slots[n]["theirs"], slots[n]["base"]) if c and c.default), None)
            return found or "NULL"

        def value(alias: str, n: int) -> str:
            column = slots[n][alias]
            held = _quoted(column.name) if column else f"({default(n)})"
            return f"TRY_CAST({held} AS {typed[n]})"

        # Cells some side holds in another type than the merge gives the column: each
        # side's value as text too, to tell a value it changed from the base's.
        retyped = [n for n in cells if any(slots[n][a] and slots[n][a].type != typed[n] for a in sides)]

        def raw(alias: str, n: int) -> str:
            column = slots[n][alias]
            return f"CAST({_quoted(column.name)} AS VARCHAR)" if column else "NULL::VARCHAR"

        def uncast(alias: str, n: int) -> str:
            column = slots[n][alias]
            if column is None or column.type == typed[n]:
                return "false"
            return f"({_quoted(column.name)} IS NOT NULL AND TRY_CAST({_quoted(column.name)} AS {typed[n]}) IS NULL)"

        def side(alias: str, mark: str) -> str:
            if alias == "base" and not has_base:
                listed = ", ".join(f"NULL::{slots[by_name[name]]['lake'].type} AS {_quoted(name)}" for name in primary)
                picked = "".join(f", NULL::{typed[n]} AS {mark}{n}" for n in cells)
                picked += "".join(f", NULL::VARCHAR AS {mark}r{n}, false AS {mark}u{n}" for n in retyped)
                return f"SELECT {listed}, true AS {mark}p{picked} WHERE false"
            listed = ", ".join(f"{_quoted(slots[by_name[name]][alias].name)} AS {_quoted(name)}" for name in primary)
            picked = "".join(f", {value(alias, n)} AS {mark}{n}" for n in cells)
            picked += "".join(f", {raw(alias, n)} AS {mark}r{n}, {uncast(alias, n)} AS {mark}u{n}" for n in retyped)
            return f"SELECT {listed}, true AS {mark}p{picked} FROM {sources.get(alias) or self.name(alias, key)}"

        def differ(one: str, other: str, n: int) -> str:
            return f"{one}{n} IS DISTINCT FROM {other}{n}"

        def any_of(terms: list[str]) -> str:
            return " OR ".join(terms) or "false"

        # Merged by cell: a cell takes theirs where theirs changed it, ours otherwise.
        written = "".join(
            f", CASE WHEN tp IS NULL THEN o{n} WHEN op IS NULL THEN t{n} "
            f"WHEN {differ('t', 'b', n)} THEN t{n} ELSE o{n} END AS {_quoted(merged[n].name)}"
            for n in kept
        )
        clash = [" AND ".join((differ("o", "b", n), differ("t", "b", n), differ("o", "t", n))) for n in kept]
        dropped = [
            (n, "o" if slots[n]["lake"] else "t") for n in cells if merged[n] is None and (slots[n]["lake"] or slots[n]["theirs"])
        ]
        conflict = " OR ".join(
            f"({condition})"
            for condition in (
                # A value a side changed that does not cast to the type the merge gives its column.
                *(
                    f"coalesce({mark}u{n}, false) AND {mark}r{n} IS DISTINCT FROM br{n}"
                    for n in retyped
                    for mark in ("o", "t")
                ),
                # A cell both changed differently.
                f"bp AND op AND tp AND ({any_of(clash)})",
                # A row both added, unless the same.
                f"bp IS NULL AND op AND tp AND ({any_of([differ('o', 't', n) for n in both])})",
                # A row one side dropped and the other changed.
                f"bp AND op IS NULL AND tp AND ({any_of([differ('t', 'b', n) for n in cells])})",
                f"bp AND tp IS NULL AND op AND ({any_of([differ('o', 'b', n) for n in cells])})",
                # A cell of a column one side dropped, which the other changed.
                *(f"bp AND {mark}p AND {differ(mark, 'b', n)}" for n, mark in dropped),
            )
        )
        keyed = ", ".join(_quoted(name) for name in primary)
        joined = f"""
            SELECT * FROM ({side('base', 'b')}) FULL JOIN ({side('lake', 'o')}) USING ({keyed})
                     FULL JOIN ({side('theirs', 't')}) USING ({keyed})
        """
        self.con.execute(
            f"""
            CREATE TEMP TABLE {staged} AS
            SELECT * FROM (
                SELECT {keyed}{written},
                       {conflict} AS __fdl_conflict,
                       CASE WHEN bp THEN coalesce(op AND tp, false) ELSE coalesce(op, tp, false) END AS __fdl_present,
                       coalesce(op, false) AS __fdl_ours,
                       {", ".join(f"o{n}" for n in cells) or "NULL AS __fdl_none"}
                FROM ({joined})
            )
            WHERE __fdl_conflict OR __fdl_present IS DISTINCT FROM __fdl_ours
               OR (__fdl_present AND ({any_of([f"{_quoted(merged[n].name)} IS DISTINCT FROM o{n}" for n in kept])}))
            """
        )
        clashes_found = self.con.execute(
            f"SELECT {keyed} FROM {staged} WHERE __fdl_conflict ORDER BY ALL LIMIT {LISTED_KEYS + 1}"
        ).fetchall()
        if clashes_found:
            total = self.con.execute(f"SELECT count(*) FROM {staged} WHERE __fdl_conflict").fetchone()[0]
            listed = "; ".join(", ".join(f"{column}={value!r}" for column, value in zip(primary, row)) for row in clashes_found[:LISTED_KEYS])
            more = f" and {total - LISTED_KEYS} more" if total > LISTED_KEYS else ""
            plan.conflicts.append(f"{label}: both sides changed {total} row(s) differently: {listed}{more}")
            return
        altered = self._alterations(key, slots, merged)
        taken = self.con.execute(f"SELECT count(*) FROM {staged}").fetchone()[0]
        if taken or altered:
            self.staged[key] = (f"temp.main.{staged}", [*primary, *(merged[n].name for n in kept)], altered)
            plan.rows[key] = taken

    def _added_rows(self, key, primary: list[str], slots, alias: str, has_base: bool) -> bool:
        """Whether ``alias`` holds a row the base does not."""
        if not has_base:
            return bool(self.con.execute(f"SELECT count(*) FROM {self.name(alias, key)}").fetchone()[0])
        by_name = {s["base"].name: s for s in slots if s["base"]}
        mine = " AND ".join(f"x.{_quoted(by_name[n][alias].name)} IS NOT DISTINCT FROM b.{_quoted(by_name[n]['base'].name)}" for n in primary)
        return bool(
            self.con.execute(
                f"SELECT count(*) FROM {self.name(alias, key)} x WHERE NOT EXISTS (SELECT 1 FROM {self.name('base', key)} b WHERE {mine})"
            ).fetchone()[0]
        )

    def _alterations(self, key: tuple[str, str], slots, merged: list[_Column | None]) -> list[str]:
        """The ALTER TABLE statements that give our table the merged columns."""
        table = self.name("lake", key)
        drops, renames, retypes, adds = [], [], [], []
        for slot, column in zip(slots, merged):
            ours = slot["lake"]
            if ours is not None and column is None:
                drops.append(f"ALTER TABLE {table} DROP COLUMN {_quoted(ours.name)}")
            elif ours is None and column is not None:
                default = f" DEFAULT {column.default}" if column.default else ""
                adds.append(f"ALTER TABLE {table} ADD COLUMN {_quoted(column.name)} {column.type}{default}")
            elif ours is not None and column is not None:
                if ours.name != column.name:
                    renames.append(f"ALTER TABLE {table} RENAME COLUMN {_quoted(ours.name)} TO {_quoted(column.name)}")
                if ours.type != column.type:
                    retypes.append(f"ALTER TABLE {table} ALTER COLUMN {_quoted(column.name)} SET DATA TYPE {column.type}")
        return drops + renames + retypes + adds

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
        for key, (staged, names, altered) in self.staged.items():
            primary = self.keys[key]
            for statement in altered:
                con.execute(statement)
            # Aliased, so a table named like the staged one's alias still binds to the outer row.
            con.execute(
                f"DELETE FROM {self.name('lake', key)} AS __fdl_target "
                f"WHERE EXISTS (SELECT 1 FROM {staged} s WHERE {_same_key(primary, '__fdl_target')})"
            )
            listed = ", ".join(map(_quoted, names))
            con.execute(
                f"INSERT INTO {self.name('lake', key)} ({listed}) SELECT {listed} FROM {staged} WHERE __fdl_present"
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
        self._merge_comments(plan)
        con.execute("COMMIT")

    def _merge_comments(self, plan: Plan) -> None:
        """Merge each comment three ways, as a value: writing a table again would lose ours or theirs."""
        base, ours, theirs = (self.comments[alias] for alias in ("base", "lake", "theirs"))
        merged = self.comments_of("lake")
        # Read inside the transaction: a view theirs replaced with a table is a table now.
        views = {tuple(row) for row in self.con.execute("SELECT schema_name, view_name FROM duckdb_views() WHERE database_name = 'lake'").fetchall()}
        for slot in sorted(merged, key=lambda slot: (slot[0], slot[1], slot[2] or "")):
            old, mine, other = (side.get(slot) for side in (base, ours, theirs))
            if mine == old:
                wanted = other if slot in theirs else mine
            elif other in (old, mine) or slot not in theirs:
                wanted = mine
            else:
                wanted = mine
                plan.comments.append(".".join(part for part in slot if part))
            if wanted == merged[slot]:
                continue
            schema, name, column = slot
            text = _literal(wanted) if wanted is not None else "NULL"
            if column is not None:
                target = f"COLUMN {self.name('lake', (schema, name))}.{_quoted(column)}"
            else:
                target = f"{'VIEW' if (schema, name) in views else 'TABLE'} {self.name('lake', (schema, name))}"
            self.con.execute(f"COMMENT ON {target} IS {text}")

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
