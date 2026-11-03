# Conformance

Tests any FDL implementation against the specification (`docs/spec/`): sample
repositories with what a reader must report, and a checker for the
repositories a writer or a host leaves behind.

## Readers

```bash
uv run python conformance/run.py -- <command>
```

For each case in `cases/` and each ref in its `expect.json`, the runner calls
`<command> <root> <ref>`, where `<root>` is the path of `cases/<case>/repo/`
with a trailing slash and `<ref>` is `HEAD` or a full ref name such as
`refs/tags/first`. The command reads that version and prints one JSON object:

```json
{"version": "01a1a0a6-…", "seq": 2, "tables": {"geo.town": {"rows": 10000}}}
```

`tables` has every table of the version, as `<schema>.<table>`, with its row
count. When the command refuses to read (a major version it does not know, a
version whose `id` is not its folder's, a version past retention), it exits
with a non-zero status.

A table marked `may_skip` has deletes or a renamed column. A reader that
cannot apply them may leave the table out, but must not report other rows.

`adapters/fdl_reader.py` is the command for fdl:

```bash
uv run python conformance/run.py -- uv run python conformance/adapters/fdl_reader.py
```

| Case | What it checks |
| --- | --- |
| `basic` | Two versions on main, the first tagged: `HEAD`, branches and tags resolve, deletes and renames are applied |
| `data-dir` | Data files under a `data_dir` other than `data/` |
| `ignore-unknown` | Unknown fields in `fdl.json` and `version.json`, and other files at the root, are ignored |
| `parents` | `parents` with more than one entry is accepted |
| `unknown-major` | `fdl.json` of an unknown major version is refused |
| `version-major` | `version.json` of an unknown major version is refused, other versions still read |
| `id-mismatch` | `version.json` whose `id` is not its folder's is refused |
| `expired` | A version with `expired.json` is refused even while its catalog is still there |

The cases are committed so that implementations in other languages use them
without fdl. `make_cases.py` builds them again with fdl; version IDs change
on every run.

## Writers and hosts

```bash
uv run python conformance/check.py <root>
```

Reads a repository (a folder, `https://…/` or `s3://…/`) and reports what
breaks the specification: `fdl.json`, `HEAD` and the refs, every field of
`version.json`, `seq`, the sizes and sha256 of the catalog, `dataset.json`
and `files.json`, that every data file the catalog names is under `data_dir`
and present, that no rows are held inline, and that no catalog sits at the
root. Versions are found from the refs and their parents, so it works over
HTTP, where nothing can be listed. Lines starting with `note` are allowed by
the specification but worth knowing, such as a `refs.json` that lags behind.

Run it on what a writer pushed, or on a host:

```bash
uv run python conformance/check.py https://data.queria.io/datasets/queria/jgrants/
```
