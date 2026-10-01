"""OAuth: the install only counts when its state matches and it landed in the tenant's portal; access tokens are
refreshed before they expire and once on a 401; and no token ever reaches an error message."""

from __future__ import annotations

import threading
from typing import Any
from urllib import error, parse, request

import pytest

from crm_platform.hubspot import oauth

APP = oauth.App("client-123", "http://localhost:0/oauth-callback",
                ("oauth", "crm.objects.contacts.read", "crm.objects.contacts.write"))
SECRET = "the-client-secret"


class FakeHubSpotOAuth:
    """The token endpoint and the token-information endpoint, with access tokens that can be revoked."""

    def __init__(self, portal: int = 247574101, scopes: tuple[str, ...] = APP.scopes) -> None:
        self.portal, self.scopes = portal, scopes
        self.calls: list[tuple[str, dict[str, str] | None]] = []
        self.issued = 0
        self.rotate = False
        self.valid_refresh = {"refresh-0"}

    def __call__(self, url: str, form: dict[str, str] | None) -> tuple[int, Any]:
        self.calls.append((url.split("/access-tokens/")[0], form))
        if url.startswith(oauth.INFO_URL):
            return 200, {"hub_id": self.portal, "app_id": 9, "scopes": list(self.scopes)}
        assert form is not None
        if form["client_secret"] != SECRET:
            return 400, {"status": "BAD_CLIENT_SECRET", "message": "the secret was wrong: " + form["client_secret"]}
        if form["grant_type"] == "authorization_code":
            if form["code"] != "good-code":
                return 400, {"status": "BAD_AUTH_CODE", "message": "code " + form["code"]}
        elif form["refresh_token"] not in self.valid_refresh:
            return 400, {"status": "BAD_REFRESH_TOKEN", "message": form["refresh_token"]}
        self.issued += 1
        held = form.get("refresh_token", "refresh-0")
        if self.rotate:
            held = f"refresh-{self.issued}"
            self.valid_refresh = {held}
        return 200, {"access_token": f"access-{self.issued}", "refresh_token": held, "expires_in": 1800}


def test_the_authorize_url_names_the_app_its_redirect_its_scopes_and_this_runs_state():
    url = parse.urlsplit(oauth.authorize_url(APP, "state-abc"))
    query = dict(parse.parse_qsl(url.query))
    assert (url.netloc, url.path) == ("app.hubspot.com", "/oauth/authorize")
    assert query == {"client_id": "client-123", "redirect_uri": APP.redirect_uri,
                     "scope": "oauth crm.objects.contacts.read crm.objects.contacts.write", "state": "state-abc"}
    assert oauth.new_state() != oauth.new_state() and len(oauth.new_state()) >= 32


def _get(port: int, query: str) -> int:
    try:
        with request.urlopen(f"http://127.0.0.1:{port}/oauth-callback?{query}", timeout=5) as response:
            return int(response.status)
    except error.HTTPError as exc:
        return exc.code


def _listen(state: str) -> tuple[int, dict[str, Any], threading.Thread]:
    server = oauth.callback_server(APP.redirect_uri)
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["code"] = oauth.wait_for_code(APP.redirect_uri, state, timeout=10, server=server)
        except oauth.OAuthError as exc:
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server.server_address[1], result, thread


def test_the_callback_takes_a_code_only_with_the_state_this_run_issued():
    port, result, thread = _listen("expected-state")
    assert _get(port, "code=stolen&state=someone-elses") == 400  # refused, and the server keeps waiting
    assert _get(port, "state=expected-state") == 400              # the right state but no code
    assert _get(port, "code=good-code&state=expected-state") == 200
    thread.join(5)
    assert result == {"code": "good-code"}


def test_a_refused_install_and_a_bad_redirect_are_errors_not_hangs():
    port, result, thread = _listen("s")
    assert _get(port, "error=access_denied&state=s") == 200
    thread.join(5)
    assert isinstance(result["error"], oauth.OAuthError) and "access_denied" in str(result["error"])
    with pytest.raises(oauth.OAuthError, match="http://localhost"):
        oauth.callback_server("https://example.com/oauth-callback")
    with pytest.raises(oauth.OAuthError, match="no approval arrived"):
        oauth.wait_for_code(APP.redirect_uri, "s", timeout=0.2)


def _connect(hubspot: FakeHubSpotOAuth, store: oauth.Store, expected: str, code: str = "good-code") -> dict[str, Any]:
    opened: list[str] = []
    seen: dict[str, str] = {}

    def wait(redirect_uri: str, state: str) -> str:
        seen["state"] = state
        return code

    summary = oauth.connect(APP, SECRET, store, "meridian", expected, http=hubspot, open_browser=opened.append,
                            wait=wait)
    assert f"state={seen['state']}" in opened[0]  # the state it waited for is the one it sent
    return summary


