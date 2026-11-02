"""Session-bound gateway for the official VNPT Web SDK. Provider keys stay server-side."""

import json
import re
import secrets
import time
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .provider import BASE_URL, SUCCESS, check_result, extracted_fields

SDK_TTL = 20 * 60
MAX_CALLS = 14
UPLOAD = "/file-service/v1/addFile"
RULES = {
    "/ai/v1/web/card/liveness": ("img",),
    "/ai/v1/web/ocr/id": ("img_front", "img_back"),
    "/ai/v1/web/ocr/id/front": ("img_front",),
    "/ai/v1/web/face/liveness-3d": ("far_img", "near_img", "scan3d"),
    "/ai/v1/web/face/mask": ("img",),
    "/ai/v1/web/face/compare": ("img_front", "img_face"),
}


class SdkConsent(BaseModel):
    consent: bool


def without_media(value):
    """Never retain captured base64, scan data or tokens in result history."""
    if isinstance(value, dict):
        return {
            key: without_media(item)
            for key, item in value.items()
            if not any(word in key.lower() for word in ("base64", "authorization", "token"))
            and key.lower() not in {"scan3d", "image", "img", "video"}
        }
    if isinstance(value, list):
        return [without_media(item) for item in value]
    if isinstance(value, str) and (value.startswith("data:") or len(value) > 20_000):
        return "[Dữ liệu ảnh/quét không lưu trong kết quả]"
    return value


@dataclass(repr=False)
class SdkRun:
    id: str
    owner: str = field(repr=False)
    created: float = field(default_factory=time.time)
    active: bool = True
    calls: int = 0
    inflight: int = 0
    hashes: set = field(default_factory=set, repr=False)
    records: list = field(default_factory=list, repr=False)
    checks: dict = field(default_factory=dict, repr=False)
    raw: dict = field(default_factory=dict, repr=False)
    failures: dict = field(default_factory=dict, repr=False)
    error: str | None = None

    def response(self):
        checks = list(self.checks.values())
        if not self.active:
            for key, label in (
                ("ocr_front", "Đọc giấy tờ"),
                ("liveness", "Kiểm tra người thật · Oval gần–xa"),
                ("compare", "So khớp khuôn mặt"),
            ):
                if key not in self.checks:
                    checks.append(
                        {
                            "key": key,
                            "label": label,
                            "status": "review",
                            "message": "Bước này chưa có kết quả từ VNPT trong lượt kiểm tra.",
                        }
                    )
        ocr = self.raw.get("ocr", {})
        return {
            "id": self.id,
            "mode": "sdk",
            "status": "running"
            if self.active or self.inflight
            else "error"
            if self.error
            else "completed",
            "progress": "Đang kiểm tra bằng Web SDK" if self.active else "Đã kết thúc lượt Web SDK",
            "calls": self.calls,
            "checks": checks,
            "fields": extracted_fields(ocr, ocr),
            "raw": self.raw,
            "requests": self.records,
            "error": self.error,
            "expires_at": int(self.created + SDK_TTL),
        }


class SdkGateway:
    def __init__(self, credentials, *, transport=None):
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "Token-id": credentials.token_id,
                "Token-key": credentials.token_key,
                "Authorization": f"Bearer {credentials.access_token}",
                "mac-address": "EKYCLAB-WEBSDK",
            },
            timeout=httpx.Timeout(60, connect=10),
            follow_redirects=False,
            transport=transport,
        )

    async def send(self, path, **kwargs):
        return await self.client.post(path, params={"challengeCode": "00000"}, **kwargs)

    async def close(self):
        await self.client.aclose()


