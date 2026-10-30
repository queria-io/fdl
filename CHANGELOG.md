# CHANGELOG


## v0.12.1 (2026-10-30)

### Bug Fixes

- Publish as fdl-cli on PyPI
  ([`37afe71`](https://github.com/queria-io/fdl/commit/37afe717b53151f59f4c221292192bb0fba13995))

PyPI refuses the name fdl as too similar to an existing project. The command and the import name
  stay fdl.


## v0.12.0 (2026-10-30)

### Build System

- Publish as fdl on PyPI
  ([`c5fc6c1`](https://github.com/queria-io/fdl/commit/c5fc6c1b5141c000550a15a081d9a8dd71628138))

BREAKING CHANGE: the package is now fdl. frozen-ducklake stays at 0.11, the earlier tool.

### Continuous Integration

- Decouple PyPI publish into a retriable workflow
  ([`270f436`](https://github.com/queria-io/fdl/commit/270f436b63dd8d2f645682415ac968f9d7d9a0c2))

The publish step lived inside release.yml behind a released==true guard, so a failed upload could
  not be retried: re-running release.yml makes semantic-release refuse (the tag already exists) and
  the publish step is skipped. A trusted-publisher misconfiguration left v0.11.0 tagged and released
  on GitHub but never uploaded to PyPI, with no way to recover short of hand-publishing.

Move build+publish into publish.yml (workflow_dispatch, ref input) and have release.yml dispatch it
  with the new tag, mirroring the existing docs trigger. A stranded release can now be published
  with: gh workflow run publish.yml -R <owner>/fdl -f ref=<tag>

### Documentation

- Point to the legacy tag for the earlier tool
  ([`6a6773b`](https://github.com/queria-io/fdl/commit/6a6773b74b7c0a67f7bb1846169313099e809d29))

- Replace derivable project structure with a DuckLake gotcha
  ([`0f5228b`](https://github.com/queria-io/fdl/commit/0f5228b957be12a9636e90b158b9d3140f068120))

The project structure section only restated what `ls src/fdl/` shows. Replace it with the
  CALL-vs-SELECT trap in DuckLake maintenance functions, which is not apparent from any single file
  and corrupts the catalog when missed.

- The earlier tool lives on the legacy branch
  ([`33c0e8c`](https://github.com/queria-io/fdl/commit/33c0e8cc1366c78294c279f3aac5d21790e173a0))

### Features

- Replace the tool with the FDL repository format
  ([`eb4fbe7`](https://github.com/queria-io/fdl/commit/eb4fbe7297870d20c2969bc4cce1d2aa883475a1))

fdl now keeps versions of a DuckLake catalog the way Git keeps commits: versions/<ID>/ holds a
  frozen catalog and version.json (parents, author, message, sha256), refs/heads/main says which
  version readers get, and refs only move by conditional writes. Data files are shared between
  versions and never rewritten.

- docs/spec.md: the format - fdl.repo: init, checkout, commit, push, log, expire, prune -
  fdl.stores: a folder and S3 (conditional writes through boto3) - fdl command: init, checkout,
  commit, log, url, expire

BREAKING CHANGE: the earlier commands (pull, push by target, run, serve, sql and the rest) and their
  configuration are gone. The previous tool is kept at the v0.11.0 tag.

- Run on Python 3.10 and make boto3 an extra
  ([`675f6ac`](https://github.com/queria-io/fdl/commit/675f6ace4832e96de1678748aa0ac2fc32430ca5))

Tools that build on fdl as a library often support older Pythons and bring their own store, so the
  floor goes to 3.10 and S3 support moves to frozen-ducklake[s3].

### Breaking Changes

- The package is now fdl. frozen-ducklake stays at 0.11, the earlier tool.


## v0.11.0 (2026-07-11)

### Chores

- Update org references from flo8s to queria-io
  ([`5db19f5`](https://github.com/queria-io/fdl/commit/5db19f5c753582a06d125df2fe422967241059f2))

### Features

- Always run file cleanup on explicit fdl expire
  ([`302ca9d`](https://github.com/queria-io/fdl/commit/302ca9dd233b6b0fd4bfda0f1d9814ae5a20a32b))

Automatic expiration skips the file cleanup when no snapshot was expired, to avoid paying a
  DATA_PATH LIST (a network round-trip on S3) on every catalog write. That gate also applied to
  explicit fdl expire / fdl.expire() calls, leaving no way to remove orphaned files from a catalog
  whose snapshots are all within the retention window.

Explicit invocations now pass always_cleanup: leftovers from crashed writes are deleted even when
  nothing was expired, and --dry-run reports the file counts (a lower bound: files the expiration
  itself would schedule are only known once it runs). This supersedes the planned standalone prune
  command — DuckLake's ducklake_delete_orphaned_files knows which files are referenced by any
  snapshot, which a hand-rolled catalog-vs-storage diff would misjudge for time-travel data.

Also replace the stale prune.py entry in CONTRIBUTING.md with maintenance.py.

Closes #11

- Expire old snapshots and clean up data files on push
  ([`c018827`](https://github.com/queria-io/fdl/commit/c018827c4a41efbf96d2229cd96217643ff0af65))

Datasets that rebuild frequently accumulate snapshots and dead table versions (including their
  ducklake_inlined_data_* tables) forever, growing the catalog linearly with build count and slowing
  down the SQLite <-> DuckDB conversion in push/pull.

Before converting the catalog, push now expires snapshots older than
  maintenance.snapshot_retention_days (default: 7 days) via ducklake_expire_snapshots and, when
  anything was expired, deletes the data files those snapshots referenced
  (ducklake_cleanup_old_files) and orphaned files older than the cutoff
  (ducklake_delete_orphaned_files). Set maintenance.snapshot_retention_days = false to disable.

- Generalize expiration into fdl expire and auto-runs on writes
  ([`7779898`](https://github.com/queria-io/fdl/commit/777989882fee78643e714cac0972df0494a6499e))

Snapshot expiration hooked only into push left catalogs that are never pushed (local-only usage)
  growing forever. Promote it to a first-class maintenance capability:

- fdl expire TARGET [--retention-days N] [--dry-run] (CLI) and fdl.expire() (Python API) as the
  explicit primitive. - Automatic runs, comparable to git gc --auto: after fdl run / fdl sql when
  the command actually wrote to the catalog (detected by bracketing the latest snapshot id, so
  read-only commands stay side-effect free), and before the catalog conversion in fdl push. -
  maintenance.snapshot_retention_days = false now disables only the automatic runs; explicit fdl
  expire works regardless.

DuckLake's CHECKPOINT-driven maintenance (expire_older_than / delete_older_than options) was
  rejected: the options merely set defaults for the maintenance functions, and a checkpoint also
  flushes inlined data to parquet, defeating fdl's inlined small-table distribution.


## v0.10.3 (2026-06-10)

### Bug Fixes

- Support Typer 0.26+ in the root callback
  ([`546c9b7`](https://github.com/queria-io/fdl/commit/546c9b7f37c1431cee70132071cde5f34e1a82b2))

Typer 0.26 invokes the root callback outside an active click context when a subcommand is
  dispatched, so click.get_current_context() raised 'There is no active click context.' and broke
  every subcommand. Use the Typer-injected ctx parameter instead, and bump the dev lock to typer
  0.26.7.


## v0.10.2 (2026-06-09)

### Bug Fixes

- Support DuckDB 1.5.2 (DuckLake v1.0) SQLite catalogs
  ([`7613d59`](https://github.com/queria-io/fdl/commit/7613d593e33687153e3971dfe31fe9d75707159f))

duckdb 制約を >=1.5.2,<1.6 に更新。

build_attach_sql の SQLite 分岐で META_TYPE 'sqlite' を明示する。DuckDB 1.5 の DuckLake 拡張は bare
  'ducklake:<file>' の自動判定時に SQLite ATTACH へ storage_version を渡して 'Unsupported parameter for SQLite
  Attach: storage_version' で失敗するため。

_convert_ducklake_catalog では inline データを flush せず、ducklake_inlined_data_* テーブルを CTAS でそのままコピーして
  inline 行をカタログ内に保持する。push は DATA_PATH を公開先に書き換えるため、flush で parquet 化すると誤った場所に書かれ pull 時に欠落するのを防ぐ。

concurrency テストの attacher も SQLite では META_TYPE 'sqlite' + WAL を明示する。

### Chores

- Sync uv.lock with version 0.10.1
  ([`36f5cf5`](https://github.com/queria-io/fdl/commit/36f5cf57ebb69df94222288945d7f07dcab79724))


## v0.10.1 (2026-04-22)

### Bug Fixes

- Require SQLite local catalog; ignore legacy ducklake.duckdb
  ([`9b88ccd`](https://github.com/queria-io/fdl/commit/9b88ccd79fd413b01e5cf50795ebb8895024aceb))

catalog_path / catalog_url no longer fall back to ducklake.duckdb. pull_if_needed and do_push key
  off ducklake.sqlite only, so a legacy v0.8 workspace surfaces the standard "Run 'fdl init' or 'fdl
  pull <target>' first." error and FDL_CATALOG_URL never leaks duckdb:/// to downstream pipelines.

To migrate a legacy local catalog, run 'fdl pull <target> --force' once the remote has a catalog to
  pull from. The existing DuckDB->SQLite conversion in _convert_downloaded_catalog handles the
  rewrite.

- **dbt**: Use FDL_CATALOG_PATH in profiles.yml attach path
  ([`829a833`](https://github.com/queria-io/fdl/commit/829a833c6f9281ab7f391ecfedfbbc172ac497b9))

The DuckLake extension's ATTACH expects 'ducklake:<bare-path>', not 'ducklake:<sqlalchemy-url>'.
  Wrapping FDL_CATALOG_URL (sqlite:///...) in ducklake: produced 'ducklake:sqlite:///path' and
  dbt-duckdb treated that as a filename, raising "No such file or directory" for 'sqlite:////path'.

Use FDL_CATALOG_PATH (the bare absolute path) instead, matching how fdl sql builds its own ATTACH
  statements in build_attach_sql. FDL_CATALOG_URL remains the right variable for SQLAlchemy-style
  clients like dlt.

### Chores

- Sync uv.lock with current package version
  ([`f2db44b`](https://github.com/queria-io/fdl/commit/f2db44bf292fd8c91855990abcb2e7e24cc0306b))

### Continuous Integration

- Trigger docs rebuild after successful release
  ([`1e09465`](https://github.com/queria-io/fdl/commit/1e09465f8b7f633b6f16666c6efe6817bb3773f4))

The semantic-release step pushes the CHANGELOG.md update using GITHUB_TOKEN, so the downstream
  docs.yml workflow (which watches CHANGELOG.md) never fires. Kick docs.yml via workflow_dispatch at
  the end of release.yml; workflow_dispatch is exempt from the GITHUB_TOKEN recursion guard.


## v0.10.0 (2026-04-22)

### Chores

- Sync uv.lock with current package version
  ([`0534f4b`](https://github.com/queria-io/fdl/commit/0534f4b004ba32e105ae5b9d83070218a5856c1b))

### Features

- Redesign FDL_* environment variables around URLs and components
  ([`40ef01b`](https://github.com/queria-io/fdl/commit/40ef01b1faf8aaf1ecbde92d20002e90328f0bf1))

Replace path-oriented env vars with a URL-first, component-rich layout so pipeline code can pass
  values straight into DuckLake / dlt / dbt without scheme inference or urlparse.

Always-present keys: FDL_CATALOG_URL sqlite:///<abs>/ducklake.sqlite FDL_CATALOG_PATH absolute path
  to the catalog file FDL_DATA_URL s3://... (S3) or absolute path (local)

S3-only keys: FDL_DATA_BUCKET parsed from target URL FDL_DATA_PREFIX ends with
  ducklake.duckdb.files/ FDL_S3_* (unchanged)

BREAKING CHANGE: FDL_STORAGE, FDL_DATA_PATH, and FDL_CATALOG are removed. Use FDL_CATALOG_URL /
  FDL_CATALOG_PATH / FDL_DATA_URL instead.

### Breaking Changes

- Fdl_storage, FDL_DATA_PATH, and FDL_CATALOG are removed. Use FDL_CATALOG_URL / FDL_CATALOG_PATH /
  FDL_DATA_URL instead.


## v0.9.2 (2026-04-22)

### Bug Fixes

- Enable SQLite WAL mode and busy timeout on catalog attach
  ([`91e036a`](https://github.com/queria-io/fdl/commit/91e036a6e7b6e1aa7888ddc774663785fb681a4b))

Pass META_JOURNAL_MODE 'WAL' and BUSY_TIMEOUT 5000 as DuckLake ATTACH options for every SQLite
  catalog. META_JOURNAL_MODE is honored both on catalog creation and on re-attach, so v0.9 catalogs
  still in the default delete journal mode auto-migrate to WAL on the next FDL command. BUSY_TIMEOUT
  is per-connection and waits out the short lock windows that occur during concurrent writes,
  avoiding SQLITE_BUSY surfaces.

This finishes the v0.9 "local catalog is SQLite" switch: reads (fdl serve) and writes (fdl sql) no
  longer block each other, which was the original motivation for moving off DuckDB's exclusive file
  lock.

### Chores

- Sync uv.lock with current package version
  ([`5b4de27`](https://github.com/queria-io/fdl/commit/5b4de27a1a27b81813d95cd1fb2b8be813670eab))

### Documentation

- **dagster**: Rewrite integration guide around FDLResource and pool-based serialization
  ([`c8bf3d7`](https://github.com/queria-io/fdl/commit/c8bf3d7e416ba54c6d574a526e72712f7ec759f4))

Replace the pass-through ConfigurableResource example with an FDLResource exposing pull(), push(),
  and a get_connection() context manager that auto-pulls on entry and auto-pushes on successful
  exit. Assets now only describe the catalog transformation. Add a short section pointing S3-target
  users at Dagster concurrency pools (default_limit: 1) to avoid ETag races on parallel pushes. Drop
  the subprocess-style fdl.run example since Dagster prefers direct Python calls.

### Testing

- Pin regression guards for the pull review fixes
  ([`b8ceb9a`](https://github.com/queria-io/fdl/commit/b8ceb9a6014cb0436086b1e80ca9c0f69bf67964))

- test_pull_preserves_etag_when_conversion_fails: monkeypatches convert_duckdb_to_sqlite to raise,
  then verifies meta.json still holds the pre-pull ETag so the next pull retries instead of falsely
  reporting "Already up to date". - test_run_errors_when_target_has_no_catalog: additionally asserts
  that the misleading "pulled from <target>" log line does not appear above the catalog-missing
  error when the remote is empty.

- Pin WAL mode regression guards for SQLite catalogs
  ([`9ebe304`](https://github.com/queria-io/fdl/commit/9ebe3047083f6d799639da4be6edd27900671b63))

Assert journal_mode=wal on: - freshly created catalog from fdl init - sqlite catalog produced by the
  DuckDB -> SQLite conversion path - legacy v0.9 catalog forced back to delete mode, after any FDL
  command attaches it

Also assert that build_attach_sql() emits META_JOURNAL_MODE and BUSY_TIMEOUT for SQLite catalogs.


## v0.9.1 (2026-04-22)

### Bug Fixes

- Defer pull ETag update until catalog conversion succeeds
  ([`8d6b950`](https://github.com/queria-io/fdl/commit/8d6b950a2ac4842b757dfeec6ed9bb0443f4cc9e))

Three review findings addressed together:

1. fetch_from_s3 previously wrote the new remote ETag before _convert_downloaded_catalog ran. If the
  conversion failed (corrupt catalog, disk full, SIGKILL after unlink), the user was left with no
  local ducklake.sqlite but an updated meta.json, and the next fdl pull would report "Already up to
  date" while the project was silently back on a DuckDB local catalog. Write the ETag only after the
  conversion succeeds so failures retry on next pull.

2. pull_if_needed always returned a truthy reason string after do_pull, even when the remote was
  empty and no catalog was materialized. The caller (fdl run) then printed "No local catalog, pulled
  from TARGET" immediately before the red "No catalog" error from the catalog-missing guard. Return
  None when no catalog landed locally.

3. Add ``.tmp-shm`` to the leftovers list in _convert_ducklake_catalog. SQLite WAL mode creates -wal
  and -shm together; listing -wal without -shm was inconsistent with the stated intent of the
  cleanup block.


## v0.9.0 (2026-04-22)

### Documentation

- Drop legacy duckdb backward-compat notes from cli/working-with-data
  ([`fa32bd3`](https://github.com/queria-io/fdl/commit/fa32bd3efaaef51c5b0637f11fccce9dc48ef68d))

- Update guides for v0.9 SQLite-only local catalog
  ([`5daf2e5`](https://github.com/queria-io/fdl/commit/5daf2e5c8a641cfdd9b265490af6970f88fd9e8d))

- configuration.md: drop the ``catalog`` key row and fdl.toml example - reference/cli.md: drop
  --sqlite from fdl init; describe SQLite-first local layout and push/pull format conversion +
  data_path rewrite - integrations/dlt.md: SQLite is now the default, so dlt users no longer need
  --sqlite; simplify the setup steps - getting-started/quickstart.md: ducklake.duckdb ->
  ducklake.sqlite, and point to the new push-time data_path rewrite instead of the removed
  known-issue section

### Features

- Convert pulled catalog to local SQLite format
  ([`402d0d7`](https://github.com/queria-io/fdl/commit/402d0d7d95a0a2131d60a383ad4f159b7d85532a))

After downloading the remote DuckDB catalog, transparently convert it to ``ducklake.sqlite`` so the
  local workspace is always in the format that supports concurrent read/write. The
  ``ducklake.duckdb`` copy is removed once the conversion succeeds.

pull_from_local now takes ``target_name`` and ``project_dir`` so the conversion helper can locate
  the target directory consistently with fetch_from_s3.

- Require existing catalog before fdl push
  ([`fc7fe92`](https://github.com/queria-io/fdl/commit/fc7fe92d6e803577bfdd3cca78e55ac4938bdb65))

Match the policy already applied to ``fdl run`` / ``fdl sql`` / ``fdl duckdb``: refuse to operate
  when the target has no local catalog, and point the user at ``fdl init`` or ``fdl pull TARGET``.

Previously push would silently succeed-with-empty-payload: the SQLite -> DuckDB conversion was a
  no-op, ``_rewrite_catalog_data_path`` then opened a fresh zero-byte DuckDB file via
  duckdb.connect() and its UPDATE failed with a raw IOException, leaving a stray file behind.

BREAKING CHANGE: ``fdl push`` on an uninitialized target now exits 1 with a helpful error instead of
  succeeding with an empty upload.

- Require explicit init/pull before fdl run and fdl sql
  ([`b8c9851`](https://github.com/queria-io/fdl/commit/b8c98515efb6dc3a2d499d7b0394109967dbefa9))

Previously ``fdl run`` silently materialized an empty SQLite catalog when a target had neither local
  nor remote state, hiding typos in the target name and obscuring the distinction between "data
  lost" and "data never existed". ``fdl sql`` and ``fdl duckdb`` in the same state would surface the
  raw FileNotFoundError traceback from DuckLake.

Drop the auto-init branch from ``run_command`` and raise FileNotFoundError with a message pointing
  at ``fdl init`` or ``fdl pull TARGET``. Have the CLI wrappers (run / sync / sql / duckdb)
  translate that into a one-line red error and exit 1. Python API callers see the FileNotFoundError
  directly.

BREAKING CHANGE: ``fdl run`` no longer creates a catalog as a side effect. Users relying on the
  implicit init must now call ``fdl init`` or ``fdl pull`` before their first run/sync.

- Rewrite data_path on push from public_url
  ([`f9b8d25`](https://github.com/queria-io/fdl/commit/f9b8d25781784b8f67a2a51caff6caebe7f4f588))

Changing ``public_url`` in fdl.toml used to leave ``ducklake_metadata.data_path`` inside the catalog
  pointing at the old URL; deployments would then serve data files from the wrong origin unless
  users ran a manual UPDATE on the catalog. Push now rewrites the DuckDB copy's ``data_path`` to
  ``public_url/<datasource>/<ducklake.duckdb>.files/`` immediately after the SQLite->DuckDB
  conversion, so every upload is self-consistent with the current config.

Done on the DuckDB copy only: the local SQLite catalog is untouched, so a mid-push crash leaves
  local state intact.

Drop the corresponding entry from docs/resources/known-issues.md.

- Store local catalog as SQLite only
  ([`4718083`](https://github.com/queria-io/fdl/commit/47180837b2c5c2c30062248ced0b8cb5165b3c9c))

The local DuckLake catalog is now always ``ducklake.sqlite``. This lets multiple processes
  read/write the catalog concurrently (DuckDB file locking rejects a second opener with LockError,
  while SQLite uses OS-level locking with snapshot isolation).

Remote catalogs continue to ship as ``ducklake.duckdb``: push converts SQLite to DuckDB before
  upload, and (in a follow-up commit) pull will convert DuckDB back to SQLite locally.

BREAKING CHANGE: the ``--sqlite`` CLI flag, the ``catalog`` key in fdl.toml, and the ``sqlite``
  keyword argument on ``fdl.init`` are removed. Any ``catalog`` value left in existing fdl.toml is
  silently ignored.

### Refactoring

- Extract shared ducklake catalog conversion helper
  ([`3b7ac2e`](https://github.com/queria-io/fdl/commit/3b7ac2e834c88ef2a2cfc721baeb0b2e905234f2))

Introduce _convert_ducklake_catalog as a direction-agnostic core, and rebuild
  convert_sqlite_to_duckdb as a thin wrapper on top of it. Add convert_duckdb_to_sqlite (inverse
  direction) for use by the upcoming auto-migration and pull-side conversion paths.

### Testing

- Add concurrent read/write tests for local SQLite catalog
  ([`12f4d26`](https://github.com/queria-io/fdl/commit/12f4d268ac624030f689a9427f37dc43107e3fba))

Spawn two ``fdl sql`` processes against the same SQLite catalog to verify the v0.9 improvement.
  Under v0.8's DuckDB catalog the second opener would have failed with a LockError; SQLite's
  OS-level file locking allows snapshot-isolated concurrency.

- Strengthen concurrency tests with barrier and DuckDB negative case
  ([`94c399c`](https://github.com/queria-io/fdl/commit/94c399c38669de309bb4a4906d1a3fcf971d0b47))

Spawn two attacher subprocesses behind a ready/go file barrier so that both land inside the ATTACH
  critical section simultaneously. Add a DuckDB-side test that exercises the v0.8 failure mode
  (DuckDB's exclusive file lock triggering "Conflicting lock"); if that stops failing, the
  justification for the v0.9 SQLite switch needs a fresh look.

The previous version only ran ``fdl sql SELECT`` twice with no synchronization, so overlap was
  incidental and there was no evidence that DuckDB would actually have failed.

### Breaking Changes

- ``fdl push`` on an uninitialized target now exits 1 with a helpful error instead of succeeding
  with an empty upload.


## v0.8.0 (2026-04-17)

### Chores

- Sync uv.lock with pyproject version 0.7.1
  ([`86d3a1c`](https://github.com/queria-io/fdl/commit/86d3a1cf21ea3bc8e65bbb6de6f3d2476cf35759))

### Documentation

- Describe ETag-based push conflict detection
  ([`67922dc`](https://github.com/queria-io/fdl/commit/67922dc8126448b372a74b7ff10daf13b42abe5f))

Replace the .fdl/meta.json timestamp description in the push section with the new If-Match
  precondition flow and clarify that local (non-S3) targets skip conflict detection.

### Features

- Use ETag + If-Match for push conflict detection
  ([`07aeaf0`](https://github.com/queria-io/fdl/commit/07aeaf0a702bd595891d496de49a71b78c2d17a3))

Replace the client-generated pushed_at JSON file with server-side ETag preconditions on the catalog
  object (put_object + If-Match / If-None-Match). The S3 server evaluates the precondition
  atomically, closing the TOCTOU race between check and write, removing client clock-drift
  sensitivity, and detecting direct catalog mutations such as set_option.

Local (non-S3) targets skip conflict detection entirely. The remote .fdl/meta.json object is no
  longer written or read. Legacy state files containing only {"pushed_at": ...} are treated as no
  record on read.

BREAKING CHANGE: existing users must run `fdl pull` once after upgrading, or pass `--force` on the
  next push, to initialize the new ETag-based local state.

- **cli**: Add fdl duckdb for interactive shells
  ([`2ed08b0`](https://github.com/queria-io/fdl/commit/2ed08b0126a20d6d56d408f0499566501fc44803))

Add `fdl duckdb TARGET [--read-only] [--force] [--dry-run] [--duckdb-bin PATH]` that resolves the
  target, performs the stale catalog check, builds the INSTALL / ATTACH / USE init SQL (including
  INSTALL httpfs + CREATE SECRET for S3 targets), and execs into the DuckDB CLI via os.execvp so
  that TTY, signals, and exit code are inherited. --dry-run prints shlex.join(argv) instead of
  execing.

Extract the init SQL builder as build_attach_sql() in fdl.ducklake and reuse it from
  fdl.ducklake.connect(). Paths and credentials embedded in SQL literals are single-quote escaped.
  Move shared moto/s3_project fixtures to tests/integration/conftest.py.

### Breaking Changes

- Existing users must run `fdl pull` once after upgrading, or pass `--force` on the next push, to
  initialize the new ETag-based local state.


## v0.7.1 (2026-04-16)

### Bug Fixes

- Update connect function to use catalog_path and simplify ducklake_path logic
  ([`3f56968`](https://github.com/queria-io/fdl/commit/3f5696892a2b996164d1057785cc15ed969f6223))

### Refactoring

- Storage URL handling and add remote meta key function
  ([`5638d2f`](https://github.com/queria-io/fdl/commit/5638d2f4523015f8d97b626516c469aa05e5eb50))


## v0.7.0 (2026-04-16)

### Bug Fixes

- **api**: Propagate push conflicts, move init rollback into API
  ([`c0206a3`](https://github.com/queria-io/fdl/commit/c0206a369e7d466b2763e800a6bb66c4e99e4cf1))

- fdl.push now raises PushConflictError instead of calling SystemExit, so Dagster/CI code can catch
  it. CLI wraps push/sync in try/except and translates to SystemExit(1). - fdl.init rolls back
  fdl.toml and the partially created .fdl/ directory itself when init_ducklake fails. CLI no longer
  needs its own rollback block. - Unified Raises sections across fdl.pull, fdl.push, fdl.run,
  fdl.sync, and fdl.connect docstrings. - Documented that fdl run / fdl sync execute the subprocess
  with the project root as cwd (was implicit in the feat/python-api series).

BREAKING CHANGE: fdl run (and fdl sync) now run the pipeline subprocess with the directory
  containing fdl.toml as its working directory, rather than the caller's cwd. Pipelines that relied
  on the caller's cwd for relative paths should adjust.

### Chores

- Sync uv.lock with pyproject version bump
  ([`68868c7`](https://github.com/queria-io/fdl/commit/68868c759a91761f7ecad6a423478786f38fcdd3))

### Documentation

- Document the Python API and Dagster integration
  ([`7b65815`](https://github.com/queria-io/fdl/commit/7b65815ca0107248d12bc05e07ec2b5611823066))

Add docs/reference/python-api.md (mkdocstrings-rendered reference for the six entry points) and
  docs/integrations/dagster.md (asset, ConfigurableResource, and run-script patterns). Mention the
  Python API in the home page, quickstart, README, and CLI reference, and note the fdl.toml walk-up
  lookup and fdl.init's non-idempotent behavior where relevant.

### Features

- Add Python API mirroring CLI commands
  ([`ac70ff0`](https://github.com/queria-io/fdl/commit/ac70ff0fce3fdc07af2d24bba325dd545d194368))

Expose fdl.init, fdl.pull, fdl.push, fdl.run, fdl.sync, and fdl.connect at the top of the fdl
  package so pipelines (e.g. Dagster assets) can drive DuckLake catalogs without spawning a CLI
  subprocess. Each function mirrors its CLI counterpart and accepts an optional project_dir keyword
  that falls back to find_project_dir() when omitted. __all__ is set so IDE completion and strict
  re-export checkers treat these six names as the public surface.

### Refactoring

- Thread project_dir through internal modules
  ([`2516d66`](https://github.com/queria-io/fdl/commit/2516d669dc02ea2bb33838020964ddec2c4e4a50))

Add an optional project_dir keyword to do_pull, pull_if_needed, do_push, run_command,
  ducklake.connect, and the meta helpers so every internal call-chain can resolve fdl.toml against
  an explicit project root instead of Path.cwd(). run_command also inherits project_dir as the
  subprocess cwd. No behavior change when project_dir is not provided — find_project_dir returns the
  nearest ancestor with fdl.toml, matching the previous cwd assumption.

- **cli**: Delegate CLI handlers to the public Python API
  ([`1f58600`](https://github.com/queria-io/fdl/commit/1f586001aab5fcfaebb349f883f8416604d5ba02))

Rewrite fdl pull, push, run, sync, and init as thin wrappers around the new fdl.pull / fdl.push /
  fdl.run / fdl.sync / fdl.init entry points. Keep CLI-only responsibilities (interactive prompts,
  ValueError -> BadParameter conversion, exit-code transport) in cli.py, and move the "--- pull: ...
  ---" / "Already up to date" / "skipping push" console messages into the API so Python callers see
  the same output.

- **config**: Auto-detect project_dir by walking up to find fdl.toml
  ([`2342b27`](https://github.com/queria-io/fdl/commit/2342b2783d2f97322e68bcb040dfbd6a622a20c6))

Add find_project_dir() and switch default resolution in datasource_name, catalog_type,
  resolve_target, target_s3_config, target_public_url, target_command, and project_config_path from
  Path.cwd() to the nearest ancestor that contains fdl.toml. Enables running fdl from subdirectories
  and lets the upcoming Python API be called from any cwd.

### Testing

- Cover Python API and find_project_dir walk-up behavior
  ([`b7818be`](https://github.com/queria-io/fdl/commit/b7818bec263de4221b193bd15021d7e2e246f167))

Add tests for the six public entry points (init/pull/push/run/sync/connect), including a local
  roundtrip, cross-cwd use with explicit project_dir, required target arg, exit-code handling,
  sync-skip-on-failure, the fdl.toml command fallback, connection close on context exit, and the
  __all__ surface. Cover find_project_dir with cwd, walk-up, closest-match, missing-file, and
  explicit-start cases.

### Breaking Changes

- **api**: Fdl run (and fdl sync) now run the pipeline subprocess with the directory containing
  fdl.toml as its working directory, rather than the caller's cwd. Pipelines that relied on the
  caller's cwd for relative paths should adjust.


## v0.6.0 (2026-04-04)

### Features

- Add fdl sync command
  ([`777528c`](https://github.com/queria-io/fdl/commit/777528c0579b19306e067fb6a6bc1e6105504325))

Combines run and push into a single command to prevent forgetting to push after running a pipeline.

Both fdl run and fdl sync read command from fdl.toml when no explicit -- COMMAND is given. Lookup
  order: targets.<name>.command → top-level command.

- **run**: Auto-pull catalog before command execution
  ([`4045f61`](https://github.com/queria-io/fdl/commit/4045f615941156d996cdf631b279ecf0680f013f))

fdl run でコマンド実行前にカタログの同期状態を確認し、 必要に応じて自動的に pull する。

判定条件: - ローカルカタログが存在しない - meta.json がない (pull/push されたことがない) - リモートの方が新しい (pushed_at 比較)

### Refactoring

- **pull**: Extract do_pull/pull_if_needed and add --force flag
  ([`128d063`](https://github.com/queria-io/fdl/commit/128d06349d980ddf26a98bddc8015848c738c564))

pull コマンドのS3/ローカル分岐ロジックを do_pull() に抽出し、 同期判定ロジックを pull_if_needed() に切り出した。

pull はデフォルトで最新の場合スキップし、--force で強制再ダウンロード。


## v0.5.7 (2026-04-02)

### Bug Fixes

- Use CREATE SECRET for S3 configuration in DuckDB
  ([`410fac3`](https://github.com/queria-io/fdl/commit/410fac3ea06667b15c0bbe98a37b3be619eb962c))


## v0.5.6 (2026-04-02)

### Bug Fixes

- Log catalog path on every fdl run
  ([`1e04295`](https://github.com/queria-io/fdl/commit/1e04295b33f4cb6fd1f9dba8baa39542d4f6eae5))

- Set PYTHONUNBUFFERED environment variable in run command
  ([`ea07bf7`](https://github.com/queria-io/fdl/commit/ea07bf708e7b51db9a948114cce9ff96ecca8e50))


## v0.5.5 (2026-04-02)

### Bug Fixes

- Support sqlite catalog type in fdl run auto-init
  ([`eb7d6f7`](https://github.com/queria-io/fdl/commit/eb7d6f78b069bc2135ba00df22e5c3c8929df399))

Read catalog type from fdl.toml to correctly initialize sqlite catalogs for dlt-based datasets. Also
  fall back to fdl.toml when auto-detecting catalog path for new targets.

### Chores

- Sync uv.lock
  ([`171f4b9`](https://github.com/queria-io/fdl/commit/171f4b922ddc2516823dd2e11b479a4b0305e305))


## v0.5.4 (2026-04-02)

### Bug Fixes

- Auto-initialize catalog on first fdl run for a target
  ([`71f2c49`](https://github.com/queria-io/fdl/commit/71f2c497b0b1611ec3cbc25d6299ffd89920f9ff))

When fdl run is invoked for a target that has no catalog yet, initialize it automatically so
  pipelines and dbt can attach without requiring a separate fdl init step.


## v0.5.3 (2026-04-02)

### Bug Fixes

- Ensure target catalog directory exists in fdl run
  ([`66868df`](https://github.com/queria-io/fdl/commit/66868df18bd8e0bb1fe69e77be2bb8fdc96609fd))

Create .fdl/{target}/ before subprocess execution so DuckLake ATTACH can create the catalog file on
  first run. Also respect FDL_CATALOG env var in connect() for pipelines that hardcode target_name.

- Respect FDL_CATALOG env var in connect()
  ([`577d924`](https://github.com/queria-io/fdl/commit/577d924419cd22cb7465c3daca57d0c5b8045f2d))

When running under fdl run, FDL_CATALOG is set to the correct target-specific path. Pipelines that
  hardcode target_name in connect() calls now use the env var instead.


## v0.5.2 (2026-04-02)

### Bug Fixes

- Isolate .fdl catalog directory per target
  ([`1b889bf`](https://github.com/queria-io/fdl/commit/1b889bfb53bb29cf1a2b81e9690731cb9f48b893))

Each target (default, local, etc.) now gets its own subdirectory under .fdl/ to prevent catalog
  state conflicts when switching between targets.

.fdl/ducklake.duckdb → .fdl/{target}/ducklake.duckdb .fdl/meta.json → .fdl/{target}/meta.json

### Chores

- Add Google Analytics to documentation site
  ([`bac7f24`](https://github.com/queria-io/fdl/commit/bac7f24a6477b6f976c93c427ff61cdcc29c0dac))

### Continuous Integration

- Fix docs workflow to watch zensical.toml instead of mkdocs.yml
  ([`6378ed3`](https://github.com/queria-io/fdl/commit/6378ed32e562967112b2d41fdffbc62d220d8282))

### Documentation

- Add semantic-release versioning rules to CLAUDE.md
  ([`dc393ad`](https://github.com/queria-io/fdl/commit/dc393ad112b08048f2bb96cb078847456ec563f6))

- Refine "Why fdl" section for clarity and conciseness
  ([`1f63113`](https://github.com/queria-io/fdl/commit/1f63113fbfaaf4ac6e971fb2ac5953ed67ffe10d))

- Rewrite README with quick start workflow and feature overview
  ([`8627c14`](https://github.com/queria-io/fdl/commit/8627c140242020b678a03a3d29714b501888b8d4))

- Update .fdl paths to reflect target-based layout
  ([`82f4872`](https://github.com/queria-io/fdl/commit/82f487277024878d6f538dc5af59ee21cb982fc5))

### Testing

- Update path assertions for target-based .fdl layout
  ([`a00c417`](https://github.com/queria-io/fdl/commit/a00c417eb342f594ccb46421ab667813bbf8f361))


## v0.5.1 (2026-03-30)

### Bug Fixes

- Correct formatting in CLI reference table
  ([`e6de3e8`](https://github.com/queria-io/fdl/commit/e6de3e8501dac9b03fd60ac1c5c07fa791aa4334))

### Chores

- Sync uv.lock with pyproject.toml version
  ([`1c4dbea`](https://github.com/queria-io/fdl/commit/1c4dbea558daeba73b95fe8b3e35a66b27b49d71))

### Documentation

- Update README formatting for documentation link
  ([`1fb8e20`](https://github.com/queria-io/fdl/commit/1fb8e2060efb293b39d2ead5d06337ee080abb35))

### Refactoring

- Replace checkpoint command with stale catalog check in fdl sql
  ([`677f23f`](https://github.com/queria-io/fdl/commit/677f23fe15b869a6e22befbff9140ab4f07797fd))


## v0.5.0 (2026-03-29)

### Build System

- Simplify dependencies and add project metadata
  ([`946e2ba`](https://github.com/queria-io/fdl/commit/946e2baf2bcf89aaaec35bc4c005bfb84d46636a))

Remove dbt-core, pydantic, jinja2, pyyaml dependencies. Add MIT license, project URLs, and authors
  to pyproject.toml. Move zensical from docs dependency group to dev.

### Chores

- Remove fdl_common dbt macros package
  ([`605a3d4`](https://github.com/queria-io/fdl/commit/605a3d403ce90ebd94c36318faa39ac16b99301e))

### Continuous Integration

- Update docs workflow to use zensical
  ([`330ab5c`](https://github.com/queria-io/fdl/commit/330ab5cc90fbfe752a7cb26d2e1bcc0d28b27594))

- Update docs workflow to use zensical
  ([`420b05b`](https://github.com/queria-io/fdl/commit/420b05b0310ebeef0bf0618a3516f16f34345f11))

### Documentation

- Add concept pages and global install instructions
  ([`35180ad`](https://github.com/queria-io/fdl/commit/35180ad2ecb0c4e3404267cd7ad148b570d2dd46))

- Add "Why fdl" page explaining the Frozen DuckLake pattern and motivation - Add Roadmap page with
  vision toward a package manager for Frozen DuckLake - Add global install instructions (uv tool /
  pipx) to quickstart

- Migrate from mkdocs to zensical
  ([`5b8359c`](https://github.com/queria-io/fdl/commit/5b8359c4e599bceeecd7b5f3fb24403746e3ccda))

Replace mkdocs-material with zensical as the documentation engine. Replace changelog symlink with
  pymdownx.snippets include.

- Rewrite documentation for target-based architecture
  ([`e48b8e3`](https://github.com/queria-io/fdl/commit/e48b8e3df00d07b30042f0a3f430e762fb460690))

Restructure docs to match new target-based config model. Migrate from mkdocs to zensical. Add guide,
  reference, and resources sections with new content.

- Update CLAUDE.md and add CONTRIBUTING.md
  ([`28d12f1`](https://github.com/queria-io/fdl/commit/28d12f1afe85d94f2249ca36c9b6c54b2c8e95fa))

- Update README, CLAUDE.md, and CONTRIBUTING.md
  ([`0c48dff`](https://github.com/queria-io/fdl/commit/0c48dff5f858e2a334a8c6744020ccb232949c85))

### Features

- Add --version flag, improve gc output
  ([`2046a11`](https://github.com/queria-io/fdl/commit/2046a11e5fdd8d6dc01949bc58ec01129bd200b0))

- Add push conflict detection and --force flag
  ([`3db0216`](https://github.com/queria-io/fdl/commit/3db0216650bc4b78479230c376de61cc37eab534))

- Track pushed_at timestamp in .fdl/meta.json (local + remote) - Push rejects when remote was
  updated since last pull - --force overrides conflict detection - Pull syncs meta.json from remote
  for next push check

- Rewrite to target-based architecture
  ([`a17d8ec`](https://github.com/queria-io/fdl/commit/a17d8ecd6648681b0acf5426f232ae00252f5abe))

Replace 3-layer config (env → workspace → user) with single fdl.toml and ${VAR} environment variable
  expansion.

BREAKING CHANGE: - `remote` renamed to `target` across all commands - `FDL_ATTACH_PATH` renamed to
  `FDL_CATALOG` - `gc` command renamed to `prune` - `fdl run` now requires TARGET argument -
  `metadata` command removed - `create_destination()` removed from ducklake module - User/workspace
  config files (~/.fdl/config, .fdl/config) no longer read

New features: - `fdl sql TARGET QUERY` — execute SQL against the catalog - `fdl init` prompts
  interactively for target config - S3Config dataclass for typed credential handling

### Refactoring

- Make load_toml strict and remove datasource_name fallback
  ([`63ca8ed`](https://github.com/queria-io/fdl/commit/63ca8ed895b6bfde79b460dcb6afa209cd1ab958))

- load_toml → _load_toml: internal function, raises FileNotFoundError - set_value: catch
  FileNotFoundError for new file creation - datasource_name: require name in fdl.toml, no directory
  name fallback

- Replace print with rich console output
  ([`3958b6b`](https://github.com/queria-io/fdl/commit/3958b6b727226cfc4ca85fcc846136927549c387))

- Replace prune with checkpoint command
  ([`6433bcd`](https://github.com/queria-io/fdl/commit/6433bcdd3c59ace27cea5588e57956477d069c7a))

- Use DuckLake CHECKPOINT statement via connect() context manager - Remove S3-only restriction,
  works on any target - Extract is_stale pure function and read_remote_pushed_at to meta.py - Add
  stale catalog check before maintenance (reuses push conflict detection)

- Unify branding to Frozen DuckLake
  ([`cd148ca`](https://github.com/queria-io/fdl/commit/cd148ca6ff0f745ef881cabe8173d64e694cee98))

- Rename "Frozen Data Lake" to "Frozen DuckLake" across all files - Normalize casing: FDL → fdl -
  Remove "Git-like" messaging - Update site_name to "fdl — Frozen DuckLake CLI"

### Testing

- Add FDL_DATA_PATH injection test
  ([`2644ae9`](https://github.com/queria-io/fdl/commit/2644ae9378446c89f6bdbcfaa02187bd1cf479e1))

Verify fdl run injects FDL_DATA_PATH ending with ducklake.duckdb.files/

- Add integration and unit tests (89 tests)
  ([`1d01ffe`](https://github.com/queria-io/fdl/commit/1d01ffeec8e2f0580bc6310c36b8a171b6e3bb23))

Integration tests (tests/integration/): - test_init: default catalog, invalid name, existing toml,
  double init, rollback, sqlite - test_config: get, set, list-all, env var reference, s3
  credentials, missing key, without init - test_sql: data persistence, invalid sql - test_run: env
  injection, no-overwrite, separator, exit code propagation - test_push: catalog copy, sqlite
  conversion, meta.json, conflict detection, force push - test_pull: catalog restore, meta.json
  sync, pull-then-push, empty target - test_prune: local target rejection, orphan deletion, dry-run
  - test_serve: CORS, range request, HEAD, OPTIONS, 404

Unit tests (tests/): - test_config: set_value, get_all, datasource_name, storage/data_path/
  catalog_path, resolve_target, target_s3_config, ducklake_url, fdl_env_dict - test_init_module:
  ducklake_data_path, default_target_url - test_s3: S3Config endpoint_host - test_serve:
  CORSRangeHandler

- Add Phase 1 pure function tests
  ([`a118610`](https://github.com/queria-io/fdl/commit/a118610fb8348579555c945967c9020de3ab6ebe))

- test_init_module.py: ducklake_data_path, default_target_url - test_s3.py: S3Config endpoint_host

- Add S3 target integration tests for push and pull
  ([`c6caf1d`](https://github.com/queria-io/fdl/commit/c6caf1ddea4a0fa15c0faaab8bc270e6024bf808))

- push uploads catalog to S3 - push conflict detection on S3 - pull restores catalog from S3


## v0.4.0 (2026-03-27)

### Continuous Integration

- Add GitHub Pages deployment workflow
  ([`ebad8ba`](https://github.com/queria-io/fdl/commit/ebad8ba244a1846a626df2da03db59c84a9d57b4))

Build and deploy docs on push to main (docs/**, mkdocs.yml, CHANGELOG.md) and manual dispatch.

### Documentation

- Add MkDocs Material documentation site
  ([`7f00206`](https://github.com/queria-io/fdl/commit/7f00206a60c1eb6cf098d5d80db8283c5ed4a051))

- mkdocs.yml with Material theme (light/dark, code copy, search) - 6 pages: index, quickstart, CLI
  reference, configuration, dlt integration, changelog - Changelog page symlinked to CHANGELOG.md -
  Update README.md with project overview and commands - Add site/ to .gitignore

- Update GitHub Pages actions and set custom site URL
  ([`5fe951b`](https://github.com/queria-io/fdl/commit/5fe951b03bc0cdac6aca1e890c2ded9026066dca))

### Features

- Add fdl gc command with --dry-run support
  ([`9e5d451`](https://github.com/queria-io/fdl/commit/9e5d451f53c1ffa488ecab564c6a0460d32f859b))

fdl gc origin --dry-run # list orphaned files and sizes fdl gc origin # interactive deletion with
  confirmation fdl gc origin --force # delete without confirmation fdl gc origin --older-than 7 #
  only files older than 7 days


## v0.3.3 (2026-03-27)

### Bug Fixes

- Include fdl.toml in config value resolution
  ([`2e6c921`](https://github.com/queria-io/fdl/commit/2e6c921e4835f94d54f47ff889809abf1f2ec5dc))

_get_config_value now checks project config (fdl.toml) before workspace and user config, matching
  resolve_remote's 3-layer lookup.


## v0.3.2 (2026-03-26)

### Bug Fixes

- Expand env vars in remote URLs
  ([`38341e3`](https://github.com/queria-io/fdl/commit/38341e30a2c131630b4eecfa96d28e0a84ca841c))

resolve_remote() now calls os.path.expandvars() so that fdl.toml remotes like s3://${FDL_S3_BUCKET}
  are expanded.


## v0.3.1 (2026-03-26)

### Bug Fixes

- Remove .fdl/ pre-check from pull command
  ([`4e2023b`](https://github.com/queria-io/fdl/commit/4e2023be8cf8f9e2d6d513014855134d6d520fd7))

pull.py already creates .fdl/ via mkdir(parents=True). The check broke CI where .fdl/ is gitignored
  and doesn't exist before pull.


## v0.3.0 (2026-03-26)

### Chores

- Bump frozen-ducklake to 0.2.1
  ([`b359533`](https://github.com/queria-io/fdl/commit/b3595338fa0651506c8778d32f094b7f9385d1df))

### Features

- Require fdl init before pull, remove --sqlite fallback
  ([`b21369e`](https://github.com/queria-io/fdl/commit/b21369e0d06a9977e4e2d4bba90661f551dcd01d))

pull no longer auto-initializes the catalog. Users must run fdl init first. Removes --sqlite option
  from pull.

- Rewrite fdl init with fdl.toml scaffolding and rollback
  ([`e5e6454`](https://github.com/queria-io/fdl/commit/e5e645457f4ed0ac65cbc04f9909fee9bc43805c))

- name argument required (like git init <repo>) - generates fdl.toml with name and optional catalog
  type - auto-creates .gitignore entry for .fdl/ - rolls back fdl.toml and .fdl/ on failure -
  set_value now auto-detects top-level vs sectioned keys

### Refactoring

- Move datasource/URL resolution from DatasetConfig to config module
  ([`6ff1ff2`](https://github.com/queria-io/fdl/commit/6ff1ff224eac627954cc4dd702cdae6f855eec09))

datasource_name(), public_url(), ducklake_url() are now in config.py with 3-layer resolution (env
  var → workspace → user config). DatasetConfig no longer holds public_url or ducklake_url.

- Resolve storage in create_destination, add FDL_S3_ENDPOINT_HOST
  ([`26bcec8`](https://github.com/queria-io/fdl/commit/26bcec89dcfc0a946602f2db9ef5c6ad96325091))

- create_destination defaults to config.storage() instead of hardcoded .fdl - s3_env_dict now
  derives FDL_S3_ENDPOINT_HOST (scheme-less) for DuckDB


## v0.2.1 (2026-03-26)

### Bug Fixes

- Update outdated docstring in create_destination
  ([`ad31665`](https://github.com/queria-io/fdl/commit/ad31665cf4cf9cd02bd4649ac45f16c2153629f4))

### Continuous Integration

- Merge publish into release workflow
  ([`22ba582`](https://github.com/queria-io/fdl/commit/22ba582fab145d68240605c9b68a9571492e6161))

GITHUB_TOKEN tags don't trigger other workflows. Run PyPI publish in the same job after
  semantic-release.

- Switch release to manual trigger (workflow_dispatch)
  ([`0186734`](https://github.com/queria-io/fdl/commit/01867344bcb931c7436407caefbf522afeaba22f))


## v0.2.0 (2026-03-26)

### Bug Fixes

- Allow zero version in semantic-release
  ([`074baae`](https://github.com/queria-io/fdl/commit/074baaea2e41eff15f74758c8d1e094d0d1e87d1))

- Use major_on_zero=false to stay in 0.x
  ([`edbc322`](https://github.com/queria-io/fdl/commit/edbc32267ec828ab21891981e286bc42a230d93d))

### Chores

- Update uv.lock
  ([`1331865`](https://github.com/queria-io/fdl/commit/1331865a39543392f2b74a83ce0fcc476259b1f4))

### Features

- Named remotes, fdl run/config/serve, FDL_* env vars
  ([`d749935`](https://github.com/queria-io/fdl/commit/d749935fafa493f6d5921876a51485c58f06b338))

BREAKING CHANGE: push/pull now require named remotes instead of URLs. s3_url removed from
  dataset.yml, replaced by fdl.toml remotes. DUCKLAKE_STORAGE renamed to FDL_STORAGE. S3 env vars
  prefixed with FDL_S3_.

Named remotes: - push/pull require explicit remote name (e.g. origin, local) - Remotes defined in
  fdl.toml (project), .fdl/config (workspace), ~/.fdl/config (user) - No built-in remotes; all
  user-defined via fdl config

fdl run: - Sets FDL_STORAGE, FDL_DATA_PATH, FDL_ATTACH_PATH for pipeline execution - S3 credentials
  loaded from config (env var → workspace → user) - Usage: fdl run [REMOTE] -- COMMAND

fdl config: - git config-like settings management - fdl config key value (user), fdl config --local
  key value (workspace)

fdl serve: - HTTP server with CORS + Range request support - Optional remote arg: fdl serve (project
  .fdl/) or fdl serve REMOTE

Other changes: - DIST_DIR renamed to FDL_DIR - S3 endpoint now stored with https:// scheme - gc.py:
  add OVERRIDE_DATA_PATH for v1.0 compatibility

### Breaking Changes

- Push/pull now require named remotes instead of URLs. s3_url removed from dataset.yml, replaced by
  fdl.toml remotes. DUCKLAKE_STORAGE renamed to FDL_STORAGE. S3 env vars prefixed with FDL_S3_.


## v0.1.0 (2026-03-22)

### Features

- Replace tagpr with python-semantic-release
  ([`c8e72ed`](https://github.com/queria-io/fdl/commit/c8e72eda39bc0cf978c9f32914b5122c6ab5dd24))

- Update release workflow and remove changelog
  ([`76eceb8`](https://github.com/queria-io/fdl/commit/76eceb876fbb0d262ba62c774077847c8fa60c62))
