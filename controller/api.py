"""Thin HTTP client for the simulator. Retries transport errors and 5xx; never retries 4xx
(those are answers, not failures — the caller decides what a 409/410/403 means)."""
from __future__ import annotations

import time

import httpx


class Api:
    def __init__(self, base_url: str, log):
        self.c = httpx.Client(base_url=base_url, timeout=300)
        self.log = log

    def _req(self, method: str, path: str, **kw) -> tuple[int, object]:
        delay = 0.5
        for attempt in range(8):
            try:
                r = self.c.request(method, path, **kw)
            except httpx.TransportError as e:
                self.log("warn", "http_transport_error", path=path, error=str(e), attempt=attempt)
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            if r.status_code >= 500:
                self.log("warn", "http_5xx", path=path, status=r.status_code, attempt=attempt)
                time.sleep(delay)
                delay = min(delay * 2, 15)
                continue
            ctype = r.headers.get("content-type", "")
            return r.status_code, (r.json() if "json" in ctype else r.text)
        raise RuntimeError(f"{method} {path}: simulator unavailable after retries")

    def get(self, path, **params):
        return self._req("GET", path, params=params or None)

    def post(self, path, body):
        return self._req("POST", path, json=body)

    def put(self, path, body):
        return self._req("PUT", path, json=body)

    def ok(self, method: str, path: str, body=None):
        s, d = self._req(method, path, json=body) if body is not None else self._req(method, path)
        if s != 200:
            raise RuntimeError(f"{method} {path} -> {s} {d}")
        return d

    def stream_chunk(self, n: int):
        """Yields a chunk's lines as they arrive (bounded memory). The caller must treat a transport error as
        'restart this chunk from scratch' — nothing is committed until the whole chunk is read."""
        with self.c.stream("GET", f"/events/chunks/{n}") as r:
            if r.status_code != 200:
                raise RuntimeError(f"chunk {n} -> {r.status_code}")
            yield from r.iter_lines()
