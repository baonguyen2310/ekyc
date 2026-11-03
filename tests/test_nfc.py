"""All provider traffic is synthetic MockTransport. No real eKYC requests."""

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_lab import CONFIG, ORIGIN, login

from app.main import create_app
from app.nfc import ReadIdClient, result_summary

DOSSIER = "00000000-0000-4000-8000-000000000001"
PROCESS = "00000000-0000-4000-8000-000000000002"
CAPTURE = "https://lab.sandbox.signicat.com/capture/#/artifact=synthetic-artifact"
NFC_CONFIG = {
    **CONFIG,
    "SIGNICAT_NFC_ENABLED": "true",
    "SIGNICAT_CLIENT_ID": "synthetic-client",
    "SIGNICAT_CLIENT_SECRET": "synthetic-secret",
    "SIGNICAT_CAPTURE_DOMAIN": "lab.sandbox.signicat.com",
}


@pytest.fixture
def nfc():
    sent = []
    control = {
        "sandbox": True,
        "capture": CAPTURE,
        "status": "accepted",
        "process": PROCESS,
        "http": 200,
        "exception": False,
        "empty": False,
        "raw": False,
        "chip": "SUCCEEDED",
    }

    def handle(request):
        sent.append(request)
        if control["exception"]:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if control["http"] != 200:
            return httpx.Response(
                control["http"], text="Do not expose upstream details synthetic-secret"
            )
        if control["raw"]:
            return httpx.Response(200, text="not json")
        path = request.url.path
        if path.endswith("/token"):
            claims = (
                base64.urlsafe_b64encode(json.dumps({"sandbox": control["sandbox"]}).encode())
                .decode()
                .rstrip("=")
            )
            return httpx.Response(
                200,
                json={
                    "access_token": "header." + claims + ".signature",
                    "expires_in": 600,
                    "token_type": "Bearer",
                },
            )
        if path == "/assure/dossiers":
            return httpx.Response(201, json={"dossierId": DOSSIER})
        if path.endswith("/capture"):
            return httpx.Response(201, json={"url": control["capture"]})
        if path.endswith("/processes/" + PROCESS):
            return httpx.Response(
                200,
                json={
                    "provider": "readid",
                    "processType": "ready",
                    "processId": control["process"],
                    "status": control["status"],
                    "providerSpecific": {
                        "consolidatedIdentityData": {
                            "chipVerification": control["chip"],
                            "chipCloneDetection": "NOT_AVAILABLE",
                        },
                        "authorization": "must-not-retain",
                    },
                    "finalResult": {
                        "firstName": "SYNTHETIC",
                        "documentNumber": "TEST-ONLY",
                        "image": "data:image/jpeg;base64,example",
                    },
                    "authorization": "must-not-retain",
                },
            )
        return httpx.Response(
            200,
            json={
                "dossierId": DOSSIER,
                "processes": []
                if control["empty"]
                else [
                    {"provider": "readid", "processType": "ready", "processId": control["process"]}
                ],
            },
        )

    app = create_app(
        settings=NFC_CONFIG,
        nfc_client_factory=lambda settings: ReadIdClient(
            settings, transport=httpx.MockTransport(handle)
        ),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        yield app, client, headers, sent, control


def start(client, headers):
    response = client.post("/api/nfc/runs", json={"consent": True}, headers=headers)
    assert response.status_code == 201
    return response.json()


def refresh(client, headers, run):
    return client.post(f"/api/nfc/runs/{run['id']}/refresh", json={}, headers=headers)


def test_nfc_disabled_without_account_and_get_never_calls_provider():
    def forbidden(settings):
        raise AssertionError("No provider client may be created")

    with TestClient(
        create_app(settings=CONFIG, nfc_client_factory=forbidden), base_url=ORIGIN
    ) as client:
        headers = login(client)
        assert client.get("/api/nfc/status").json()["ready"] is False
        assert (
            client.post("/api/nfc/runs", json={"consent": True}, headers=headers).status_code == 503
        )


def test_create_and_result_use_fixed_backend_api_not_client_claims(nfc):
    app, client, headers, sent, control = nfc
    assert client.get("/api/nfc/status").json()["ready"] is True
    assert not sent
    run = start(client, headers)
    assert run["status"] == "pending"
    assert run["capture_url"] == CAPTURE
    assert len(sent) == 3
    payload = json.loads(sent[2].content)
    assert payload == {
        "providers": [{"provider": "readid", "processType": "ready"}],
        "sdk": "native",
        "redirectUrl": ORIGIN + "/?tab=nfc",
        "requestDomain": "lab.sandbox.signicat.com",
    }
    assert json.loads(sent[1].content)["externalReference"] == "ekyc-lab-" + run["id"]
    assert all(r.url.host == "api.signicat.com" for r in sent)
    assert sent[0].headers["authorization"].startswith("Basic ")
    assert "scope=signicat-api" in sent[0].content.decode()
    for path in ("/api/nfc/status", f"/api/nfc/runs/{run['id']}?status=accepted&processId=forged"):
        assert "synthetic-secret" not in client.get(path).text
    assert len(sent) == 3, "GETs and redirect parameters must not trigger provider calls"
    result = refresh(client, headers, run).json()
    assert result["status"] == "accepted"
    assert result["sandbox"] is True
    assert result["checks"][0]["state"] == "pass"
    assert result["checks"][1]["state"] == "review"
    assert result["capture_url"] is None
    assert "SYNTHETIC" in str(result["fields"])
    assert "must-not-retain" not in json.dumps(result)
    assert "data:image" not in json.dumps(result)
    assert result["calls"] == 5
    assert refresh(client, headers, run).json() == result
    assert len(sent) == 5, "Final results must not be polled again"


def test_consent_csrf_and_session_isolation(nfc):
    app, client, headers, sent, control = nfc
    assert client.post("/api/nfc/runs", json={"consent": False}, headers=headers).status_code == 422
    assert (
        client.post("/api/nfc/runs", json={"consent": True}, headers={"origin": ORIGIN}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/nfc/runs", json={"consent": True, "status": "accepted"}, headers=headers
        ).status_code
        == 422
    )
    assert not sent
    run = start(client, headers)
    assert client.post("/api/nfc/runs", json={"consent": True}, headers=headers).status_code == 409
    first_cookie = client.cookies.get("ekyc_lab_session")
    client.cookies.clear()
    assert client.get(f"/api/nfc/runs/{run['id']}").status_code == 401
    other_headers = login(client)
    assert client.get(f"/api/nfc/runs/{run['id']}").status_code == 404
    assert refresh(client, other_headers, run).status_code == 404
    client.cookies.clear()
    client.cookies.set("ekyc_lab_session", first_cookie, domain="testserver.local", path="/")
    assert client.post("/api/logout", json={}, headers=headers).status_code == 200
    login(client)
    assert client.get("/api/nfc/status").json()["latest_run"] is None
    assert len(sent) == 3


def test_production_token_is_refused_before_creating_dossier(nfc):
    app, client, headers, sent, control = nfc
    control["sandbox"] = False
    run = start(client, headers)
    assert run["status"] == "error"
    assert "sandbox" in run["error"]
    assert len(sent) == 1
    assert "access_token" not in json.dumps(run)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/capture/#secret",
        "javascript:alert(1)",
        "https://lab.sandbox.signicat.com@evil.example/capture/",
        "https://lab.sandbox.signicat.com:8443/capture/",
        "https://lab.sandbox.signicat.com/capture/\nmalicious",
    ],
)
def test_capture_url_cannot_send_user_to_untrusted_site(nfc, url):
    app, client, headers, sent, control = nfc
    control["capture"] = url
    result = start(client, headers)
    assert result["status"] == "error"
    assert result["capture_url"] is None


