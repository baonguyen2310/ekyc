"""ReadID Ready through Signicat Assure. No live calls at startup or on GET."""

import asyncio
import base64
import json
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

TTL = 20 * 60
FINAL = {"accepted", "rejected", "inconclusive", "canceled", "failed"}
API = "https://api.signicat.com"
FIELDS = {
    "firstName": "Tên",
    "lastName": "Họ",
    "dateOfBirth": "Ngày sinh",
    "documentNumber": "Số giấy tờ",
    "personalIdentificationNumber": "Số định danh",
    "documentType": "Loại giấy tờ",
    "gender": "Giới tính",
    "nationality": "Quốc tịch",
    "issuingCountry": "Nước cấp",
    "dateOfExpiry": "Hết hạn",
}


class NfcError(ValueError):
    pass


def identifier(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise NfcError("Signicat trả mã phiên không hợp lệ.") from None


@dataclass(repr=False)
class ReadIdSettings:
    client_id: str = ""
    secret: str = ""
    domain: str = ""
    enabled: bool = False

    @classmethod
    def from_config(cls, config):
        return cls(
            config.get("SIGNICAT_CLIENT_ID", "").strip(),
            config.get("SIGNICAT_CLIENT_SECRET", "").strip(),
            config.get("SIGNICAT_CAPTURE_DOMAIN", "").strip().lower(),
            config.get("SIGNICAT_NFC_ENABLED", "false").lower() == "true",
        )

    def status(self):
        if not self.enabled or not self.client_id or not self.secret or not self.domain:
            return (
                False,
                "Chưa cấu hình tài khoản sandbox Signicat có quyền ReadID Ready tại backend.",
            )
        if not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.sandbox\.signicat\.com", self.domain
        ):
            return (
                False,
                "Cần domain sandbox do Signicat cấp, dạng ten-tai-khoan.sandbox.signicat.com.",
            )
        return (
            True,
            "Đã có cấu hình sandbox; quyền ReadID Ready chưa được kiểm chứng bằng lượt thật.",
        )


