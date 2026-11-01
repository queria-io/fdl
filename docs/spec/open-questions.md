---
title: Open questions
description: What this draft of the specification does not settle yet.
---

These are not part of FDL 0.1 yet. Tools must not rely on any answer below until it moves into the specification.

## A list of refs for HTTP readers

A reader over plain HTTP cannot list the store, so it cannot find branches and tags it does not already know by name. Git's dumb HTTP protocol answers this with `info/refs`. FDL could do the same with a file at the root that lists every ref and the version it holds, rewritten whenever a ref moves. The open points are its name and content, and who keeps it up to date when writers move refs themselves.

## Characters in ref names

Ref names are lower-case ASCII today. Names in other scripts (a dataset maintained in Japanese, say) would need a rule for normalisation and for how they are written in paths and URLs.

## Merges

`parents` can hold two IDs, but this specification gives no way to make such a version. DuckLake has no notion of the same table in two catalogs, so merging rows cannot be done safely from the catalogs alone. This waits for DuckLake's own work on branches.

## A compatibility test suite

A set of repositories and expected results that a reader or writer can be tested against: valid and invalid `version.json`, refs that fail their condition, expired versions, catalogs in several formats.

## Withdrawal

Retention removes catalogs but keeps every `version.json` and leaves data files named by live versions. Removing data entirely, for example when personal data was published by mistake, needs more: a `reason: withdrawn`, rewriting the Parquet files of live versions that hold the data, and a way to tell mirrors, which keep their own copies.

## Signatures

Anyone who can write to a store can write a `version.json`. A reader of a mirror cannot tell whether a version came from the origin. One answer is an optional signature beside the record, such as `versions/<ID>/version.json.sig`, made with a key the origin publishes.

## Retention defaults

How a repository declares how long it keeps versions (a count, a number of days, or both) in `fdl.json`, so that maintenance tools agree.

## Newer formats from an older working catalog

[DuckLake formats](/internals/ducklake-formats) shows how to write an older format from DuckDB 2.0. Writing `catalogs/<newer>.duckdb` from a working catalog kept in the oldest declared format is not yet described.
