"""FDL: versions of a DuckLake catalog, kept the way Git keeps commits.

The format is written down in ``docs/spec.md``. This package is its reference
implementation: a repository lives in a local folder or any S3-compatible
bucket that takes conditional writes, and every function here works on one.
Services that host repositories call this package the way ``gh`` calls
``git``, so nothing in here knows about any of them.

    catalog   a DuckLake catalog in its two formats, and the conversion
    layout    where each file of a repository sits
    version   the record of a version (version.json) and its ID
    refs      reading and moving refs under their conditions
    repo      init, commit, push, log, expire: the operations, without a terminal
    store     what this package needs from the storage underneath
    stores    a local folder and S3 as stores
"""
