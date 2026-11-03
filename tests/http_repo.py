"""A repository over HTTP for the tests: the protocol in the specification,
served from a folder with `LocalStore` behind it."""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from fdl.stores import LocalStore, StoreError

TOKEN = "secret"


#: What the test host signs its upload URLs with.
SIGNATURE = "signed"


def serve(store: LocalStore, *, batch: bool = False, sign_up_to: int | None = None) -> ThreadingHTTPServer:
    """``batch`` answers ``POST ?batch`` with URLs under ``/storage/``, as a
    host handing out signed URLs would; files over ``sign_up_to`` bytes get a
    413 there."""
    uploads: dict[str, dict[int, bytes]] = {}
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            pass

        def _parts(self):
            url = urlparse(self.path)
            return unquote(url.path.lstrip("/")), parse_qs(url.query, keep_blank_values=True)

        def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _authorized(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {TOKEN}":
                return True
            self._send(401, b"no token")
            return False

        def _write(self, key: str, data: bytes) -> None:
            try:
                etag = store.put(
                    key,
                    data,
                    if_match=self.headers.get("If-Match"),
                    if_none_match=self.headers.get("If-None-Match"),
                )
            except StoreError as exc:
                self._send(exc.status or 500, str(exc).encode())
                return
            self._send(200, headers={"ETag": etag})

        def do_GET(self) -> None:
            key, query = self._parts()
            requests.append(("GET", key))
            if "list" in query:
                keys = store.list(query.get("prefix", [""])[0])
                self._send(200, json.dumps({"keys": keys, "next": None}).encode())
                return
            try:
                data = store.get(key)
            except StoreError as exc:
                self._send(exc.status or 500)
                return
            self._send(200, data, {"ETag": store._etag(data)})

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_PUT(self) -> None:
            key, query = self._parts()
            requests.append(("PUT", key))
            if key.startswith("storage/"):
                # Storage takes the signature in the URL, not the token.
                if query.get("sig") != [SIGNATURE] or "Authorization" in self.headers:
                    self._send(403, b"bad signature")
                    return
                self._write(key[len("storage/") :], self._body())
                return
            if not self._authorized():
                return
            data = self._body()
            if "upload" in query:
                uploads[query["upload"][0]][int(query["part"][0])] = data
                self._send(200, json.dumps({"etag": str(len(data))}).encode())
                return
            self._write(key, data)

        def do_POST(self) -> None:
            key, query = self._parts()
            requests.append(("POST", key))
            if not self._authorized():
                return
            body = self._body()
            if "batch" in query:
                if not batch:
                    self._send(404)
                    return
                self._send(200, json.dumps({"objects": [answer(o) for o in json.loads(body)["objects"]]}).encode())
                return
            if "uploads" in query:
                upload = uuid.uuid4().hex
                uploads[upload] = {}
                self._send(200, json.dumps({"upload": upload}).encode())
                return
            parts = uploads.pop(query["upload"][0])
            numbers = [part["part"] for part in json.loads(body)["parts"]]
            self._write(key, b"".join(parts[n] for n in numbers))

    def answer(item: dict) -> dict:
        if store.head(item["key"]) is not None:
            return item
        if sign_up_to is not None and item["size"] > sign_up_to:
            return {**item, "error": {"code": 413, "message": "too large to sign"}}
        href = f"http://127.0.0.1:{server.server_port}/storage/{item['key']}?sig={SIGNATURE}"
        return {**item, "actions": {"upload": {"href": href, "header": {"If-None-Match": "*"}, "expires_in": 3600}}}

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = requests  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
