"""The loader's signed path through a tenant app's provision function."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from crm_platform.hubspot.client import FunctionTransport, HubSpotClient

ROOT = Path(__file__).resolve().parents[1]
KEY = "k" * 64


def test_calls_are_signed_enveloped_and_unwrapped():
    sent = []

    def post(url, data, headers, timeout):
        sent.append((url, json.loads(data), headers))
        return 200, json.dumps({"status": 207, "headers": {"retry-after": "1"}, "body": {"results": []}}).encode()

    transport = FunctionTransport("https://p.example/hs/serverless/provision", KEY, clock=lambda: 1790000000.0,
                                  post=post)
    status, body, headers = transport("POST", "/crm/v3/objects/0-2/batch/create", {"inputs": [{"name": "Côté"}]})
    assert (status, body, headers) == (207, {"results": []}, {"retry-after": "1"})
    url, envelope, sent_headers = sent[0]
    assert {k: envelope[k] for k in ("method", "path", "bodyText", "timestamp")} == {
        "method": "POST", "path": "/crm/v3/objects/0-2/batch/create", "bodyText": '{"inputs":[{"name":"Côté"}]}',
        "timestamp": "1790000000000"}
    assert envelope["signature"] == transport.sign("1790000000000", "POST", envelope["path"], envelope["bodyText"])
    assert set(sent_headers) == {"Content-Type"}  # nothing rides in headers HubSpot's gateway would drop


def test_a_refusal_by_the_function_reaches_the_client_as_a_permanent_error():
    transport = FunctionTransport("https://p.example/x", KEY, post=lambda *a: (401, b'{"error":"signature mismatch"}'))
    with pytest.raises(Exception, match="401"):
        HubSpotClient(transport, sleep=lambda s: None).get("/crm/v3/schemas")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_signs_exactly_as_the_function_verifies():
    transport = FunctionTransport("https://p.example/x", KEY)
    body_text = json.dumps({"inputs": [{"properties": {"name": "Côté", "n": 1.5}}]}, separators=(",", ":"),
                           ensure_ascii=False)
    script = ("import { signRequest } from './hubspot/functions/provision.js';"
              "const [t, m, p, b] = JSON.parse(process.argv[1]);"
              "process.stdout.write(signRequest(process.argv[2], t, m, p, b));")
    args = json.dumps(["1790000000000", "POST", "/crm/v3/objects/0-2/batch/create", body_text])
    js = subprocess.run(["node", "--input-type=module", "-e", script, args, KEY], cwd=ROOT, capture_output=True,
                        text=True, encoding="utf-8", check=True).stdout
    assert js == transport.sign("1790000000000", "POST", "/crm/v3/objects/0-2/batch/create", body_text)
