---
title: Specification
sidebar:
  label: Overview
description: The normative description of FDL 0.1 (draft) - what it decides, what it leaves to hosts, and the terms it uses.
---

This is the specification of FDL 0.1, a draft. It changes as the tools that implement it are built. Tools are written against this specification, and where a tool and this text disagree, the text is changed first and the tool follows.

FDL is a storage layout for keeping versions of a [DuckLake](https://ducklake.select/) catalog, modelled on how Git stores commits. It says which files go where in a store and how readers and writers handle them. No server is needed: the store has to read, write and write on condition.

"Must" is required. "May" is optional. "Should" is recommended, and a tool that does otherwise has a reason.

## What it decides

- the layout of a repository, and the name and content of each file ([Layout](/spec/layout))
- the fields of a version record and the frozen catalog ([Versions](/spec/versions))
- the content of refs and the rules for moving them ([Refs](/spec/refs))
- when data files may be deleted, and how a version is taken out of retention or withdrawn ([Data files](/spec/data-files))
- how a reader finds and opens a version, and checks who made it ([Reading](/spec/reading))
- the order of writes, and when a push is refused ([Writing](/spec/writing))
- the requests a host answers to take pushes over HTTP ([Pushing over HTTP](/spec/http))

## What it leaves to hosts

- who can read a repository, and who may move `main`. Everything in one repository is readable by the same people
- authentication, URL shapes and retention defaults
- the content of the DuckLake catalog itself, which is DuckLake's specification

The format names no host and no service. Fields a host adds go where the specification leaves room for them.

## Terms

| Term | Meaning |
| --- | --- |
| store | Where a repository is kept: a bucket, or a folder |
| repository | Everything under one prefix of a store (the root) |
| root | The prefix, ending in `/`. Paths in this specification are relative to it |
| version | One frozen catalog and its record. Never changed once written |
| ref | A file that holds a version ID. Branches, tags and requests are refs |
| working catalog | A writer's own DuckLake catalog, in SQLite. Never in the repository |
| data file | A Parquet file DuckLake writes, shared between versions |
| host | A service that keeps repositories and controls access to them |
| grace period | How long an unnamed data file or an unlisted attachment is kept before it may be deleted. 7 days unless a host says otherwise |

A DuckLake snapshot is to a version what a small commit on a work branch is to the squash commit that lands on `main`. Snapshots are made on every write and stay inside the catalog. The unit shown to people is the version.
