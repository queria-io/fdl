"""Three stores: a folder on this machine, an S3-compatible bucket, and a
repository served over HTTP.

Both raise `StoreError` with the HTTP status a failure would have had (404 for
missing, 412 for a failed condition), which is all `fdl.store` looks at.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator, Sequence
from urllib.parse import quote, urlencode, urlparse

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def _version() -> str:
    try:
        return importlib.metadata.version("fdl-cli")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


#: Sent with every request. Some hosts refuse the default ``Python-urllib/x.y``.
USER_AGENT = f"fdl/{_version()}"


class StoreError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LocalStore:
    """A repository in a folder. ETags are content hashes, which is enough for
    the conditional writes refs rely on. One writer at a time: the condition
    is checked and the file written in two steps."""

    #: DuckDB writes data files straight into the repository.
    writes_data_files = True

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

    writes_data_files = True

    def __init__(self, bucket: str, prefix: str = "", *, client=None) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise StoreError("S3 needs boto3: pip install 'fdl-cli[s3]'") from exc

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


class HttpStore:
    """A repository served over HTTP by a host that takes writes.

    The protocol is in the specification ("A repository over HTTP"): plain
    GET, HEAD and conditional PUT, a listing, and an upload in parts for files
    larger than a host takes in one request. There is no delete: what to keep
    is the host's call.

    DuckDB cannot write data files to an HTTP URL, so a working catalog of
    this store writes them to a folder on this machine and `fdl.repo.push`
    uploads them (`writes_data_files` is False).
    """

    writes_data_files = False

    #: Files larger than this go up in parts. Hosts commonly cap a request
    #: body at 100 MB.
    PART_SIZE = 64 * 1024 * 1024

    def __init__(self, url: str, *, token: str | None = None, part_size: int | None = None) -> None:
        self.root = url.rstrip("/") + "/"
        self.token = token if token is not None else credentials_for(self.root)
        self.part_size = part_size or self.PART_SIZE

    @property
    def url(self) -> str:
        return self.root

    def _request(
        self,
        method: str,
        key: str,
        *,
        query: dict[str, str] | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        url = self.root + quote(key) + ("?" + urlencode(query) if query else "")
        request = urllib.request.Request(
            url, data=data, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            return urllib.request.urlopen(request)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()
            raise StoreError(
                f"{method} {key or '/'}: {exc.code} {detail or exc.reason}", status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise StoreError(f"{method} {key or '/'}: {exc.reason}") from exc

    @staticmethod
    def _conditions(
        content_type: str | None, if_match: str | None, if_none_match: str | None
    ) -> dict[str, str]:
        return {
            name: value
            for name, value in (
                ("Content-Type", content_type),
                ("If-Match", if_match),
                ("If-None-Match", if_none_match),
            )
            if value is not None
        }

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> str:
        headers = self._conditions(content_type, if_match, if_none_match)
        with self._request("PUT", key, data=data, headers=headers) as answer:
            return answer.headers.get("ETag", "")

    def put_file(self, key: str, path: Path, **kwargs) -> str:
        path = Path(path)
        size = path.stat().st_size
        if size <= self.part_size:
            return self.put(key, path.read_bytes(), **kwargs)
        return self._put_in_parts(key, path, **kwargs)

    def _put_in_parts(
        self,
        key: str,
        path: Path,
        *,
        content_type: str | None = None,
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> str:
        headers = self._conditions(content_type, if_match, if_none_match)
        with self._request("POST", key, query={"uploads": ""}, headers=headers) as answer:
            upload = json.load(answer)["upload"]
        parts = []
        with path.open("rb") as fh:
            for number, chunk in enumerate(iter(lambda: fh.read(self.part_size), b""), start=1):
                query = {"upload": upload, "part": str(number)}
                with self._request("PUT", key, query=query, data=chunk) as answer:
                    parts.append({"part": number, "etag": json.load(answer)["etag"]})
        body = json.dumps({"parts": parts}).encode()
        headers = {**headers, "Content-Type": "application/json"}
        with self._request("POST", key, query={"upload": upload}, data=body, headers=headers) as answer:
            return answer.headers.get("ETag", "")

    #: Objects asked about in one batch, as Git LFS suggests.
    BATCH_SIZE = 100
    #: Uploads to storage running at once.
    UPLOADS_AT_ONCE = 8

    def put_files(self, files: Sequence[tuple[str, Path]], *, content_type: str | None = None) -> None:
        """Write files that are written once (data files, a version's files).

        Asks the host for upload URLs in batches (``POST ?batch``, after Git
        LFS's batch API) and sends each file straight to storage. Files already
        there are skipped. A host without batches, or a file it will not sign
        for, goes up through the host as `put_file` would.
        """
        for start in range(0, len(files), self.BATCH_SIZE):
            chunk = list(files[start : start + self.BATCH_SIZE])
            try:
                answered = self._batch(chunk)
            except StoreError as exc:
                if exc.status not in (404, 405, 501):
                    raise
                answered = [(key, path, None) for key, path in chunk]
            with ThreadPoolExecutor(self.UPLOADS_AT_ONCE) as pool:
                list(pool.map(lambda item: self._send_one(*item, content_type), answered))

    def _batch(self, files: list[tuple[str, Path]]) -> list[tuple[str, Path, dict | None]]:
        """Each file with the host's upload action, ``{}`` when it is already
        there, or ``None`` to send it through the host."""
        body = {
            "operation": "upload",
            "objects": [{"key": key, "size": path.stat().st_size} for key, path in files],
        }
        headers = {"Content-Type": "application/json"}
        with self._request("POST", "", query={"batch": ""}, data=json.dumps(body).encode(), headers=headers) as answer:
            objects = {item["key"]: item for item in json.load(answer)["objects"]}
        answered = []
        for key, path in files:
            item = objects.get(key)
            if item is None:
                raise StoreError(f"the host's batch answer leaves out {key}")
            error = item.get("error")
            if error is not None and error.get("code") != 413:
                raise StoreError(f"{key}: {error.get('code')} {error.get('message', '')}", status=error.get("code"))
            answered.append((key, path, None if error else item.get("actions", {}).get("upload", {})))
        return answered

    def _send_one(self, key: str, path: Path, action: dict | None, content_type: str | None) -> None:
        if action is None:
            try:
                self.put_file(key, path, content_type=content_type, if_none_match="*")
            except StoreError as exc:
                if exc.status != 412:
                    raise
            return
        if not action:
            return
        try:
            with path.open("rb") as body:
                request = urllib.request.Request(
                    action["href"],
                    data=body,
                    method="PUT",
                    # Only the headers the host gave: the repository's token
                    # is not for the storage the URL points at.
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Length": str(path.stat().st_size),
                        **action.get("header", {}),
                    },
                )
                urllib.request.urlopen(request).close()
        except urllib.error.HTTPError as exc:
            # Written once: one already there is this one.
            if exc.code != 412:
                detail = exc.read().decode(errors="replace").strip()
                raise StoreError(f"PUT {key} to storage: {exc.code} {detail or exc.reason}", status=exc.code) from exc
        except urllib.error.URLError as exc:
            raise StoreError(f"PUT {key} to storage: {exc.reason}") from exc

    def get(self, key: str) -> bytes:
        with self._request("GET", key) as answer:
            return answer.read()

    def head(self, key: str) -> dict[str, str] | None:
        try:
            with self._request("HEAD", key) as answer:
                return {"ETag": answer.headers.get("ETag", "")}
        except StoreError as exc:
            if exc.status == 404:
                return None
            raise

    def list(self, prefix: str = "") -> list[str]:
        return list(self._pages(prefix))

    def _pages(self, prefix: str) -> Iterator[str]:
        after: str | None = None
        while True:
            query = {"list": "", "prefix": prefix, **({"after": after} if after else {})}
            with self._request("GET", "", query=query) as answer:
                page = json.load(answer)
            yield from page["keys"]
            after = page.get("next")
            if not after:
                return

    def delete(self, keys: Sequence[str]) -> None:
        raise StoreError("a repository over HTTP is not deleted from by writers", status=405)


def config_path() -> Path:
    """fdl's own settings: ``FDL_CONFIG``, else ``$XDG_CONFIG_HOME/fdl/config.toml``."""
    if os.environ.get("FDL_CONFIG"):
        return Path(os.environ["FDL_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fdl" / "config.toml"


def helper_for(url: str, config: dict) -> str | None:
    """The credential helper set for ``url``: the ``[credential."<prefix>"]`` with
    the same scheme and host whose path is the longest prefix of the URL's."""
    target = urlparse(url)
    best: tuple[int, str] | None = None
    for prefix, entry in (config.get("credential") or {}).items():
        key = urlparse(prefix)
        helper = entry.get("helper") if isinstance(entry, dict) else None
        if not isinstance(helper, str) or (key.scheme, key.netloc) != (target.scheme, target.netloc):
            continue
        path = key.path.rstrip("/")
        if target.path.rstrip("/") != path and not target.path.startswith(path + "/"):
            continue
        if best is None or len(path) > best[0]:
            best = (len(path), helper)
    return best[1] if best else None


def credentials_for(url: str) -> str | None:
    """The token for ``url``: ``FDL_TOKEN``, else what the credential helper set
    for it in fdl's own config answers.

    fdl keeps no tokens. A helper is a command, run as ``<helper> get`` with
    ``protocol=``, ``host=`` and ``path=`` lines on stdin, that answers with a
    ``password=<token>`` line. A host's own tool stores the token and
    registers its helper here.
    """
    if os.environ.get("FDL_TOKEN"):
        return os.environ["FDL_TOKEN"]
    path = config_path()
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StoreError(f"{path} could not be read: {exc}") from exc
    helper = helper_for(url, config)
    if helper is None:
        return None
    parsed = urlparse(url)
    request = f"protocol={parsed.scheme}\nhost={parsed.netloc}\npath={parsed.path.lstrip('/')}\n\n"
    try:
        answer = subprocess.run(
            f"{helper} get",
            shell=True,
            input=request,
            # Only stdout is the answer; what the helper says on stderr (or the
            # shell's "not found") reaches the person, as with Git's helpers.
            stdout=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if answer.returncode != 0:
        return None
    fields = dict(line.split("=", 1) for line in answer.stdout.splitlines() if "=" in line)
    return fields.get("password") or None


def open_store(url: str) -> LocalStore | S3Store | HttpStore:
    """``s3://bucket/prefix/``, ``https://host/path/`` or a path on this machine."""
    parsed = urlparse(url)
    if parsed.scheme == "s3":
        return S3Store(parsed.netloc, parsed.path)
    if parsed.scheme in ("http", "https"):
        return HttpStore(url)
    if parsed.scheme in ("", "file"):
        return LocalStore(parsed.path if parsed.scheme == "file" else url)
    raise StoreError(
        f"not a store this knows: {url!r} (s3://bucket/prefix/, https://host/path/ or a folder)"
    )