def test_connect_keeps_the_refresh_token_only_when_the_install_is_in_the_tenants_portal():
    store = oauth.MemoryStore()
    summary = _connect(FakeHubSpotOAuth(), store, "247574101")
    assert summary == {"tenant": "meridian", "portal_id": "247574101", "app_id": "9", "scopes_granted": 3,
                       "scopes_missing": [], "access_token_seconds": 1800}
    assert store.get("meridian:refresh-token") == "refresh-0"
    assert "access-" not in str(summary) and "refresh-" not in str(summary)

    elsewhere = oauth.MemoryStore()
    with pytest.raises(oauth.OAuthError, match="installed in portal 999.*nothing was saved"):
        _connect(FakeHubSpotOAuth(portal=999), elsewhere, "247574101")
    assert elsewhere.values == {}


def test_connect_reports_scopes_the_portal_did_not_grant():
    summary = _connect(FakeHubSpotOAuth(scopes=("oauth", "crm.objects.contacts.read")), oauth.MemoryStore(),
                       "247574101")
    assert summary["scopes_missing"] == ["crm.objects.contacts.write"]


def test_errors_carry_hubspots_code_and_never_a_secret_a_code_or_a_token():
    with pytest.raises(oauth.OAuthError) as bad_code:
        _connect(FakeHubSpotOAuth(), oauth.MemoryStore(), "247574101", code="wrong-code")
    assert "BAD_AUTH_CODE" in str(bad_code.value) and "wrong-code" not in str(bad_code.value)
    with pytest.raises(oauth.OAuthError) as bad_secret:
        oauth.refresh(APP, "not-the-secret", "refresh-0", FakeHubSpotOAuth())
    assert "BAD_CLIENT_SECRET" in str(bad_secret.value) and "not-the-secret" not in str(bad_secret.value)
    with pytest.raises(oauth.OAuthError) as info:
        oauth.token_info("access-token-value", lambda url, form: (404, {"status": "error"}))
    assert "access-token-value" not in str(info.value) and "access-tokens" not in str(info.value)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _transport(hubspot: FakeHubSpotOAuth, store: oauth.MemoryStore, clock: Clock, answers: list[int]):
    used: list[str] = []

    def send(token: str):
        def call(method: str, path: str, body: Any) -> tuple[int, Any, dict[str, str]]:
            used.append(token)
            return (answers.pop(0) if answers else 200), {"ok": True}, {}
        return call

    return oauth.OAuthTransport(APP, SECRET, store, "meridian", http=hubspot, send=send, clock=clock), used


def test_the_access_token_is_reused_until_a_minute_before_it_expires():
    hubspot, store, clock = FakeHubSpotOAuth(), oauth.MemoryStore(), Clock()
    store.set("meridian:refresh-token", "refresh-0")
    transport, used = _transport(hubspot, store, clock, [])
    transport("GET", "/crm/v3/objects/contacts", None)
    clock.now += 1_700
    transport("GET", "/crm/v3/objects/contacts", None)
    clock.now += 50  # 1,750 s in: inside the last minute of a 1,800 s token
    transport("GET", "/crm/v3/objects/contacts", None)
    assert used == ["access-1", "access-1", "access-2"] and transport.refreshes == 2


def test_a_401_gets_one_new_token_and_one_retry_and_a_rotated_refresh_token_is_kept():
    hubspot, store, clock = FakeHubSpotOAuth(), oauth.MemoryStore(), Clock()
    hubspot.rotate = True
    store.set("meridian:refresh-token", "refresh-0")
    transport, used = _transport(hubspot, store, clock, [401, 200])
    assert transport("GET", "/crm/v3/objects/contacts", None)[0] == 200
    assert used == ["access-1", "access-2"]
    assert store.get("meridian:refresh-token") == "refresh-2"  # the one HubSpot issued last
    still_refused, _ = _transport(hubspot, store, clock, [401, 401])
    assert still_refused("GET", "/x", None)[0] == 401  # one retry, not a loop


def test_a_tenant_that_was_never_connected_says_so():
    transport, _ = _transport(FakeHubSpotOAuth(), oauth.MemoryStore(), Clock(), [])
    with pytest.raises(oauth.OAuthError, match="meridian is not connected: run connect first"):
        transport("GET", "/crm/v3/objects/contacts", None)
