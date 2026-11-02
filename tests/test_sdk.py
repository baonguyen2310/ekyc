import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_lab import CONFIG, KEYS, ORIGIN, files, login

from app.main import COOKIE, Job, create_app
from app.provider import SUCCESS
from app.sdk import MAX_CALLS, SdkGateway


@pytest.fixture
def sdk():
    sent = []
    reply = {"status": 200, "body": None, "raw": None, "exception": None}

    def handle(request):
        sent.append(request)
        if reply["exception"]:
            raise reply["exception"]("synthetic transport failure", request=request)
        if reply["raw"] is not None:
            return httpx.Response(
                reply["status"], content=reply["raw"], headers={"content-type": "text/html"}
            )
        path = request.url.path
        if reply["body"] is not None:
            return httpx.Response(reply["status"], json=reply["body"])
        if path.endswith("addFile"):
            obj = {"hash": f"sdk-hash-{len(sent)}"}
        elif "/ocr/" in path:
            obj = {
                "msg": "OK",
                "msg_back": "OK",
                "name": "SYNTHETIC TEST",
                "id": "TEST-ID",
                "issue_date": "01/01/2024",
            }
        elif path.endswith("/mask"):
            obj = {"masked": "no"}
        elif path.endswith("/compare"):
            obj = {"msg": "MATCH", "prob": 99}
        else:
            obj = {"liveness": "success"}
        return httpx.Response(200, json={"message": SUCCESS, "object": obj})

    app = create_app(
        settings=CONFIG,
        sdk_gateway_factory=lambda credentials: SdkGateway(
            credentials, transport=httpx.MockTransport(handle)
        ),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        response = client.post(
            "/api/sdk/runs", json={"consent": True, "flow": "document_face"}, headers=headers
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        yield app, client, headers, run_id, sent, reply


def upload(client, headers, run_id, content=b"opaque-vendor-scan", content_type="text"):
    response = client.post(
        f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile",
        files={"file": ("blob", content, content_type)},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["object"]["hash"]


def test_sdk_uses_server_keys_and_real_oval_config(sdk):
    app, client, headers, run_id, sent, reply = sdk
    response = client.get(f"/api/sdk/runs/{run_id}/config")
    config = response.json()
    assert config["DOUBLE_LIVENESS"] is True
    assert config["ENABLE_API_LIVENESS_FACE"] is True
    assert config["USE_METHOD"] == "PHOTO"
    assert config["SDK_FLOW"] == "DOCUMENT_TO_FACE"
    assert config["ENABLE_API_OCR_DOCUMENT"] is True
    assert config["ENABLE_API_LIVENESS_DOCUMENT"] is True
    assert config["ENABLE_API_COMPARE_FACE"] is True
    assert config["FACE_SCAN_RECORD"] is False
    assert config["HEADERS_REQUEST"]["X-CSRF-Token"] == headers["x-csrf-token"]
    assert config["ACCESS_TOKEN"] == config["TOKEN_KEY"] == config["TOKEN_ID"] == ""
    for path in ("/", "/sdk", "/static/sdk.js", "/api/status", f"/api/sdk/runs/{run_id}/config"):
        for secret in KEYS.values():
            assert secret not in client.get(path).text
    value = upload(client, headers, run_id)
    assert value in app.state.sdk_runs[run_id].hashes
    assert sent[0].url.host == "api.idg.vnpt.vn"
    assert sent[0].headers["Token-id"] == KEYS["token_id"]
    assert sent[0].headers["Token-key"] == KEYS["token_key"]
    assert sent[0].headers["Authorization"] == "Bearer " + KEYS["access_token"]
    assert b"opaque-vendor-scan" in sent[0].content
    assert client.get("/sdk").headers["X-Frame-Options"] == "DENY"
    assert "'unsafe-inline'" in client.get("/sdk").headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in client.get("/").headers["Content-Security-Policy"]


def test_sdk_proxy_accepts_only_run_owned_hashes_and_allowed_endpoints(sdk):
    app, client, headers, run_id, sent, reply = sdk
    prefix = f"/api/sdk/runs/{run_id}/proxy"
    assert client.post(prefix + "/ai/v1/faces/add", json={}, headers=headers).status_code == 404
    assert (
        client.post(
            prefix + "/ai/v1/web/face/compare",
            json={"img_front": "foreign", "img_face": "foreign"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(prefix + "/ai/v1/web/face/compare", json=[], headers=headers).status_code == 422
    )
    assert sent == []
    value = upload(client, headers, run_id)
    assert (
        client.post(
            prefix + "/ai/v1/web/face/compare",
            json={"img_front": value, "img_face": value},
            headers={"origin": ORIGIN},
        ).status_code
        == 403
    )
    assert (
        client.post(
            prefix + "/ai/v1/web/face/compare",
            json={"img_front": value, "img_face": value},
            headers={**headers, "origin": "https://other.example"},
        ).status_code
        == 403
    )
    assert len(sent) == 1


def test_sdk_full_flow_reports_only_server_observed_checks(sdk):
    app, client, headers, run_id, sent, reply = sdk
    front, back, scan, far, near = [upload(client, headers, run_id) for _ in range(5)]
    calls = [
        ("card/liveness", {"img": front}),
        ("card/liveness", {"img": back}),
        ("ocr/id/front", {"img_front": front, "type": -1}),
        ("ocr/id", {"img_front": front, "img_back": back, "type": -1}),
        ("face/liveness-3d", {"far_img": far, "near_img": near, "scan3d": scan}),
        ("face/mask", {"img": near}),
        ("face/compare", {"img_front": front, "img_face": near}),
    ]
    for path, payload in calls:
        response = client.post(
            f"/api/sdk/runs/{run_id}/proxy/ai/v1/web/{path}",
            json={
                **payload,
                "client_session": "WEB-SDK_Chrome-145_3.2.1_test-uuid_1793600000000",
                "token": "forged-token",
                "arbitrary": "not-forwarded",
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        actual = json.loads(sent[-1].content)
        assert actual["client_session"] == "WEB-SDK_Chrome-145_3.2.1_test-uuid_1793600000000"
        assert actual["token"] == "e41-1b6d-45c9-9"
        assert "arbitrary" not in actual
    result = client.post(f"/api/sdk/runs/{run_id}/finish", json={}, headers=headers).json()
    assert result["status"] == "completed"
    assert result["calls"] == 12
    assert len(result["checks"]) == 7
    assert all(check["status"] == "pass" for check in result["checks"])
    assert {field["label"] for field in result["fields"]} >= {"Họ và tên", "Số giấy tờ", "Ngày cấp"}
    assert "opaque-vendor-scan" not in json.dumps(result)
    assert (
        client.post(
            f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile",
            files={"file": ("blob", b"data", "text")},
            headers=headers,
        ).status_code
        == 409
    )


def test_sdk_cancelled_or_missing_liveness_is_never_a_pass(sdk):
    app, client, headers, run_id, sent, reply = sdk
    result = client.post(
        f"/api/sdk/runs/{run_id}/finish", json={"liveness": "success"}, headers=headers
    ).json()
    assert result["calls"] == 0
    assert all(check["status"] == "review" for check in result["checks"])
    assert result["raw"] == {}


def test_sdk_run_is_private_revoked_on_logout_and_expires(sdk):
    app, client, headers, run_id, sent, reply = sdk
    other = TestClient(app, base_url=ORIGIN)
    try:
        assert other.get("/sdk").status_code == 401
        assert other.get(f"/api/sdk/runs/{run_id}").status_code == 401
        login(other)
        assert other.get(f"/api/sdk/runs/{run_id}").status_code == 404
        assert other.get(f"/api/sdk/runs/{run_id}/config").status_code == 404
    finally:
        other.close()
    app.state.sdk_runs[run_id].created -= 1201
    assert client.get(f"/api/sdk/runs/{run_id}").status_code == 404
    response = client.post("/api/sdk/runs", json={"consent": True}, headers=headers)
    new_id = response.json()["id"]
    client.post("/api/logout", headers=headers)
    assert client.get(f"/api/sdk/runs/{new_id}").status_code == 401
    assert app.state.sdk_runs == {}


def test_sdk_requires_consent_shares_admission_with_images_and_caps_requests(sdk):
    app, client, headers, run_id, sent, reply = sdk
    assert client.post("/api/sdk/runs", json={"consent": False}, headers=headers).status_code == 422
    assert client.post("/api/sdk/runs", json={"consent": True}, headers=headers).status_code == 409
    assert (
        client.post(
            "/api/verify", data={"mode": "full", "consent": "yes"}, files=files(), headers=headers
        ).status_code
        == 409
    )
    app.state.sdk_runs[run_id].calls = MAX_CALLS
    response = client.post(
        f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile",
        files={"file": ("blob", b"data", "text")},
        headers=headers,
    )
    assert response.status_code == 429
    assert sent == []


def test_sdk_vendor_failure_is_visible_and_credentials_are_redacted(sdk):
    app, client, headers, run_id, sent, reply = sdk
    reply.update(
        status=403,
        body={
            "message": "IDG-00000001",
            "error": KEYS["access_token"],
            "Token-key": KEYS["token_key"],
        },
    )
    response = client.post(
        f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile",
        files={"file": ("blob", b"data", "text")},
        headers=headers,
    )
    assert response.status_code == 403
    for secret in KEYS.values():
        assert secret not in response.text
    result = client.post(f"/api/sdk/runs/{run_id}/finish", json={}, headers=headers).json()
    assert result["status"] == "error"
    assert "HTTP 403" in result["error"]
    assert result["calls"] == 1
    assert result["requests"][0]["code"] == "IDG-00000001"
    assert all(check["status"] == "review" for check in result["checks"])


def test_sdk_success_after_retry_clears_only_that_endpoint_failure(sdk):
    app, client, headers, run_id, sent, reply = sdk
    reply.update(status=400, body={"message": "IDG-00010003"})
    path = f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile"
    client.post(path, files={"file": ("blob", b"data", "text")}, headers=headers)
    assert app.state.sdk_runs[run_id].error
    reply.update(status=200, body=None)
    upload(client, headers, run_id)
    assert app.state.sdk_runs[run_id].error is None
    assert len(app.state.sdk_runs[run_id].records) == 2


def start_oval(client, headers, old_id):
    client.post(f"/api/sdk/runs/{old_id}/finish", json={}, headers=headers)
    response = client.post("/api/sdk/runs", json={"consent": True}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def test_oval_runs_without_documents_and_never_calls_ocr_or_compare(sdk):
    app, client, headers, old_id, sent, reply = sdk
    run_id = start_oval(client, headers, old_id)
    config = client.get(f"/api/sdk/runs/{run_id}/config").json()
    assert config["SDK_FLOW"] == "FACE"
    assert config["SHOW_STEP"] is False
    assert config["ENABLE_API_OCR_DOCUMENT"] is False
    assert config["ENABLE_API_LIVENESS_DOCUMENT"] is False
    assert config["ENABLE_API_COMPARE_FACE"] is False
    assert config["ENABLE_API_LIVENESS_FACE"] is True
    assert config["ENABLE_API_MASKED_FACE"] is True
    prefix = f"/api/sdk/runs/{run_id}/proxy"
    for path in ["ocr/id", "card/liveness", "face/compare"]:
        assert (
            client.post(prefix + "/ai/v1/web/" + path, json={}, headers=headers).status_code == 422
        )
    assert sent == []
    scan, near, far = [upload(client, headers, run_id) for _ in range(3)]
    for path, payload in [
        ("face/liveness-3d", {"scan3d": scan, "near_img": near, "far_img": far}),
        ("face/mask", {"img": near}),
    ]:
        response = client.post(
            prefix + "/ai/v1/web/" + path,
            json={**payload, "client_session": "WEB-SDK_Safari-18_3.2.1_test_123"},
            headers=headers,
        )
        assert response.status_code == 200
    result = client.post(f"/api/sdk/runs/{run_id}/finish", json={}, headers=headers).json()
    assert result["mode"] == "oval"
    assert result["calls"] == 5
    assert result["fields"] == []
    assert {check["key"] for check in result["checks"]} == {"liveness", "mask"}
    assert all(check["status"] == "pass" for check in result["checks"])
    assert len(result["requests"]) == 5


def test_cancelled_oval_expects_only_liveness_and_mask(sdk):
    app, client, headers, old_id, sent, reply = sdk
    run_id = start_oval(client, headers, old_id)
    result = client.post(f"/api/sdk/runs/{run_id}/finish", json={}, headers=headers).json()
    assert {check["key"] for check in result["checks"]} == {"liveness", "mask"}
    assert all(check["status"] == "review" for check in result["checks"])


@pytest.mark.parametrize(
    "exception,kind,status",
    [(httpx.ReadTimeout, "timeout", 504), (httpx.ConnectError, "connection", 502)],
)
def test_failed_upload_keeps_failed_request_in_history(sdk, exception, kind, status):
    app, client, headers, run_id, sent, reply = sdk
    reply["exception"] = exception
    response = client.post(
        f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile",
        files={"file": ("blob", b"opaque-scan", "text")},
        headers=headers,
    )
    assert response.status_code == status
    assert response.json()["error_kind"] == kind
    result = client.post(f"/api/sdk/runs/{run_id}/finish", json={}, headers=headers).json()
    assert result["calls"] == len(result["requests"]) == 1
    record = result["requests"][0]
    assert record["http_status"] is None
    assert record["gateway_status"] == status
    assert record["error_kind"] == kind
    assert record["status"] == "error"
    assert record["elapsed_ms"] >= 0
    assert "synthetic transport failure" not in json.dumps(result)


def test_non_json_upload_response_is_diagnosable_without_storing_body(sdk):
    app, client, headers, run_id, sent, reply = sdk
    reply["raw"] = b"<html>private-vendor-diagnostic-body</html>"
    response = client.post(
        f"/api/sdk/runs/{run_id}/proxy/file-service/v1/addFile",
        files={"file": ("blob", b"opaque-scan", "text")},
        headers=headers,
    )
    assert response.status_code == 502
    assert response.json()["error_kind"] == "invalid_json"
    result = client.get(f"/api/sdk/runs/{run_id}").json()
    record = result["requests"][0]
    assert record["http_status"] == 200
    assert record["gateway_status"] == 502
    assert record["content_type"] == "text/html"
    assert record["error_kind"] == "invalid_json"
    assert "private-vendor-diagnostic-body" not in json.dumps(result)


def test_ocr_result_is_retained_independently_of_latest_oval(sdk):
    app, client, headers, old_id, sent, reply = sdk
    owner = client.cookies.get(COOKIE)
    job = Job("separate-ocr-job", owner, "ocr", False, status="completed")
    app.state.jobs[job.id] = job
    run_id = start_oval(client, headers, old_id)
    status = client.get("/api/status").json()
    assert status["latest_job"] == job.id
    assert status["latest_sdk"] == run_id
    assert status["latest_sdk_by_flow"] == {"document_face": old_id, "oval": run_id}
    assert client.get(f"/api/jobs/{job.id}").json()["mode"] == "ocr"


def test_all_three_result_histories_survive_a_new_full_run(sdk):
    app, client, headers, old_id, sent, reply = sdk
    owner = client.cookies.get(COOKIE)
    job = Job("independent-ocr", owner, "ocr", False, status="completed")
    app.state.jobs[job.id] = job
    oval_id = start_oval(client, headers, old_id)
    client.post(f"/api/sdk/runs/{oval_id}/finish", json={}, headers=headers)
    response = client.post(
        "/api/sdk/runs", json={"consent": True, "flow": "document_face"}, headers=headers
    )
    assert response.status_code == 201
    full_id = response.json()["id"]
    status = client.get("/api/status").json()
    assert status["latest_job"] == job.id
    assert status["latest_sdk"] == full_id
    assert status["latest_sdk_by_flow"] == {"document_face": full_id, "oval": oval_id}
    other = TestClient(app, base_url=ORIGIN)
    try:
        login(other)
        assert other.get("/api/status").json()["latest_sdk_by_flow"] == {}
    finally:
        other.close()
    assert not sent, "History restoration must not send requests to VNPT"


def test_client_runtime_failure_is_retained_without_trusting_client_success(sdk):
    app, client, headers, old_id, sent, reply = sdk
    run_id = start_oval(client, headers, old_id)
    response = client.post(
        f"/api/sdk/runs/{run_id}/finish",
        json={"client_error": "sdk_runtime", "liveness": "success"},
        headers=headers,
    )
    result = response.json()
    assert result["status"] == "error"
    assert "SDK" in result["error"]
    assert result["calls"] == 0
    assert all(check["status"] == "review" for check in result["checks"])