@pytest.mark.parametrize("kind", ["timeout", "http", "json"])
def test_error_is_sanitized_and_not_retried(nfc, kind):
    app, client, headers, sent, control = nfc
    if kind == "timeout":
        control["exception"] = True
    if kind == "http":
        control["http"] = 403
    if kind == "json":
        control["raw"] = True
    result = start(client, headers)
    assert result["status"] == "error"
    assert result["error"]
    assert "synthetic-secret" not in json.dumps(result)
    assert result["calls"] == len(sent) == 1


def test_waiting_rate_limit_and_expiry(nfc, monkeypatch):
    app, client, headers, sent, control = nfc
    control["empty"] = True
    run = start(client, headers)
    assert refresh(client, headers, run).json()["status"] == "pending"
    assert refresh(client, headers, run).status_code == 429
    assert len(sent) == 4
    now = time.time()
    monkeypatch.setattr("app.nfc.time.time", lambda: now + 1201)
    assert client.get(f"/api/nfc/runs/{run['id']}").status_code == 404
    assert client.get("/api/nfc/status").json()["latest_run"] is None


def test_only_exact_process_can_be_read(nfc):
    app, client, headers, sent, control = nfc
    run = start(client, headers)
    control["process"] = "../../outside-dossier"
    result = refresh(client, headers, run).json()
    assert result["error"]
    assert result["status"] == "pending"
    assert len(sent) == 4
    assert not any("outside-dossier" in str(request.url) for request in sent)


def test_unknown_chip_checks_never_pass():
    summary = result_summary(
        {
            "status": "accepted",
            "providerSpecific": {
                "consolidatedIdentityData": {
                    "chipVerification": True,
                    "chipCloneDetection": "NOT_AVAILABLE",
                }
            },
        }
    )
    assert all(check["state"] == "review" for check in summary["checks"])
