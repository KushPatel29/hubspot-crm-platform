"""OAuth 2.0 against HubSpot: the install flow, token refresh, and a transport the client can use like any other.

The apps in this repo are private static-auth apps, one per portal (the agency pattern), and the loader reaches each
portal through that app's own ``provision`` function. This module is the other way an integration reaches HubSpot,
the one a product installed into many portals uses: an OAuth app (``hubspot/projects/connector``), an authorization
code captured on a local redirect, a refresh token, and short-lived access tokens.

* :func:`connect` runs the install: it opens HubSpot's authorize page, waits on ``http://localhost:<port>`` for the
  redirect, checks the ``state`` it issued (so a code delivered by another page is refused), exchanges the code, asks
  HubSpot which portal the token belongs to, and stores the refresh token only if that is the portal the tenant is
  bound to. A token for the wrong portal is discarded, never stored.
* :class:`OAuthTransport` is a client transport: it refreshes the access token a minute before it expires and once on
  a 401, and keeps a rotated refresh token.

What is secret and where it lives: the refresh token and the app's client secret go to the operating system's
credential store (:class:`KeyringStore`: Windows Credential Manager, macOS Keychain, Secret Service), never to a file
in the repo; access tokens live in memory. Errors carry HubSpot's status and error code only. The token endpoint's
answers are never logged, and the token-information URL (which HubSpot defines with the token in its path) is never
put in an error.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol
from urllib import error, parse, request

from crm_platform.hubspot.client import Transport, token_transport

AUTHORIZE = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
INFO_URL = "https://api.hubapi.com/oauth/v1/access-tokens/"
SERVICE = "crm-platform-hubspot-oauth"
CLIENT_SECRET = "client-secret"
EARLY = 60  # refresh this many seconds before the access token expires
# (url, form fields or None for a GET) -> (status, parsed JSON)
Http = Callable[[str, dict[str, str] | None], tuple[int, Any]]


class OAuthError(RuntimeError):
    """A failed OAuth step. Safe to log: the step, HubSpot's status and its error code; never a token or a URL."""

    def __init__(self, step: str, status: int = 0, code: str = "") -> None:
        self.step, self.status, self.code = step, status, code
        super().__init__(f"hubspot oauth: {step} failed" + (f" (HTTP {status})" if status else "")
                         + (f": {code}" if code else ""))


@dataclass(frozen=True)
class App:
    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]


