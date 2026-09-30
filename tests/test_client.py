"""The client's production behaviour: pacing, retries, the circuit breaker, and errors that are safe to log."""

from __future__ import annotations

import pytest

from crm_platform.hubspot.client import CircuitOpen, HubSpotClient, HubSpotError, token_transport


def test_retries_wait_at_least_as_long_as_retry_after(client, fake, clock):
    fake.fail_next = [429, 502]
    assert client.get("/account-info/v3/details")["portalId"] == 1234
    assert [s for s in clock.slept if s >= 1] == [1.0, 2.0]  # Retry-After 1, then the 2^1 backoff
    assert [status for _, _, status in client.calls] == [429, 502, 200]


def test_requests_are_paced_under_the_app_limit(client, clock):
    for _ in range(3):
        client.get("/account-info/v3/details")
    assert sum(clock.slept) == pytest.approx(0.22)


def test_the_circuit_opens_after_repeated_exhausted_retries_and_closes_after_cooldown(fake, clock):
    client = HubSpotClient(fake, sleep=clock.sleep, clock=clock, max_attempts=2, breaker_threshold=2,
                           breaker_cooldown=60)
    for _ in range(2):
        fake.fail_next = [500, 500]
        with pytest.raises(HubSpotError, match="still failing"):
            client.get("/account-info/v3/details")
    with pytest.raises(CircuitOpen):
        client.get("/account-info/v3/details")
    clock.now += 61
    assert client.get("/account-info/v3/details")["accountType"] == "DEVELOPER_TEST"


def test_errors_name_category_properties_and_correlation_never_values():
    def transport(method, path, body):
        return 400, {"category": "VALIDATION_ERROR", "correlationId": "abc-123",
                     "message": "Property values were not valid: jane@example.com",
                     "errors": [{"context": {"propertyName": ["email"]}}]}, {}

    with pytest.raises(HubSpotError) as caught:
        HubSpotClient(transport).post("/crm/v3/objects/contacts", {"properties": {"email": "jane@example.com"}})
    message = str(caught.value)
    assert "VALIDATION_ERROR" in message and "email" in message and "abc-123" in message
    assert "jane@example.com" not in message and not caught.value.transient


def test_the_token_transport_keeps_the_token_out_of_errors(monkeypatch):
    from urllib import error

    def refuse(*args, **kwargs):
        raise error.URLError("unreachable")

    monkeypatch.setattr("crm_platform.hubspot.client.request.urlopen", refuse)
    send = token_transport("pat-secret-value")
    assert send("GET", "/x", None) == (0, None, {})
    client = HubSpotClient(send, sleep=lambda s: None, max_attempts=1)
    with pytest.raises(HubSpotError) as caught:
        client.get("/x")
    assert "pat-secret-value" not in str(caught.value) and caught.value.transient


def test_pages_follow_hubspot_paging(client, fake):
    for i in range(250):
        fake._write("0-2", None, {"name": f"Company {i}"})
    assert len(list(client.pages("/crm/v3/objects/companies?limit=100&properties=name"))) == 250
