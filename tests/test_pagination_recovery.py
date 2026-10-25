"""Repeated provider cursors cannot replay records or keep a loader running."""

import pytest

from crm_platform.hubspot.client import HubSpotClient, PaginationError


@pytest.mark.parametrize("tokens", [("a",), ("a", "b")])
def test_cyclic_continuations_fail_before_replaying_a_request(tokens):
    calls = []

    def transport(method, path, body):
        calls.append(path)
        if len(calls) > len(tokens) + 1:
            raise RuntimeError("test guard: unbounded pagination")
        token = tokens[(len(calls) - 1) % len(tokens)]
        return 200, {
            "results": [{"id": str(len(calls))}],
            "paging": {"next": {"after": token}},
        }, {}

    client = HubSpotClient(transport, min_interval=0)
    with pytest.raises(PaginationError, match="repeated pagination"):
        list(client.pages("/crm/v3/objects/companies?limit=100"))
    assert len(calls) == len(tokens) + 1
    assert len(calls) == len(set(calls))
