import asyncio
import base64
import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.provider import Credentials, ProviderError, VnptClient, check_result, normalize_image

ACCESS_CODE = "test-private-access-code-0123456789abcdef"
ORIGIN = "https://testserver"
CONFIG = {"LAB_ACCESS_CODE": ACCESS_CODE, "PUBLIC_ORIGIN": ORIGIN, "COOKIE_SECURE": "true"}
KEYS = {
    "token_id": "test-token-id",
    "token_key": "test-private-token-key",
    "access_token": "test-private-access-token",
}
CONFIG.update(
    {
        "VNPT_TOKEN_ID": KEYS["token_id"],
        "VNPT_TOKEN_KEY": KEYS["token_key"],
        "VNPT_ACCESS_TOKEN": "bearer " + KEYS["access_token"],
    }
)


def image():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buffer, "PNG")
    return buffer.getvalue()


def files(full=True):
    return {
        key: (f"{key}.png", image(), "image/png")
        for key in (["front", "back", "selfie"] if full else ["front", "back"])
    }


class FakeVnpt:
    uploads = []
    calls = []
    error = None
    missing_liveness = False

    def __init__(self, credentials):
        self.credentials = credentials

    async def upload(self, content, title):
        self.uploads.append(title)
        return f"provider-hash-{title}"

    async def check(self, path, payload):
        self.calls.append((path, payload))
        if self.error:
            raise ProviderError("AUTH", "VNPT từ chối quyền truy cập.")
        if path.endswith("/front"):
            return {
                "msg": "OK",
                "name": "TEST PERSON",
                "id": "TEST-DOCUMENT",
                "birth_day": "01/01/1990",
            }
        if path.endswith("/back"):
            return {"msg_back": "OK", "issue_date": "01/01/2024"}
        if path.endswith("/liveness"):
            return {} if self.missing_liveness else {"liveness": "success"}
        if path.endswith("/mask"):
            return {"masked": "no"}
        return {"msg": "MATCH", "prob": 99.5}

    async def close(self):
        pass


@pytest.fixture
def app():
    FakeVnpt.calls, FakeVnpt.uploads = [], []
    FakeVnpt.error, FakeVnpt.missing_liveness = None, False
    return create_app(settings=CONFIG, client_factory=FakeVnpt)


