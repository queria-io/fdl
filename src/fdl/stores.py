"""Two stores: a folder on this machine, and an S3-compatible bucket.

Both raise `StoreError` with the HTTP status a failure would have had (404 for
missing, 412 for a failed condition), which is all `fdl.store` looks at.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse


class StoreError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LocalStore:
    """A repository in a folder. ETags are content hashes, which is enough for
    the conditional writes refs rely on. One writer at a time: the condition
    is checked and the file written in two steps."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @property
    def url(self) -> str:
        return str(self.root.resolve())

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise StoreError(f"key escapes the store root: {key!r}")
        return path

    @staticmethod
    def _etag(data: bytes) -> str:
        return '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> str:
        path = self._path(key)
        current = path.read_bytes() if path.is_file() else None
        if if_none_match == "*" and current is not None:
            raise StoreError(f"{key} already exists", status=412)
        if if_match is not None and (current is None or self._etag(current) != if_match):
            raise StoreError(f"{key} does not match {if_match}", status=412)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self._etag(data)

    def put_file(self, key: str, path: Path, **kwargs) -> str:
        return self.put(key, Path(path).read_bytes(), **kwargs)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StoreError(f"no such object: {key}", status=404)
        return path.read_bytes()

    def head(self, key: str) -> dict[str, str] | None:
        path = self._path(key)
        return {"ETag": self._etag(path.read_bytes())} if path.is_file() else None

    def list(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        keys = (str(p.relative_to(self.root)) for p in sorted(self.root.rglob("*")) if p.is_file())
        return [key for key in keys if key.startswith(prefix)]

    def delete(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._path(key).unlink(missing_ok=True)


class S3Store:
    """A repository under ``s3://<bucket>/<prefix>``.

    Credentials and the endpoint come from boto3's usual places (the
    ``AWS_*`` environment variables, ``AWS_ENDPOINT_URL`` for R2 and other
    S3-compatible stores, or a profile).
    """

    def __init__(self, bucket: str, prefix: str = "", *, client=None) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise StoreError("S3 needs boto3: pip install 'fdl[s3]'") from exc

            client = boto3.client("s3")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    @property
    def url(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"

    def _call(self, method: str, key: str, **kwargs):
        from botocore.exceptions import ClientError

        try:
            return getattr(self.client, method)(Bucket=self.bucket, **kwargs)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            raise StoreError(f"{method} {key}: {exc}", status=status) from exc

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> str:
        options = {
            name: value
            for name, value in (
                ("ContentType", content_type),
                ("IfMatch", if_match),
                ("IfNoneMatch", if_none_match),
            )
            if value is not None
        }
        answer = self._call("put_object", key, Key=self.prefix + key, Body=data, **options)
        return answer["ETag"]

    def put_file(self, key: str, path: Path, **kwargs) -> str:
        # Catalogs are a few MB, so one request is enough.
        return self.put(key, Path(path).read_bytes(), **kwargs)

    def get(self, key: str) -> bytes:
        return self._call("get_object", key, Key=self.prefix + key)["Body"].read()

    def head(self, key: str) -> dict[str, str] | None:
        try:
            answer = self._call("head_object", key, Key=self.prefix + key)
        except StoreError as exc:
            if exc.status == 404:
                return None
            raise
        return {"ETag": answer["ETag"]}

    def list(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self.prefix + prefix
        )
        for page in pages:
            keys.extend(item["Key"][len(self.prefix) :] for item in page.get("Contents", []))
        return keys

    def delete(self, keys: Sequence[str]) -> None:
        for key in keys:
            self._call("delete_object", key, Key=self.prefix + key)


def open_store(url: str) -> LocalStore | S3Store:
    """``s3://bucket/prefix/`` or a path on this machine."""
    parsed = urlparse(url)
    if parsed.scheme == "s3":
        return S3Store(parsed.netloc, parsed.path)
    if parsed.scheme in ("", "file"):
        return LocalStore(parsed.path if parsed.scheme == "file" else url)
    raise StoreError(f"not a store this knows: {url!r} (s3://bucket/prefix/ or a folder)")