class Store(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class MemoryStore:
    """A store for tests and one-off runs: nothing outlives the process."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class KeyringStore:
    """The operating system's credential store, through the ``keyring`` package (``pip install .[oauth]``)."""

    def __init__(self, service: str = SERVICE) -> None:
        try:
            import keyring
        except ImportError:
            raise SystemExit("the OAuth commands keep tokens in the OS credential store: "
                             "python -m pip install -e .[oauth]") from None
        self.keyring, self.service = keyring, service

    def get(self, name: str) -> str | None:
        return self.keyring.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        self.keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> None:
        try:
            self.keyring.delete_password(self.service, name)
        except Exception:  # noqa: BLE001  (each backend raises its own "not found")
            return


def default_http(url: str, form: dict[str, str] | None) -> tuple[int, Any]:
    data = parse.urlencode(form).encode() if form is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        with request.urlopen(request.Request(url, data=data, headers=headers), timeout=60) as response:
            status, payload = response.status, response.read()
    except error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    except (error.URLError, TimeoutError, OSError):
        return 0, None
    try:
        return status, json.loads(payload) if payload else None
    except ValueError:
        return status, None


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(app: App, state: str) -> str:
    return AUTHORIZE + "?" + parse.urlencode({"client_id": app.client_id, "redirect_uri": app.redirect_uri,
                                             "scope": " ".join(app.scopes), "state": state})


def _code(step: str, status: int, body: Any) -> OAuthError:
    code = str(body.get("status") or body.get("error") or "") if isinstance(body, dict) else ""
    return OAuthError(step, status, code)


def callback_server(redirect_uri: str) -> HTTPServer:
    """A server bound to the redirect URI's port on the loopback address only (port 0 picks a free one)."""
    target = parse.urlsplit(redirect_uri)
    if target.scheme != "http" or target.hostname not in ("localhost", "127.0.0.1"):
        raise OAuthError("the local redirect must be http://localhost:<port>/<path>")
    return HTTPServer(("127.0.0.1", target.port or 0), BaseHTTPRequestHandler)


def wait_for_code(redirect_uri: str, state: str, *, timeout: float = 300, server: HTTPServer | None = None,
                  clock: Callable[[], float] = time.monotonic) -> str:
    """Serve the redirect URI until HubSpot delivers a code with the state this run issued."""
    path = parse.urlsplit(redirect_uri).path or "/"
    outcome: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802  (the name http.server calls)
            url = parse.urlsplit(self.path)
            query = dict(parse.parse_qsl(url.query))
            if url.path != path:
                return self._answer(404, "Not the OAuth callback.")
            if not hmac.compare_digest(query.get("state", ""), state):
                return self._answer(400, "This request did not come from the install you started. Nothing was saved.")
            if "error" in query:
                outcome["error"] = query["error"][:80]
                return self._answer(200, "The install was not approved. You can close this tab.")
            if not query.get("code"):
                return self._answer(400, "HubSpot sent no code.")
            outcome["code"] = query["code"]
            self._answer(200, "Connected. You can close this tab and return to the terminal.")

        def _answer(self, status: int, text: str) -> None:
            body = f"<!doctype html><meta charset=utf-8><title>CRM platform</title><p>{text}</p>".encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # the request line carries the code: never log it
            return

    httpd = server or callback_server(redirect_uri)
    httpd.RequestHandlerClass = Handler
    deadline = clock() + timeout
    try:
        while not outcome and clock() < deadline:
            httpd.timeout = max(0.05, min(1.0, deadline - clock()))
            httpd.handle_request()
    finally:
        httpd.server_close()
    if "error" in outcome:
        raise OAuthError("authorization", code=outcome["error"])
    if "code" not in outcome:
        raise OAuthError("authorization", code="no approval arrived before the timeout")
    return outcome["code"]


def _grant(app: App, client_secret: str, http: Http, step: str, fields: dict[str, str]) -> dict[str, Any]:
    status, body = http(TOKEN_URL, {"client_id": app.client_id, "client_secret": client_secret, **fields})
    if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise _code(step, status, body)
    return body


def exchange(app: App, client_secret: str, code: str, http: Http = default_http) -> dict[str, Any]:
    return _grant(app, client_secret, http, "code exchange",
                  {"grant_type": "authorization_code", "redirect_uri": app.redirect_uri, "code": code})


def refresh(app: App, client_secret: str, refresh_token: str, http: Http = default_http) -> dict[str, Any]:
    return _grant(app, client_secret, http, "token refresh",
                  {"grant_type": "refresh_token", "refresh_token": refresh_token})


def token_info(access_token: str, http: Http = default_http) -> dict[str, Any]:
    status, body = http(INFO_URL + parse.quote(access_token, safe=""), None)
    if status != 200 or not isinstance(body, dict):
        raise _code("token information", status, body)
    return body


def refresh_name(tenant: str) -> str:
    return f"{tenant}:refresh-token"


def connect(app: App, client_secret: str, store: Store, tenant: str, expected_portal: str, *,
            http: Http = default_http, open_browser: Callable[[str], Any] = webbrowser.open,
            wait: Callable[[str, str], str] = wait_for_code) -> dict[str, Any]:
    """Install the app into the tenant's portal and keep its refresh token. Returns counts and IDs, no tokens."""
    state = new_state()
    open_browser(authorize_url(app, state))
    tokens = exchange(app, client_secret, wait(app.redirect_uri, state), http)
    info = token_info(tokens["access_token"], http)
    portal = str(info.get("hub_id", ""))
    if portal != str(expected_portal):
        raise OAuthError("install", code=f"the app was installed in portal {portal or 'unknown'}, but {tenant} is "
                                        f"bound to {expected_portal}; nothing was saved")
    granted = set(info.get("scopes") or [])
    store.set(refresh_name(tenant), tokens["refresh_token"])
    return {"tenant": tenant, "portal_id": portal, "app_id": str(info.get("app_id", "")),
            "scopes_granted": len(granted), "scopes_missing": sorted(set(app.scopes) - granted),
            "access_token_seconds": int(tokens.get("expires_in", 0))}


class OAuthTransport:
    """A client transport over OAuth: a fresh access token when the old one is about to expire, or was refused."""

    def __init__(self, app: App, client_secret: str, store: Store, tenant: str, *, http: Http = default_http,
                 send: Callable[[str], Transport] = token_transport, clock: Callable[[], float] = time.time) -> None:
        self.app, self.client_secret, self.store, self.tenant = app, client_secret, store, tenant
        self.http, self.send, self.clock = http, send, clock
        self.transport: Transport | None = None
        self.expires_at = 0.0
        self.refreshes = 0

    def _fresh(self, *, force: bool = False) -> Transport:
        if self.transport is None or force or self.clock() >= self.expires_at - EARLY:
            held = self.store.get(refresh_name(self.tenant))
            if not held:
                raise OAuthError("token refresh", code=f"{self.tenant} is not connected: run connect first")
            tokens = refresh(self.app, self.client_secret, held, self.http)
            if tokens.get("refresh_token") and tokens["refresh_token"] != held:
                self.store.set(refresh_name(self.tenant), tokens["refresh_token"])  # HubSpot rotated it
            self.transport = self.send(tokens["access_token"])
            self.expires_at = self.clock() + float(tokens.get("expires_in", 1800))
            self.refreshes += 1
        return self.transport

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
        answer = self._fresh()(method, path, body)
        if answer[0] == 401:  # revoked or expired early: one new token, one more try
            answer = self._fresh(force=True)(method, path, body)
        return answer
