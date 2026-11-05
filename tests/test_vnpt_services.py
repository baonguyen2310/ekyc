"""No provider requests, including for configured OCR credentials."""

import httpx
from fastapi.testclient import TestClient
from test_lab import CONFIG, KEYS, ORIGIN, login

from app.main import create_app


def test_services_are_private_and_do_not_inherit_ocr_entitlement(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Availability must not contact a provider")

    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    with TestClient(create_app(settings=CONFIG), base_url=ORIGIN) as client:
        assert client.get("/api/vnpt-services/status").status_code == 401
        login(client)
        result = client.get("/api/vnpt-services/status")
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        for value in result.json().values():
            assert value["ready"] is False
            assert value["state"] == "integration_pending"
        for secret in KEYS.values():
            assert secret not in result.text


def test_no_execution_or_client_supplied_result_is_accepted():
    with TestClient(create_app(settings=CONFIG), base_url=ORIGIN) as client:
        login(client)
        csrf = client.get("/api/status").json()["csrf"]
        headers = {"origin": ORIGIN, "x-csrf-token": csrf}
        for service in ("idcheck", "vneid"):
            response = client.post(
                f"/api/vnpt-services/{service}/runs",
                json={"status": "verified", "consent": True},
                headers=headers,
            )
            assert response.status_code == 404
