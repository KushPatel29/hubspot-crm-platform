"""The live-run entry point: bound to one portal, test accounts only, and evidence without record values."""

from __future__ import annotations

import json

import pytest

from crm_platform import cli


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "EVIDENCE", tmp_path)
    return tmp_path


def test_a_tenant_must_be_bound_before_anything_is_written(fake, evidence):
    with pytest.raises(cli.Refused, match="not bound"):
        cli.run("apply", "crosssell", fake)
    assert fake.calls == [("GET", "/account-info/v3/details")]
    cli.run("bind", "crosssell", fake)
    assert json.loads((evidence / "crosssell" / "deploy.json").read_text())["accountId"] == 1234


def test_a_tenant_bound_elsewhere_is_refused(fake, evidence):
    (evidence / "aml").mkdir()
    (evidence / "aml" / "deploy.json").write_text(json.dumps({"accountId": 999}))
    with pytest.raises(cli.Refused, match="bound to portal 999"):
        cli.run("apply", "aml", fake)


def test_only_test_accounts_are_written_unless_named(fake, evidence, monkeypatch):
    monkeypatch.setattr(fake, "routes", lambda original=fake.routes: [
        (r"GET /account-info/v3/details", lambda b, q: (200, {"portalId": 1234, "accountType": "STANDARD"})),
        *original()])
    with pytest.raises(cli.Refused, match="STANDARD"):
        cli.run("bind", "meridian", fake)
    assert cli.run("bind", "meridian", fake, allow_portal="1234")["bound"]["accountId"] == 1234


def test_apply_converges_and_the_evidence_has_counts_not_values(fake, evidence):
    cli.run("bind", "crosssell", fake)
    report = cli.run("apply", "crosssell", fake)
    assert report["verify"]["converged"] and report["verify"]["writes"] == 0
    text = (evidence / "crosssell" / "apply.json").read_text()
    assert "Aurora Kaiseki" not in text and ".example.com" not in text
    assert json.loads(text)["load"]["objects"]["recommendation"]["create"] == 302
    plan = cli.run("plan", "crosssell", fake)
    assert plan["schema"]["converged"] and plan["writes"] == 0
    assert all(o["create"] == 0 and o["update"] == 0 for o in plan["records"]["objects"].values())


def test_the_provision_key_can_come_from_the_environment_and_is_preferred_to_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "SECRETS_DIR", tmp_path)
    monkeypatch.delenv("CRM_PLATFORM_PROVISION_KEY_MERIDIAN", raising=False)
    assert cli.provision_key_value("meridian") == ""
    (tmp_path / "meridian.provision.key").write_text("from-file\n", encoding="utf-8")
    assert cli.provision_key_value("meridian") == "from-file"
    monkeypatch.setenv("CRM_PLATFORM_PROVISION_KEY_MERIDIAN", " from-ci ")
    assert cli.provision_key_value("meridian") == "from-ci"
    assert cli.provision_key_value("aml") == ""  # one tenant's key is never another's


def test_connect_needs_a_bound_portal_a_client_id_and_a_stored_secret_and_records_no_token(tmp_path, monkeypatch):
    from crm_platform.hubspot import oauth

    monkeypatch.setattr(cli, "EVIDENCE", tmp_path)
    monkeypatch.delenv(cli.OAUTH_SECRET_ENV, raising=False)
    store = oauth.MemoryStore()
    with pytest.raises(cli.Refused, match="not bound to a portal"):
        cli.connect_tenant("meridian", store)
    (tmp_path / "meridian").mkdir()
    (tmp_path / "meridian" / "deploy.json").write_text(json.dumps({"accountId": 247574101}), encoding="utf-8")
    with pytest.raises(cli.Refused, match="no client ID yet"):
        cli.connect_tenant("meridian", store)
    (tmp_path / "connector").mkdir()
    (tmp_path / "connector" / "deploy.json").write_text(json.dumps({"clientId": "client-123"}), encoding="utf-8")
    with pytest.raises(cli.Refused, match="client secret is not stored"):
        cli.connect_tenant("meridian", store)
    with pytest.raises(cli.Refused, match="does not hold a single value"):
        cli.store_oauth_secret(store, "two words")
    assert cli.store_oauth_secret(store, "the-secret") == {"stored": "client-secret", "length": 10}

    def http(url, form):
        if form is None:
            return 200, {"hub_id": 247574101, "app_id": 9, "scopes": list(cli.connector_app().scopes)}
        return 200, {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 1800}

    summary = cli.connect_tenant("meridian", store, http=http, open_browser=lambda url: None,
                                 wait=lambda redirect, state: "code")
    assert summary["portal_id"] == "247574101" and summary["scopes_missing"] == []
    assert store.get("meridian:refresh-token") == "refresh-1"
    written = (tmp_path / "meridian" / "oauth_connect.json").read_text(encoding="utf-8")
    assert "refresh-1" not in written and "access-1" not in written and "the-secret" not in written
    assert cli.connector_app().redirect_uri == "http://localhost:3000/oauth-callback"
