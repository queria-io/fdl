# fdl

Versions of a [DuckLake](https://ducklake.select/) catalog on object storage, kept the way Git keeps commits.

Each version is a frozen catalog with a record of its parent, author and message. Refs such as `refs/heads/main` say which version readers get. Data files are shared between versions and never rewritten. All it needs is a store that can read, write and write on condition: a folder, S3, R2 or anything S3-compatible.

- [Specification](spec.md): the layout, `version.json`, and the rules for refs and retention
- [Command line](cli.md): `fdl init`, `checkout`, `commit`, `log`, `url`, `expire`

```bash
pip install 'frozen-ducklake[s3]'
```