def login(client):
    response = client.post(
        "/api/login", json={"access_code": ACCESS_CODE}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 200
    assert (
        "HttpOnly" in response.headers.get("set-cookie", "") or "set-cookie" not in response.headers
    )
    state = client.get("/api/status").json()
    headers = {"origin": ORIGIN, "x-csrf-token": state["csrf"]}
    return headers


def wait_result(client, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        if response.json()["status"] != "running":
            return response.json()
        time.sleep(0.01)
    pytest.fail("Test job never finished")


def test_no_credential_or_result_access_without_login(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/jobs/anything").status_code == 401
        assert client.post("/api/verify", files=files()).status_code == 403
        headers = login(client)
        assert (
            client.put(
                "/api/credentials",
                json=KEYS,
                headers={
                    "origin": "https://wrong.example",
                    "x-csrf-token": headers["x-csrf-token"],
                },
            ).status_code
            == 403
        )
        assert (
            client.put("/api/credentials", json=KEYS, headers={"origin": ORIGIN}).status_code == 403
        )
        assert client.put("/api/credentials", json=KEYS, headers=headers).status_code == 404
        state = client.get("/api/status")
        assert state.json()["provider_ready"] is True
        for secret in KEYS.values():
            assert secret not in state.text
            assert secret not in client.get("/").text
        assert 'id="credentials-dialog"' not in client.get("/").text


def test_unicode_access_code_is_rejected_without_server_error(app):
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/login",
            json={"access_code": "mật-khẩu-không-đúng-của-tôi"},
            headers={"origin": ORIGIN},
        )
        assert response.status_code == 401


def test_full_workflow_uses_fixed_api_calls_and_server_credentials(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        response = client.post(
            "/api/verify",
            data={"mode": "full", "consent": "yes", "card_check": "true"},
            files=files(),
            headers=headers,
        )
        assert response.status_code == 202, response.text
        result = wait_result(client, response.json()["id"])
        assert result["status"] == "completed"
        assert result["calls"] == 10
        assert FakeVnpt.uploads == ["front", "back", "selfie"]
        assert len(result["checks"]) == 7
        assert all(item["status"] == "pass" for item in result["checks"])
        assert {item["label"] for item in result["fields"]} >= {
            "Họ và tên",
            "Số giấy tờ",
            "Ngày cấp",
        }
        for path, payload in FakeVnpt.calls:
            assert path.startswith("/ai/v1/")
            assert payload["client_session"].startswith("WEB_EKYC_LAB_")
            assert all(secret not in json.dumps(result) for secret in KEYS.values())
        assert "unsafe-inline" not in client.get("/").headers["content-security-policy"]


def test_ocr_only_never_uploads_portrait_or_calls_biometrics(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        response = client.post(
            "/api/verify", data={"mode": "ocr", "consent": "yes"}, files=files(), headers=headers
        )
        result = wait_result(client, response.json()["id"])
        assert result["calls"] == 4
        assert FakeVnpt.uploads == ["front", "back"]
        assert len(result["checks"]) == 2


def test_missing_consent_selfie_and_invalid_image_do_not_call_vnpt(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        common = {"mode": "full", "consent": "yes"}
        assert (
            client.post(
                "/api/verify",
                data={"mode": "full", "consent": "no"},
                files=files(),
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/verify", data=common, files=files(full=False), headers=headers
            ).status_code
            == 422
        )
        bad_files = files()
        bad_files["front"] = ("front.jpg", b"not an image", "image/jpeg")
        assert (
            client.post("/api/verify", data=common, files=bad_files, headers=headers).status_code
            == 422
        )
        assert FakeVnpt.uploads == []


def test_keys_not_echoed_in_validation_errors_and_logout_clears_state(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        result = client.post("/api/login", json={"access_code": KEYS}, headers={"origin": ORIGIN})
        assert result.status_code == 422
        assert KEYS["access_token"] not in result.text
        assert "input" not in result.json()
        assert client.post("/api/logout", headers=headers).status_code == 200
        assert client.get("/api/status").status_code == 401
        assert len(app.state.sessions) == 0


@pytest.mark.parametrize("expired", [False, True])
def test_unconfigured_or_expired_backend_credentials_never_call_vnpt(app, expired):
    config = dict(CONFIG)
    if expired:
        claims = base64.urlsafe_b64encode(json.dumps({"exp": 1}).encode()).decode().rstrip("=")
        config["VNPT_ACCESS_TOKEN"] = f"e30.{claims}.test-signature"
    else:
        config.pop("VNPT_TOKEN_KEY")
    unavailable = create_app(settings=config, client_factory=FakeVnpt)
    with TestClient(unavailable, base_url=ORIGIN) as client:
        headers = login(client)
        assert client.get("/api/status").json()["provider_ready"] is False
        response = client.post(
            "/api/verify",
            data={"mode": "ocr", "consent": "yes"},
            files=files(False),
            headers=headers,
        )
        assert response.status_code == 503
        assert FakeVnpt.uploads == []


def test_logging_out_does_not_remove_backend_credentials_for_new_sessions(app):
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        assert client.post("/api/logout", headers=headers).status_code == 200
        login(client)
        assert client.get("/api/status").json()["provider_ready"] is True


def test_result_isolation_and_ttl(app):
    with TestClient(app, base_url=ORIGIN) as first:
        headers = login(first)
        response = first.post(
            "/api/verify",
            data={"mode": "ocr", "consent": "yes"},
            files=files(False),
            headers=headers,
        )
        job_id = response.json()["id"]
        wait_result(first, job_id)
        with TestClient(app, base_url=ORIGIN) as other:
            login(other)
            assert other.get(f"/api/jobs/{job_id}").status_code == 404
            app.state.jobs[job_id].created = time.time() - 1300
            assert first.get(f"/api/jobs/{job_id}").status_code == 404


def test_upstream_error_stops_without_retries_and_retains_partial_status(app):
    FakeVnpt.error = True
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client)
        response = client.post(
            "/api/verify", data={"mode": "full", "consent": "yes"}, files=files(), headers=headers
        )
        result = wait_result(client, response.json()["id"])
        assert result["status"] == "error"
        assert result["calls"] == 4
        assert len(FakeVnpt.calls) == 1
        assert result["checks"] == []


@pytest.mark.parametrize(
    "key,obj,expected",
    [
        ("compare", {"msg": "NOMATCH", "prob": 99}, "fail"),
        ("compare", {"msg": "MATCH", "prob": "NaN"}, "review"),
        ("compare", {"msg": "MATCH"}, "review"),
        ("liveness", {}, "review"),
        ("liveness", {"liveness": "failure"}, "fail"),
        ("card_front", {"liveness": "success", "fake_liveness": True}, "fail"),
        ("mask", {}, "review"),
        ("ocr_front", {"msg": "OK"}, "review"),
        (
            "ocr_front",
            {"msg": "OK", "id": "test-id", "name": "TEST", "warning": ["blur"]},
            "review",
        ),
        (
            "ocr_back",
            {"msg": "OK", "issue_date": "01/01/2024", "tampering": {"is_legal": "no"}},
            "fail",
        ),
        ("ocr_back", {"msg": "OK"}, "review"),
    ],
)
def test_unknown_or_failed_responses_are_not_passes(key, obj, expected):
    assert check_result(key, obj)[0] == expected


def test_back_ocr_accepts_real_side_specific_response_without_personal_data():
    response = {
        "msg_back": "OK",
        "issue_date": "01/01/2024",
        "issue_place": "TEST ISSUING AUTHORITY",
        "mrz_valid_score": 100,
        "issue_date_prob": 0.9998,
        "issue_place_prob": 0.9999,
        "general_warning": [],
        "back_corner_warning": "no",
        "back_expire_warning": "no",
    }
    assert check_result("ocr_back", response) == ("pass", "Đã đọc thông tin giấy tờ.")


@pytest.mark.parametrize(
    "response,expected",
    [
        ({"msg": "OK", "issue_date": "01/01/2024"}, "pass"),
        ({"msg_back": "ERROR", "msg": "OK", "issue_date": "01/01/2024"}, "review"),
        ({"msg_back": None, "msg": "OK", "issue_date": "01/01/2024"}, "review"),
        ({"msg_back": "OK"}, "review"),
        ({"msg_back": "OK", "issue_date": "01/01/2024", "back_expire_warning": "yes"}, "fail"),
        ({"msg_back": "OK", "issue_date": "01/01/2024", "warning": ["blur"]}, "review"),
        ({"msg_back": "OK", "id": "test-id", "name": "TEST"}, "review"),
    ],
)
def test_back_status_compatibility_does_not_hide_missing_data_or_warnings(response, expected):
    assert check_result("ocr_back", response)[0] == expected


def test_client_headers_and_error_sanitization():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path == "/file-service/v1/addFile":
            return httpx.Response(
                200, json={"message": "IDG-00000000", "object": {"hash": "test-hash"}}
            )
        return httpx.Response(403, json={"secret": KEYS["access_token"]})

    async def run():
        client = VnptClient(Credentials(*KEYS.values()), transport=httpx.MockTransport(handle))
        assert await client.upload(image(), "front") == "test-hash"
        with pytest.raises(ProviderError) as caught:
            await client.check("/ai/v1/face/compare", {"img_front": "test-hash"})
        assert KEYS["access_token"] not in str(caught.value)
        assert seen[0].headers["authorization"] == "Bearer " + KEYS["access_token"]
        assert seen[0].url.host == "api.idg.vnpt.vn"
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "code,expected",
    [
        ("IDG-00010445", "loại giấy tờ"),
        ("IDG-00010003", "không tìm thấy khuôn mặt"),
    ],
)
def test_real_vnpt_input_errors_are_actionable_not_quota_errors(code, expected):
    async def run():
        client = VnptClient(
            Credentials(*KEYS.values()),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    400, json={"message": code, "errors": [KEYS["access_token"]]}
                )
            ),
        )
        with pytest.raises(ProviderError) as caught:
            await client.check("/ai/v1/ocr/id/front", {})
        assert expected in caught.value.safe_message
        assert KEYS["access_token"] not in caught.value.safe_message
        await client.close()

    asyncio.run(run())


def test_actual_image_decoder_and_metadata_normalization():
    result = normalize_image(image())
    with Image.open(io.BytesIO(result)) as decoded:
        assert decoded.format == "JPEG"
        assert not decoded.getexif()
    with pytest.raises(ValueError):
        normalize_image(b"<html>not an image</html>")
    with pytest.raises(ValueError):
        normalize_image(b"x" * (5 * 1024 * 1024 + 1))
