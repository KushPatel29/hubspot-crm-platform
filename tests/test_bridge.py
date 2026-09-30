"""The CLI bridge transport: one Node process, a marked line protocol, statuses passed through."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from crm_platform.hubspot.client import BridgeTransport, HubSpotClient

FAKE = Path(__file__).with_name("fake_bridge.mjs")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def test_requests_round_trip_and_library_noise_is_ignored():
    with BridgeTransport("demo", script=FAKE) as bridge:
        assert bridge.account_id == "4242"
        status, body, headers = bridge("POST", "/crm/v3/objects/contacts", {"properties": {"email": "a@b.c"}})
        assert status == 200 and body["echoed"] == {"properties": {"email": "a@b.c"}}
        assert headers["X-HubSpot-RateLimit-Remaining"] == "9"
        client = HubSpotClient(bridge, sleep=lambda s: None)
        assert client.get("/missing", missing_ok=True) is None
        assert client.get("/crm/v3/objects/deals")["method"] == "GET"


def test_an_unknown_cli_account_fails_with_the_bridge_message():
    with pytest.raises(RuntimeError, match='no CLI account "missing"'):
        BridgeTransport("missing", script=FAKE)
