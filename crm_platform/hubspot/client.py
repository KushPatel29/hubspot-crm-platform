"""The HubSpot API client: paced, retried, breaker-protected, safe to log, and indifferent to where its key comes from.

The behaviour is GrowthOps OS's production client (``growthops/hubspot_client.py``), with one change: the client
takes a *transport*, and the transport owns authentication. Two exist:

* :func:`token_transport`: a bearer token (a service key or an app's access token) over HTTPS.
* :class:`BridgeTransport`: the HubSpot CLI's own login, through ``scripts/hs_bridge.mjs``. The CLI keeps a personal
  access key per account and swaps it for short-lived tokens; the bridge reuses that, so the tools here hold no key
  and never print one. This is how every portal in this repo is built.

What the client guarantees, each pinned by a test in ``tests/test_client.py``:

* **Pacing** under the lowest limit an app can have (100 requests per 10 seconds), wider for CRM search.
* **Retries** on 429, 5xx and network failures, with exponential backoff that waits at least ``Retry-After``.
* **A circuit breaker**: after ``breaker_threshold`` calls in a row exhaust their retries, calls are refused for
  ``breaker_cooldown`` seconds instead of hammering a provider that is down.
* **Redacted errors**: HubSpot's category, the property names involved and the correlation ID, never the response
  text (a validation error can echo an email address) and never a token.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

API = "https://api.hubapi.com"
READ_SUFFIXES = ("/batch/read", "/search")
ROOT = Path(__file__).resolve().parents[2]
# (method, path, json body or None) -> (status, parsed body, headers)
Transport = Callable[[str, str, Any], tuple[int, Any, dict[str, str]]]


class HubSpotError(RuntimeError):
    """A failed HubSpot call. Safe to log: category, property names and correlation ID, never values."""

    def __init__(self, status: int, method: str, path: str, *, category: str = "", correlation_id: str = "",
                 properties: tuple[str, ...] = (), note: str = "") -> None:
        self.status, self.method, self.path = status, method, path
        self.category, self.correlation_id, self.properties = category, correlation_id, properties
        detail = "; ".join(part for part in (
            category, f"properties {', '.join(properties)}" if properties else "",
            f"correlation {correlation_id}" if correlation_id else "", note) if part)
        kind = "transient" if self.transient else "permanent"
        super().__init__(f"hubspot: HTTP {status} ({kind}) {method} {path}" + (f": {detail}" if detail else ""))

    @property
    def transient(self) -> bool:
        return self.status in (0, 429) or self.status >= 500


class CircuitOpen(RuntimeError):
    """Recent calls kept failing after retries; the client is cooling down before trying HubSpot again."""


def error_fields(body: Any) -> tuple[str, str, tuple[str, ...]]:
    """HubSpot's category, correlation ID and the property names an error body names."""
    if not isinstance(body, dict):
        return "", "", ()
    names: set[str] = set()
    for item in [body, *(body.get("errors") or [])]:
        context = item.get("context") if isinstance(item, dict) else None
        if isinstance(context, dict):
            for key in ("propertyName", "properties", "property"):
                value = context.get(key)
                names.update(value if isinstance(value, list) else [value] if isinstance(value, str) else [])
    return str(body.get("category") or ""), str(body.get("correlationId") or ""), tuple(sorted(names))


def batch_failures(page: dict) -> list[dict]:
    """The failures in a 207 batch response, each with HubSpot's category and the IDs or keys it names."""
    failures = []
    for item in page.get("errors") or []:
        context = item.get("context") or {}
        ids = context.get("ids") or context.get("id") or []
        category, _, names = error_fields(item)
        failures.append({"category": category or item.get("category", "UNKNOWN"), "properties": list(names),
                         "ids": [str(i) for i in (ids if isinstance(ids, list) else [ids])]})
    return failures