def sdk_router(*, cookie, sessions, credentials, provider_status, starts, jobs, gateway_factory):
    router = APIRouter()
    runs: dict[str, SdkRun] = {}

    def sweep():
        now = time.time()
        for key, run in list(runs.items()):
            if run.created + SDK_TTL < now or run.owner not in sessions:
                runs.pop(key, None)

    def owned(run_id, request, *, active=False):
        sweep()
        run = runs.get(run_id)
        if run is None or run.owner != request.cookies.get(cookie):
            raise HTTPException(404, "Lượt Web SDK không tồn tại hoặc đã hết hạn.")
        if active and not run.active:
            raise HTTPException(409, "Lượt Web SDK đã kết thúc. Bắt đầu lượt mới để kiểm tra lại.")
        return run

    @router.post("/api/sdk/runs", status_code=201)
    async def start(body: SdkConsent, request: Request):
        if not body.consent:
            raise HTTPException(422, "Cần đồng ý gửi dữ liệu camera đến VNPT trước khi bắt đầu.")
        ready, message = provider_status()
        if not ready:
            raise HTTPException(503, message)
        sweep()
        sid = request.cookies[cookie]
        if any(r.active and r.owner == sid for r in runs.values()) or any(
            j.status == "running" and j.owner == sid for j in jobs.values()
        ):
            raise HTTPException(409, "Một lượt kiểm tra đang chạy. Kết thúc lượt đó trước.")
        if (
            sum(r.active for r in runs.values()) + sum(j.status == "running" for j in jobs.values())
            >= 2
        ):
            raise HTTPException(429, "Đang có nhiều lượt kiểm tra. Vui lòng thử lại sau.")
        now = time.time()
        while starts and starts[0] < now - 3600:
            starts.popleft()
        if len(starts) >= 20:
            raise HTTPException(429, "Service đã đạt giới hạn 20 lượt mỗi giờ.")
        starts.append(now)
        run = SdkRun(secrets.token_hex(16), sid)
        runs[run.id] = run
        return {"id": run.id, "max_calls": MAX_CALLS, "expires_at": int(now + SDK_TTL)}

    @router.get("/api/sdk/runs/{run_id}")
    async def result(run_id: str, request: Request):
        return owned(run_id, request).response()

    @router.get("/api/sdk/runs/{run_id}/config")
    async def config(run_id: str, request: Request):
        run = owned(run_id, request, active=True)
        return {
            "BACKEND_URL": f"/api/sdk/runs/{run.id}/proxy",
            "TOKEN_ID": "",
            "TOKEN_KEY": "",
            "ACCESS_TOKEN": "",
            "HEADERS_REQUEST": {"X-CSRF-Token": sessions[run.owner].csrf},
            "SDK_FLOW": "DOCUMENT_TO_FACE",
            "DOCUMENT_TYPE_START": 9,
            "LIST_TYPE_DOCUMENT": [-1, 9],
            "HAS_QR_SCAN": False,
            "DOUBLE_LIVENESS": True,
            "FACE_SCAN_RECORD": False,
            "USE_METHOD": "PHOTO",
            "HAS_RESULT_SCREEN": False,
            "HAS_BACKGROUND_IMAGE": False,
            "DEFAULT_LANGUAGE": "vi",
            "MAX_SIZE_IMAGE": 1,
            "CUSTOM_THEME": {
                "PRIMARY_COLOR": "#155bd7",
                "TEXT_COLOR_DEFAULT": "#172334",
                "BACKGROUND_COLOR": "#ffffff",
            },
            "ENABLE_API_LIVENESS_FACE": True,
            "CHALLENGE_CODE": "00000",
            "URL_WEB_OVAL": "/static/vendor/vnpt-3.2.1.0/lib/web-oval.json",
            "URL_MOBILE_OVAL": "/static/vendor/vnpt-3.2.1.0/lib/mobile-oval.json",
            "URL_VIETNAMESE_VIDEO_TUTORIAL": "/static/vendor/vnpt-3.2.1.0/lib/vietnamese-tutorial.mp4",
            "URL_ENGLISH_VIDEO_TUTORIAL": "/static/vendor/vnpt-3.2.1.0/lib/english-tutorial.mp4",
        }

    @router.post("/api/sdk/runs/{run_id}/finish")
    async def finish(run_id: str, request: Request):
        run = owned(run_id, request)
        run.active = False
        return run.response()

    def record_check(run, key, label, obj):
        state, message = check_result(key, obj)
        if key == "liveness" and state == "pass":
            message = "Đạt kiểm tra liveness oval gần–xa của VNPT."
        run.checks[key] = {"key": key, "label": label, "status": state, "message": message}

    @router.post("/api/sdk/runs/{run_id}/proxy/{path:path}")
    async def proxy(run_id: str, path: str, request: Request):
        run = owned(run_id, request, active=True)
        endpoint = "/" + path
        if endpoint != UPLOAD and endpoint not in RULES:
            raise HTTPException(404, "Endpoint không được phép trong eKYC Lab.")
        ready, message = provider_status()
        if not ready:
            raise HTTPException(503, message)
        if run.calls >= MAX_CALLS:
            raise HTTPException(
                429, "Lượt Web SDK đã đạt giới hạn 14 request. Kết thúc để xem kết quả."
            )
        if run.inflight >= 4:
            raise HTTPException(429, "Đang có nhiều request trong lượt kiểm tra.")
        run.inflight += 1
        gateway = None
        try:
            if endpoint == UPLOAD:
                async with request.form(
                    max_files=1, max_fields=3, max_part_size=5 * 1024 * 1024
                ) as form:
                    upload = form.get("file")
                    if not hasattr(upload, "read"):
                        raise HTTPException(422, "Thiếu tệp do Web SDK ghi nhận.")
                    data = await upload.read(5 * 1024 * 1024 + 1)
                    if not data or len(data) > 5 * 1024 * 1024:
                        raise HTTPException(413, "Tệp Web SDK vượt giới hạn 5 MB.")
                    # The SDK uploads both canvas images and its opaque scan3d blob.
                    content_type = upload.content_type or "application/octet-stream"
                    if content_type not in {
                        "image/jpeg",
                        "image/png",
                        "text",
                        "text/plain",
                        "application/octet-stream",
                    }:
                        raise HTTPException(422, "Định dạng tệp Web SDK không được hỗ trợ.")
                    kwargs = {
                        "files": {"file": ("blob", data, content_type)},
                        "data": {"title": "upload_file", "description": "ic_upload_file"},
                    }
            else:
                raw = await request.body()
                if len(raw) > 16_384:
                    raise HTTPException(413, "Payload kiểm tra vượt giới hạn.")
                try:
                    payload = json.loads(raw)
                except (ValueError, TypeError) as exc:
                    raise HTTPException(422, "Payload Web SDK không hợp lệ.") from exc
                if not isinstance(payload, dict):
                    raise HTTPException(422, "Payload Web SDK không hợp lệ.")
                fields = RULES[endpoint]
                if any(
                    not isinstance(payload.get(key), str) or payload[key] not in run.hashes
                    for key in fields
                ):
                    raise HTTPException(
                        422, "Chỉ kiểm tra tệp đã gửi trong chính lượt Web SDK này."
                    )
                clean = {key: payload[key] for key in fields}
                if "/ocr/" in endpoint:
                    doc_type = payload.get("type", -1)
                    if isinstance(doc_type, bool) or doc_type not in {-1, 9}:
                        raise HTTPException(422, "Luồng này chỉ hỗ trợ CCCD/CMND.")
                    clean.update(type=doc_type, validate_postcode=True)
                if "/ocr/" in endpoint or "/card/" in endpoint:
                    clean["crop_param"] = "0,0"
                client_session = payload.get("client_session", "")
                if not isinstance(client_session, str) or not re.fullmatch(
                    r"WEB-SDK_[A-Za-z0-9._-]{1,220}", client_session
                ):
                    raise HTTPException(422, "Thiếu mã phiên do VNPT Web SDK tạo.")
                # Preserve SDK metadata and its public request marker for scan3d compatibility.
                # Provider authentication still comes exclusively from backend credentials.
                clean.update(client_session=client_session, token="e41-1b6d-45c9-9")
                kwargs = {"json": clean}
            # Admission is rechecked after reading the request body.
            owned(run_id, request, active=True)
            if run.calls >= MAX_CALLS:
                raise HTTPException(429, "Đã đạt giới hạn request của lượt Web SDK.")
            run.calls += 1
            gateway = gateway_factory(credentials)
            response = await gateway.send(endpoint, **kwargs)
            if len(response.content) > 2_000_000:
                raise ValueError("Oversized upstream response")
            # A vendor error must not reflect our injected provider keys back to the browser.
            text = response.text
            for secret in (credentials.token_id, credentials.token_key, credentials.access_token):
                text = text.replace(secret, "[REDACTED]")
            body = json.loads(text)
            if not isinstance(body, dict):
                raise ValueError("Invalid upstream envelope")
            obj = body.get("object")
            if isinstance(obj, str):
                obj = json.loads(obj)
                body["object"] = obj
            code = body.get("message", "UNKNOWN")
            code = (
                code if isinstance(code, str) and re.fullmatch(r"IDG-[0-9]{8}", code) else "UNKNOWN"
            )
            run.records.append(
                {"endpoint": endpoint, "http_status": response.status_code, "code": code}
            )
            if 200 <= response.status_code < 300 and code == SUCCESS and isinstance(obj, dict):
                run.failures.pop(endpoint, None)
                run.error = next(iter(run.failures.values()), None)
                if endpoint == UPLOAD:
                    value = obj.get("hash")
                    if not isinstance(value, str) or not value or len(value) > 2048:
                        raise ValueError("Missing upload hash")
                    run.hashes.add(value)
                elif "/ocr/" in endpoint:
                    run.raw["ocr"] = without_media(obj)
                    record_check(run, "ocr_front", "Đọc mặt trước", obj)
                    if endpoint.endswith("/id"):
                        record_check(run, "ocr_back", "Đọc mặt sau", obj)
                else:
                    key, label = (
                        ("liveness", "Kiểm tra người thật · Oval gần–xa")
                        if endpoint.endswith("liveness-3d")
                        else ("mask", "Kiểm tra che mặt")
                        if endpoint.endswith("/mask")
                        else ("compare", "So khớp khuôn mặt")
                        if endpoint.endswith("/compare")
                        else (
                            f"card_{'front' if 'card_front' not in run.checks else 'back'}",
                            "Kiểm tra giấy tờ",
                        )
                    )
                    run.raw[key] = without_media(obj)
                    record_check(run, key, label, obj)
            else:
                run.error = f"VNPT chưa hoàn tất bước {endpoint.rsplit('/', 1)[-1]} (HTTP {response.status_code}, mã {code})."
                run.failures[endpoint] = run.error
                run.raw.setdefault("errors", []).append(
                    {"endpoint": endpoint, "http_status": response.status_code, "code": code}
                )
            # Only the JSON body is forwarded, never upstream headers or client-supplied credentials.
            return JSONResponse(body, status_code=response.status_code)
        except (httpx.RequestError, ValueError, TypeError):
            run.error = "Chưa nhận được phản hồi hợp lệ từ VNPT. Không tự động gửi lại để tránh tốn hạn mức."
            run.failures[endpoint] = run.error
            return JSONResponse(
                {"message": "LAB_UPSTREAM_ERROR", "error": run.error}, status_code=502
            )
        finally:
            run.inflight -= 1
            if gateway:
                await gateway.close()

    return router, runs, sweep
