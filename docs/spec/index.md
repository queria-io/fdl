---
title: Specification
sidebar:
  label: Overview
description: The normative description of FDL 0.1 (draft) - what it decides, what it leaves to hosts, and the terms it uses.
---

This is the specification of FDL 0.1, a draft. Tools are written against this specification, and where a tool and this text disagree, the text is changed first and the tool follows.

FDL is a storage layout for keeping versions of [DuckLake](https://ducklake.select/) metadata, modelled on how Git stores commits. It says which files go where in a store and how readers and writers handle them. No server is needed: the store has to read, write and write on condition.

"Must" is required. "May" is optional. "Should" is recommended, and a tool that does otherwise has a reason.

## What it decides

- the layout of a repository, and the name and content of each file ([Layout](/spec/layout))
- the fields of a version record and the frozen metadata ([Versions](/spec/versions))
- the tables of the `_fdl` schema in the metadata: what a dataset, its tables and its columns mean, and the primary keys, assets and other repositories FDL reads ([The _fdl schema](/spec/fdl-schema))
- the content of refs and the rules for moving them ([Refs](/spec/refs))
- when shared objects (data files and assets) may be deleted, and how a version is taken out of retention or withdrawn ([Data files](/spec/data-files))
- how a reader finds and opens a version, and checks who made it ([Reading](/spec/reading))
- the order of writes, and when a push is refused ([Writing](/spec/writing))
- the requests a host answers to take pushes over HTTP ([Pushing over HTTP](/spec/http))

## What it leaves to hosts

- who can read a repository, and who may move `main`. Everything in one repository is readable by the same people
- authentication, URL shapes and retention defaults
- the content of the DuckLake metadata itself, which is DuckLake's specification
- how to show the dataset's documents and the attributes of `_fdl.attributes` meant for it

The format names no host and no service. Fields a host adds go where the specification leaves room for them.

## Terms

| Term | Meaning |
| --- | --- |
| store | Where a repository is kept: a bucket, or a folder |
| repository | Everything under one prefix of a store (the root) |
| root | The prefix, ending in `/`. Paths in this specification are relative to it |
| metadata | DuckLake's record of the tables, their snapshots and their data files, kept in a database (DuckLake calls it the catalog database) |
| version | One frozen metadata file and its record. Never changed once written |
| ref | A file that holds a version ID. Branches, tags and requests are refs |
| working metadata | A writer's own DuckLake metadata, in SQLite. Never in the repository |
| data file | A Parquet file DuckLake writes, shared between versions |
| host | A service that keeps repositories and controls access to them |
| asset | A file the rows of a table point at by its sha256, such as a PDF or an image collected as data |
| `_fdl` | The schema in the metadata whose tables say what FDL needs beyond DuckLake's structure, and what the dataset means to people |
| grace period | How long a shared object no version reaches is kept before it may be deleted. 7 days unless a host says otherwise |

A DuckLake snapshot is to a version what a small commit on a work branch is to the squash commit that lands on `main`. Snapshots are made on every write to the working metadata. A frozen metadata keeps only its last snapshot, so the unit a reader can go back to is the version, read from its own metadata.