def token_transport(token: str, *, base_url: str = API, timeout: float = 60) -> Transport:
    """Bearer-token HTTPS. The token stays inside this closure."""

    def send(method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
        http_request = request.Request(base_url + path, data=data, headers=headers, method=method)
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                payload, status, response_headers = response.read(), response.status, dict(response.headers.items())
        except error.HTTPError as exc:
            payload, status = exc.read(), exc.code
            response_headers = dict(exc.headers.items()) if exc.headers else {}
        except (error.URLError, TimeoutError, OSError):
            return 0, None, {}
        try:
            parsed = json.loads(payload) if payload else None
        except ValueError:
            parsed = None
        return status, parsed, response_headers

    return send


class BridgeTransport:
    """HubSpot through the CLI's login (``scripts/hs_bridge.mjs``), one Node process for the whole run."""

    MARKER = "\x01bridge "

    def __init__(self, account: str, *, node: str = "node", script: Path = ROOT / "scripts" / "hs_bridge.mjs") -> None:
        self.process = subprocess.Popen([node, str(script), account], cwd=ROOT, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self._next = 0
        hello = self._read()
        if not hello.get("ready"):
            raise RuntimeError(f"hs_bridge did not start for account {account!r}")
        self.account_id = str(hello.get("accountId", ""))

    def _read(self) -> dict:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            if line.startswith(self.MARKER):
                return json.loads(line[len(self.MARKER):])
        detail = self.process.stderr.read()[-300:] if self.process.stderr else ""
        raise RuntimeError(f"hs_bridge exited: {detail.strip()}")

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        assert self.process.stdin is not None
        self._next += 1
        self.process.stdin.write(json.dumps({"id": self._next, "method": method, "path": path, "body": body}) + "\n")
        self.process.stdin.flush()
        reply = self._read()
        headers = {str(k): str(v) for k, v in (reply.get("headers") or {}).items()}
        return int(reply["status"]), reply.get("body"), headers

    def close(self) -> None:
        if self.process.poll() is None:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait(timeout=10)

    def __enter__(self) -> BridgeTransport:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FunctionTransport:
    """HubSpot through a tenant app's ``provision`` function, which makes each call with the app's own token.

    A developer key cannot write standard CRM records, and HubSpot does not issue local-dev app tokens on test
    accounts, so the loader's writes run inside HubSpot instead (``hubspot/functions/provision.js``). Each call is
    sent as ``{method, path, bodyText, timestamp, signature}``, the signature a hex HMAC-SHA256 over
    ``"<timestamp>.<method>.<path>.<bodyText>"`` keyed with the portal's provision key. (HubSpot's gateway does not
    pass custom headers to public functions, so the timestamp and signature travel in the signed envelope.) The
    function returns the upstream status, body and rate-limit headers, so the client's retries, pacing and 207
    handling are unchanged.
    """

    def __init__(self, url: str, key: str, *, timeout: float = 60, clock: Callable[[], float] = time.time,
                 post: Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]] | None = None) -> None:
        self.url, self._key, self.timeout, self.clock = url, key.encode(), timeout, clock
        self._post = post or self._urllib_post

    @staticmethod
    def _urllib_post(url: str, data: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        http_request = request.Request(url, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read()
        except (error.URLError, TimeoutError, OSError):
            return 0, b""

    def sign(self, timestamp: str, method: str, path: str, body_text: str) -> str:
        message = f"{timestamp}.{method}.{path}.{body_text}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""
        timestamp = str(int(self.clock() * 1000))
        headers = {"Content-Type": "application/json"}
        envelope = json.dumps({"method": method, "path": path, "bodyText": body_text, "timestamp": timestamp,
                               "signature": self.sign(timestamp, method, path, body_text)}).encode()
        status, payload = self._post(self.url, envelope, headers, self.timeout)
        try:
            parsed = json.loads(payload) if payload else None
        except ValueError:
            parsed = None
        if status != 200 or not isinstance(parsed, dict) or "status" not in parsed:
            return status, parsed, {}  # the function itself refused (401/403) or is unavailable (0/5xx)
        headers = {str(k): str(v) for k, v in (parsed.get("headers") or {}).items()}
        return int(parsed["status"]), parsed.get("body"), headers


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip()) if value is not None else None
    except ValueError:
        return None


@dataclass
class HubSpotClient:
    transport: Transport
    min_interval: float = 0.11  # under 100 requests per 10 seconds
    search_interval: float = 0.3  # CRM search has its own, lower limit
    max_attempts: int = 6
    breaker_threshold: int = 3
    breaker_cooldown: float = 60
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    calls: list[tuple[str, str, int]] = field(default_factory=list)
    rate_limit: dict[str, int] = field(default_factory=dict)
    _last: float = field(default=-1e9, repr=False)
    _failures: int = field(default=0, repr=False)
    _open_until: float = field(default=0.0, repr=False)

    @property
    def writes(self) -> int:
        return sum(1 for method, path, _ in self.calls
                   if method in {"POST", "PATCH", "PUT", "DELETE"} and not path.endswith(READ_SUFFIXES))

    def _backoff(self, attempt: int, headers: dict[str, str]) -> float:
        wanted = _int(headers.get("retry-after"))
        return max(float(wanted or 0), float(min(2 ** attempt, 20)))

    def request(self, method: str, path: str, body: Any = None, *, missing_ok: bool = False) -> Any:
        route = path.split("?")[0]
        if self._open_until > self.clock():
            raise CircuitOpen(f"hubspot: circuit open after {self._failures} failing calls; {method} {route} not sent")
        interval = self.search_interval if route.endswith("/search") else self.min_interval
        status = 0
        for attempt in range(self.max_attempts):
            wait = self._last + interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            status, payload, raw_headers = self.transport(method, path, body)
            headers = {k.lower(): v for k, v in raw_headers.items()}
            self._last = self.clock()
            self.calls.append((method, route, status))
            for header, key in (("x-hubspot-ratelimit-daily-remaining", "daily_remaining"),
                                ("x-hubspot-ratelimit-remaining", "interval_remaining")):
                value = _int(headers.get(header))
                if value is not None:
                    self.rate_limit[key] = value
            if status in (0, 429) or status >= 500:
                self.sleep(self._backoff(attempt, headers))
                continue
            self._failures = 0
            if status == 404 and missing_ok:
                return None
            if not 200 <= status < 300:
                category, correlation, names = error_fields(payload)
                raise HubSpotError(status, method, route, category=category, correlation_id=correlation,
                                   properties=names)
            return payload if payload is not None else {}
        self._failures += 1
        if self._failures >= self.breaker_threshold:
            self._open_until = self.clock() + self.breaker_cooldown
        raise HubSpotError(status, method, route, note="still failing after retries")

    def get(self, path: str, *, missing_ok: bool = False) -> Any:
        return self.request("GET", path, missing_ok=missing_ok)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body)

    def patch(self, path: str, body: Any) -> Any:
        return self.request("PATCH", path, body)

    def pages(self, path: str, key: str = "results") -> Iterator[dict]:
        after = None
        while True:
            sep = "&" if "?" in path else "?"
            page = self.get(path + (f"{sep}after={after}" if after else ""))
            yield from page.get(key, [])
            after = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return
