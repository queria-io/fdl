# Contributing to fdl

## Prerequisites

- Python 3.13 or later
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
docs/spec.md          — the format
src/fdl/
├── layout.py         — where each file of a repository sits
├── version.py        — version.json and version IDs
├── refs.py           — reading refs and moving them on condition
├── catalog.py        — DuckLake catalogs in SQLite and DuckDB, and the conversion
├── repo.py           — init, checkout, commit, push, log, expire
├── store.py          — what a store has to provide
├── stores.py         — a folder and S3 as stores
└── cli.py            — the fdl command
```

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

Documentation is built with [Zensical](https://zensical.com/) and deployed to [fdl.flo8s.com](https://fdl.flo8s.com/).

To preview locally:

```bash
uv run zensical serve
```

Documentation deploys automatically on push to main when files under `docs/` or `CHANGELOG.md` change.

## Code style

- Python 3.13+ — use modern syntax (`X | Y` for unions, etc.)
- All code comments, docstrings, and commit messages in English
