# Contributing to fdl

## Prerequisites

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/) for dependency management

## Getting started

```bash
git clone https://github.com/queria-io/fdl.git
cd fdl
uv sync
uv run fdl --help
uv run pytest
```

## Project structure

```
docs/                 — the documentation site (Blume); docs/spec/ is the specification, docs/ja/ the Japanese mirror
src/fdl/
├── layout.py         — where each file of a repository sits
├── version.py        — version.json and version IDs
├── refs.py           — reading refs and moving them on condition
├── catalog.py        — DuckLake catalogs in SQLite and DuckDB, and the conversion
├── dataset.py        — dataset.json: reading the description and checking it against the catalog
├── objects.py        — assets and tree.json
├── profiles.py       — the profile of each table
├── repo.py           — init, checkout, commit, push, log, expire, withdraw
├── store.py          — what a store has to provide
├── stores.py         — a folder, S3 and a host over HTTP as stores
└── cli.py            — the fdl command
tests/                — pytest; tests/http_repo.py is a small host over HTTP for the tests
```

Change the specification first, then the code. Every page in `docs/` has a Japanese counterpart under `docs/ja/`; change both in the same pull request. Check the docs with `npm ci && npm run check`.

Report a vulnerability privately, as [SECURITY.md](SECURITY.md) says.

## Commit conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/ja/v1.0.0/). Commit messages are parsed by [python-semantic-release](https://python-semantic-release.readthedocs.io/) to determine version bumps and generate the CHANGELOG automatically.

## Releases

Releases are triggered manually via the GitHub Actions "Release" workflow (`workflow_dispatch`). The workflow runs python-semantic-release, which:

1. Reads commit history since the last release
2. Determines the next version based on commit types
3. Updates the version in pyproject.toml
4. Generates CHANGELOG.md entries
5. Creates a Git tag
6. Builds and publishes to PyPI

Do not manually edit the version in pyproject.toml or modify CHANGELOG.md — these are managed by the release automation.

## Documentation

Documentation is built with [Zensical](https://zensical.com/) and deployed to [fdl.queria.io](https://fdl.queria.io/).

To preview locally:

```bash
uv run zensical serve
```

Documentation deploys automatically on push to main when files under `docs/` or `CHANGELOG.md` change.

## Code style

- Python 3.10+ — `X | Y` unions are fine; nothing newer
- All code comments, docstrings, and commit messages in English