class ReadIdClient:
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=API,
            timeout=20,
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self.token = ""
        self.token_until = 0
        self.auth_lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()
        self.token = ""

    async def request(self, method, path, label, log, **kwargs):
        entry = {"label": label, "http": None, "outcome": "error", "duration_ms": 0}
        log.append(entry)
        started = time.monotonic()
        try:
            async with self.http.stream(method, path, **kwargs) as response:
                entry["http"] = response.status_code
                if response.status_code not in {200, 201}:
                    if response.status_code == 401:
                        self.token = ""
                        self.token_until = 0
                    messages = {
                        401: "Signicat từ chối xác thực. Kiểm tra API key ở backend.",
                        403: "Tài khoản chưa có quyền Assure API hoặc ReadID Ready.",
                        429: "Signicat đang giới hạn request. Lab không tự thử lại.",
                    }
                    raise NfcError(
                        messages.get(
                            response.status_code,
                            "Signicat chưa xử lý được yêu cầu. Lab không tự thử lại.",
                        )
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1024 * 1024:
                        raise NfcError("Phản hồi Signicat vượt giới hạn đọc của Lab.")
                try:
                    body = json.loads(content)
                except (ValueError, UnicodeError):
                    raise NfcError("Signicat chưa trả JSON hợp lệ.") from None
                if not isinstance(body, dict):
                    raise NfcError("Cấu trúc phản hồi Signicat chưa hợp lệ.")
                entry["outcome"] = "ok"
                return body
        except httpx.TimeoutException:
            raise NfcError(
                "Signicat phản hồi quá lâu. Request có thể đã được xử lý; Lab không tự gửi lại."
            ) from None
        except httpx.RequestError:
            raise NfcError("Mất kết nối với Signicat. Lab không tự gửi lại.") from None
        finally:
            entry["duration_ms"] = round((time.monotonic() - started) * 1000)

    async def authorize(self, log):
        async with self.auth_lock:
            if self.token and time.monotonic() < self.token_until:
                return
            body = await self.request(
                "POST",
                "/auth/open/connect/token",
                "Xác thực backend Signicat",
                log,
                auth=httpx.BasicAuth(self.settings.client_id, self.settings.secret),
                data={"grant_type": "client_credentials", "scope": "signicat-api"},
            )
            token = body.get("access_token")
            try:
                # This token comes directly from the fixed HTTPS OAuth endpoint,
                # never from the browser. Inspect its environment to refuse live keys.
                part = token.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
                if claims.get("sandbox") is not True:
                    raise ValueError
                expires = float(body["expires_in"])
                if expires <= 30 or body.get("token_type", "").lower() != "bearer":
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                raise NfcError(
                    "Lab chỉ chấp nhận token sandbox từ Signicat; đã chặn trước khi tạo lượt NFC."
                ) from None
            self.token, self.token_until = token, time.monotonic() + min(expires, 3600) - 30

    async def call(self, method, path, label, log, **kwargs):
        await self.authorize(log)
        return await self.request(
            method, path, label, log, headers={"Authorization": "Bearer " + self.token}, **kwargs
        )

    async def create_dossier(self, run):
        body = await self.call(
            "POST",
            "/assure/dossiers",
            "Tạo phiên NFC",
            run.log,
            json={"externalReference": "ekyc-lab-" + run.id},
        )
        return identifier(body.get("dossierId"))

    async def capture(self, run, origin):
        body = await self.call(
            "POST",
            f"/assure/dossiers/{run.dossier}/capture",
            "Tạo liên kết ReadID Ready",
            run.log,
            json={
                "providers": [{"provider": "readid", "processType": "ready"}],
                "sdk": "native",
                "redirectUrl": origin + "/?tab=nfc",
                "requestDomain": self.settings.domain,
            },
        )
        url = body.get("url")
        try:
            parsed = urlsplit(url)
            valid = (
                isinstance(url, str)
                and len(url) <= 4096
                and not any(ord(char) <= 32 or ord(char) == 127 for char in url)
                and parsed.scheme == "https"
                and parsed.hostname == self.settings.domain
                and parsed.port in {None, 443}
                and not parsed.username
                and not parsed.password
                and parsed.path.startswith(("/capture/", "/assure/capture/"))
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise NfcError(
                "Liên kết ReadID không khớp domain sandbox đã cấu hình. Lab không mở liên kết này."
            )
        return url

    async def result(self, run):
        if not run.process:
            dossier = await self.call(
                "GET", f"/assure/dossiers/{run.dossier}", "Tìm lượt ReadID trong phiên", run.log
            )
            if identifier(dossier.get("dossierId")) != run.dossier:
                raise NfcError("Signicat trả kết quả của phiên khác.")
            processes = dossier.get("processes")
            if not isinstance(processes, list):
                raise NfcError("Signicat chưa trả danh sách lượt kiểm tra hợp lệ.")
            matching = [
                p
                for p in processes
                if isinstance(p, dict)
                and p.get("provider") == "readid"
                and p.get("processType") == "ready"
            ]
            if not matching:
                return None
            if len(matching) != 1:
                raise NfcError("Có nhiều lượt ReadID trong phiên; Lab chưa thể chọn đúng kết quả.")
            run.process = identifier(matching[0].get("processId"))
        body = await self.call(
            "GET",
            f"/assure/dossiers/{run.dossier}/processes/{run.process}",
            "Lấy kết quả NFC",
            run.log,
        )
        if (
            identifier(body.get("processId")) != run.process
            or body.get("provider") != "readid"
            or body.get("processType") != "ready"
        ):
            raise NfcError("Kết quả không thuộc đúng lượt ReadID Ready đã tạo.")
        return body


def result_summary(body):
    """Allowlist only data useful to this experiment; no media, URLs or tokens."""
    specific = body.get("providerSpecific", {})
    identity = specific.get("consolidatedIdentityData", {}) if isinstance(specific, dict) else {}
    final = body.get("finalResult", {})
    if not isinstance(identity, dict) or not isinstance(final, dict):
        raise NfcError("Cấu trúc kết quả ReadID chưa hợp lệ.")

    def safe(value):
        return value[:500] if isinstance(value, str) else None

    data = {key: safe(final.get(key) or identity.get(key)) for key in FIELDS}
    checks = []
    for key, label in (
        ("chipVerification", "Kiểm tra chữ ký và dữ liệu chip"),
        ("chipCloneDetection", "Kiểm tra chip sao chép"),
    ):
        value = identity.get(key)
        checks.append(
            {
                "label": label,
                "state": "pass"
                if value == "SUCCEEDED"
                else "fail"
                if value == "FAILED"
                else "review",
                "value": safe(value) or "Chưa có kết quả",
            }
        )
    return {
        "fields": [{"label": FIELDS[key], "value": value} for key, value in data.items() if value],
        "checks": checks,
        "raw": {
            "status": safe(body.get("status")),
            "provider": "readid",
            "processType": "ready",
            "finalResult": {k: v for k, v in data.items() if v},
            "chipVerification": safe(identity.get("chipVerification")),
            "chipCloneDetection": safe(identity.get("chipCloneDetection")),
        },
    }


@dataclass(repr=False)
class NfcRun:
    id: str
    owner: str = field(repr=False)
    created: float = field(default_factory=time.time)
    dossier: str | None = None
    process: str | None = None
    capture_url: str | None = field(default=None, repr=False)
    status: str = "creating"
    error: str | None = None
    busy: bool = False
    refreshes: int = 0
    last_refresh: float = 0
    summary: dict = field(default_factory=dict, repr=False)
    log: list = field(default_factory=list, repr=False)

    def response(self):
        return {
            "id": self.id,
            "status": self.status,
            "capture_url": self.capture_url if self.status == "pending" else None,
            "error": self.error,
            "sandbox": True,
            "calls": len(self.log),
            "log": self.log,
            "expires_at": int(self.created + TTL),
            **self.summary,
        }


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: bool


def nfc_router(*, config, cookie, sessions, origin, client_factory=ReadIdClient):
    router = APIRouter(prefix="/api/nfc")
    settings = ReadIdSettings.from_config(config)
    runs = {}
    starts = deque()
    client = None

    def get_client():
        nonlocal client
        ready, message = settings.status()
        if not ready:
            raise HTTPException(503, message)
        if client is None:
            client = client_factory(settings)
        return client

    def own(run_id, request):
        run = runs.get(run_id)
        if run is None or run.owner != request.cookies[cookie]:
            raise HTTPException(404, "Lượt NFC không tồn tại hoặc đã hết thời gian lưu tạm.")
        return run

    def sweep():
        for key, run in list(runs.items()):
            if run.created + TTL < time.time() or run.owner not in sessions:
                runs.pop(key, None)

    async def close():
        if client:
            await client.close()
        runs.clear()

    @router.get("/status")
    async def status(request: Request):
        ready, message = settings.status()
        own_runs = [r for r in runs.values() if r.owner == request.cookies[cookie]]
        return {
            "ready": ready,
            "message": message,
            "sandbox": True,
            "latest_run": own_runs[-1].response() if own_runs else None,
        }

    @router.post("/runs", status_code=201)
    async def start(body: Consent, request: Request):
        if body.consent is not True:
            raise HTTPException(422, "Cần đồng ý chuyển sang Signicat trước khi tạo lượt NFC.")
        provider = get_client()
        sid = request.cookies[cookie]
        if any(r.owner == sid and r.status not in FINAL | {"error"} for r in runs.values()):
            raise HTTPException(409, "Một lượt NFC đang mở. Dùng lại liên kết của lượt đó.")
        if sum(r.busy for r in runs.values()) >= 2:
            raise HTTPException(429, "Có nhiều yêu cầu NFC đang chạy. Thử lại sau.")
        while starts and starts[0] < time.time() - 3600:
            starts.popleft()
        if len(starts) >= 20:
            raise HTTPException(429, "Đã đạt giới hạn 20 lượt NFC/giờ của Lab.")
        starts.append(time.time())
        run = NfcRun(secrets.token_hex(16), sid, busy=True)
        runs[run.id] = run
        try:
            async with asyncio.timeout(25):
                run.dossier = await provider.create_dossier(run)
                run.capture_url = await provider.capture(run, origin)
                run.status = "pending"
        except (NfcError, TimeoutError) as error:
            run.status = "error"
            run.error = (
                str(error)
                if isinstance(error, NfcError)
                else "Hết thời gian chờ Signicat. Không tự tạo lượt khác; request trước có thể đã được xử lý."
            )
        finally:
            run.busy = False
        return run.response()

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        return own(run_id, request).response()

    @router.post("/runs/{run_id}/refresh")
    async def refresh(run_id: str, request: Request):
        run = own(run_id, request)
        if run.status in FINAL | {"error"}:
            return run.response()
        if run.busy or sum(r.busy for r in runs.values()) >= 2:
            raise HTTPException(409, "Đang chờ kết quả của yêu cầu NFC trước.")
        if run.refreshes >= 15 or time.monotonic() - run.last_refresh < 5:
            raise HTTPException(
                429, "Chờ ít nhất 5 giây giữa các lần cập nhật; tối đa 15 lần/lượt NFC."
            )
        run.last_refresh, run.refreshes, run.busy = time.monotonic(), run.refreshes + 1, True
        run.error = None
        try:
            async with asyncio.timeout(25):
                body = await get_client().result(run)
                if body is not None:
                    if body.get("status") not in FINAL | {"pending", "processing"}:
                        raise NfcError("Signicat trả trạng thái chưa được Lab hỗ trợ.")
                    run.summary = result_summary(body)
                    run.status = body["status"]
        except (NfcError, TimeoutError) as error:
            run.error = (
                str(error)
                if isinstance(error, NfcError)
                else "Hết thời gian lấy kết quả Signicat. Không tự gửi lại."
            )
        finally:
            run.busy = False
        return run.response()

    return router, sweep, close
