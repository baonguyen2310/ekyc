import asyncio
import base64
import hmac
import json
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .nfc import ReadIdClient, nfc_router
from .provider import (
    Credentials,
    ProviderError,
    VnptClient,
    check_result,
    extracted_fields,
    normalize_image,
)
from .sdk import SdkGateway, sdk_router
from .vnpt_services import vnpt_services_router

STATIC = Path(__file__).parent / "static"
COOKIE = "ekyc_lab_session"
SESSION_TTL = 8 * 3600
RESULT_TTL = 20 * 60
MAX_BODY_BYTES = 16 * 1024 * 1024


@dataclass(repr=False)
class Session:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    expires: float = field(default_factory=lambda: time.time() + SESSION_TTL)


@dataclass(repr=False)
class Job:
    id: str
    owner: str = field(repr=False)
    mode: str
    card_check: bool
    created: float = field(default_factory=time.time)
    status: str = "running"
    progress: str = "Đang chuẩn bị ảnh"
    calls: int = 0
    checks: list = field(default_factory=list, repr=False)
    fields: list = field(default_factory=list, repr=False)
    raw: dict = field(default_factory=dict, repr=False)
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def response(self):
        return {
            "id": self.id,
            "mode": self.mode,
            "card_check": self.card_check,
            "status": self.status,
            "progress": self.progress,
            "calls": self.calls,
            "checks": self.checks,
            "fields": self.fields,
            "raw": self.raw,
            "error": self.error,
            "expires_at": int(self.created + RESULT_TTL),
        }


class LoginBody(BaseModel):
    access_code: SecretStr = Field(min_length=16, max_length=256)


def create_app(
    *,
    settings=None,
    client_factory=VnptClient,
    sdk_gateway_factory=SdkGateway,
    nfc_client_factory=ReadIdClient,
):
    config = dict(os.environ) if settings is None else settings
    access_code = config.get("LAB_ACCESS_CODE", "")
    if len(access_code) < 32 or access_code.startswith("replace-"):
        raise RuntimeError("Configure a random LAB_ACCESS_CODE of at least 32 characters.")
    origin = config.get("PUBLIC_ORIGIN", "https://tools.opskoo.com").rstrip("/")
    secure = config.get("COOKIE_SECURE", "true").lower() == "true"
    values = [
        config.get(name, "").strip()
        for name in ("VNPT_TOKEN_ID", "VNPT_TOKEN_KEY", "VNPT_ACCESS_TOKEN")
    ]
    values[2] = values[2].removeprefix("Bearer ").removeprefix("bearer ")
    provider_credentials = Credentials(*values) if all(values) else None
    provider_expires_at = None
    if provider_credentials and len(values[2].split(".")) == 3:
        try:
            encoded = values[2].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            exp = claims.get("exp")
            if isinstance(exp, (int, float)) and not isinstance(exp, bool):
                provider_expires_at = int(exp)
        except (ValueError, TypeError, AttributeError):
            pass

    def provider_status():
        # exp is only a local expiry hint, not signature or identity verification.
        if provider_credentials is None:
            return False, "Dịch vụ chưa được cấu hình VNPT. Người quản trị cần kiểm tra backend."
        if provider_expires_at is not None and provider_expires_at <= time.time():
            return False, "Kết nối VNPT đã hết hạn. Người quản trị cần cập nhật cấu hình backend."
        return True, "VNPT API đã được cấu hình tại backend."

    sessions: dict[str, Session] = {}
    jobs: dict[str, Job] = {}
    attempts = defaultdict(deque)
    starts = deque()
    sdk_runs = {}

    def sweep():
        now = time.time()
        for key, job in list(jobs.items()):
            if job.created + RESULT_TTL < now and job.status != "running":
                jobs.pop(key, None)
        for key, session in list(sessions.items()):
            if session.expires < now:
                sessions.pop(key, None)
                for job_id, job in list(jobs.items()):
                    if job.owner == key:
                        if job.task and not job.task.done():
                            job.task.cancel()
                        jobs.pop(job_id, None)
        for key, queue in list(attempts.items()):
            while queue and queue[0] < now - 300:
                queue.popleft()
            if not queue:
                attempts.pop(key, None)
        sdk_sweep()
        nfc_sweep()

    async def cleanup():
        while True:
            await asyncio.sleep(30)
            sweep()

    @asynccontextmanager
    async def lifespan(app):
        cleaner = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            cleaner.cancel()
            tasks = [j.task for j in jobs.values() if j.task and not j.task.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(cleaner, *tasks, return_exceptions=True)
            sessions.clear()
            jobs.clear()
            sdk_runs.clear()
            await nfc_close()

    app = FastAPI(
        title="eKYC Lab", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo submitted credentials, images or request values in validation errors.
        return JSONResponse(
            {"detail": "Thông tin gửi lên chưa hợp lệ. Kiểm tra lại các trường và ảnh."},
            status_code=422,
        )

    @app.middleware("http")
    async def protect(request: Request, call_next):
        sweep()
        if request.url.path == "/sdk" and request.cookies.get(COOKIE) not in sessions:
            return JSONResponse({"detail": "Cần mở phiên eKYC Lab trước."}, status_code=401)
        if request.url.path.startswith("/api/"):
            if request.method not in {"GET", "HEAD"}:
                if request.headers.get("origin") != origin:
                    return JSONResponse(
                        {"detail": "Nguồn gửi yêu cầu không hợp lệ."}, status_code=403
                    )
                length = request.headers.get("content-length", "")
                if not length.isdigit() or int(length) > MAX_BODY_BYTES:
                    return JSONResponse(
                        {"detail": "Yêu cầu quá lớn hoặc thiếu kích thước."}, status_code=413
                    )
            if request.url.path != "/api/login":
                session = sessions.get(request.cookies.get(COOKIE, ""))
                if session is None:
                    return JSONResponse(
                        {"detail": "Phiên thử đã hết hạn. Vui lòng mở lại liên kết truy cập."},
                        status_code=401,
                    )
                if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(
                    request.headers.get("x-csrf-token", "").encode(), session.csrf.encode()
                ):
                    return JSONResponse(
                        {"detail": "Phiên gửi yêu cầu không hợp lệ."}, status_code=403
                    )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        is_sdk = request.url.path == "/sdk"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        if is_sdk:
            # The official React SDK inserts styles at runtime. Keep this exception
            # in its isolated document; the main Lab keeps its existing strict CSP.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; img-src 'self' blob: data:; "
                "media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    def current(request: Request):
        return sessions[request.cookies[COOKIE]]

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "service": "ekyc-lab"}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sdk")
    async def sdk_document():
        return FileResponse(STATIC / "sdk.html")

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request):
        ip = request.client.host if request.client else "unknown"
        queue = attempts[ip]
        if len(queue) >= 20:
            raise HTTPException(429, "Quá nhiều lần thử. Vui lòng đợi 5 phút.")
        queue.append(time.time())
        if not hmac.compare_digest(
            body.access_code.get_secret_value().encode(), access_code.encode()
        ):
            raise HTTPException(401, "Mã truy cập không đúng.")
        if len(sessions) >= 16:
            raise HTTPException(429, "Đang có nhiều phiên thử. Vui lòng thử lại sau.")
        old = request.cookies.get(COOKIE)
        if old and old in sessions:
            # Keep an existing active session and its running job.
            return {"ok": True}
        session = Session()
        sid = secrets.token_urlsafe(32)
        sessions[sid] = session
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE, sid, max_age=SESSION_TTL, secure=secure, httponly=True, samesite="strict"
        )
        return response

    @app.get("/api/status")
    async def status(request: Request, session: Annotated[Session, Depends(current)]):
        own = [j for j in jobs.values() if j.owner == request.cookies[COOKIE]]
        own_sdk = [r for r in sdk_runs.values() if r.owner == request.cookies[COOKIE]]
        latest_sdk = own_sdk[-1] if own_sdk else None
        ready, message = provider_status()
        return {
            "provider_ready": ready,
            "provider_message": message,
            "provider_expires_at": provider_expires_at,
            "csrf": session.csrf,
            "session_expires_at": int(session.expires),
            "latest_job": own[-1].id if own else None,
            "latest_sdk": latest_sdk.id if latest_sdk else None,
            "latest_sdk_by_flow": {run.flow: run.id for run in own_sdk},
            "sdk_version": "3.2.1.0",
        }

    @app.post("/api/logout")
    async def logout(request: Request):
        sid = request.cookies[COOKIE]
        sessions.pop(sid, None)
        nfc_sweep()
        for job_id, job in list(jobs.items()):
            if job.owner == sid:
                if job.task and not job.task.done():
                    job.task.cancel()
                jobs.pop(job_id, None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, secure=secure, httponly=True, samesite="strict")
        return response

    async def run_job(job: Job, credentials: Credentials, images: dict):
        client = client_factory(credentials)
        hashes = {}
        session_name = f"WEB_EKYC_LAB_Browser_1_{job.id}_{int(time.time())}"
        common = {"client_session": session_name, "token": secrets.token_hex(16)}
        try:
            async with asyncio.timeout(240):
                for key, image in images.items():
                    job.progress = {
                        "front": "Đang gửi ảnh mặt trước",
                        "back": "Đang gửi ảnh mặt sau",
                        "selfie": "Đang gửi ảnh chân dung",
                    }[key]
                    job.calls += 1
                    hashes[key] = await client.upload(image, key)
                images.clear()
                plan = [
                    (
                        "ocr_front",
                        "Đọc mặt trước",
                        "/ai/v1/ocr/id/front",
                        {"img_front": hashes["front"], "type": -1, "validate_postcode": True},
                    ),
                    (
                        "ocr_back",
                        "Đọc mặt sau",
                        "/ai/v1/ocr/id/back",
                        {"img_back": hashes["back"], "type": -1},
                    ),
                ]
                if job.mode == "full":
                    if job.card_check:
                        plan.extend(
                            [
                                (
                                    "card_front",
                                    "Kiểm tra giấy tờ mặt trước",
                                    "/ai/v1/card/liveness",
                                    {"img": hashes["front"]},
                                ),
                                (
                                    "card_back",
                                    "Kiểm tra giấy tờ mặt sau",
                                    "/ai/v1/card/liveness",
                                    {"img": hashes["back"]},
                                ),
                            ]
                        )
                    plan.extend(
                        [
                            (
                                "liveness",
                                "Kiểm tra người thật qua ảnh",
                                "/ai/v1/face/liveness",
                                {"img": hashes["selfie"]},
                            ),
                            (
                                "mask",
                                "Kiểm tra che mặt",
                                "/ai/v1/face/mask",
                                {"img": hashes["selfie"]},
                            ),
                            (
                                "compare",
                                "So khớp khuôn mặt",
                                "/ai/v1/face/compare",
                                {"img_front": hashes["front"], "img_face": hashes["selfie"]},
                            ),
                        ]
                    )
                for key, label, path, payload in plan:
                    job.progress = label
                    job.calls += 1
                    result = await client.check(path, {**common, **payload})
                    job.raw[key] = result
                    state, message = check_result(key, result)
                    job.checks.append(
                        {"key": key, "label": label, "status": state, "message": message}
                    )
                    job.fields = extracted_fields(
                        job.raw.get("ocr_front", {}), job.raw.get("ocr_back", {})
                    )
                job.status = "completed"
                job.progress = "Đã nhận kết quả từ VNPT"
        except ProviderError as exc:
            job.status = "error"
            job.error = exc.safe_message
            job.progress = "Chưa hoàn tất kiểm tra"
        except TimeoutError:
            job.status = "error"
            job.error = "Lượt kiểm tra vượt thời gian cho phép. Kết quả chưa đầy đủ."
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception:
            job.status = "error"
            job.error = "Không xử lý được lượt kiểm tra. Kết quả chưa đầy đủ."
        finally:
            images.clear()
            hashes.clear()
            await client.close()

    @app.post("/api/verify", status_code=202)
    async def verify(
        request: Request,
        mode: Annotated[Literal["ocr", "full"], Form()],
        consent: Annotated[str, Form()],
        front: Annotated[UploadFile, File()],
        back: Annotated[UploadFile, File()],
        session: Annotated[Session, Depends(current)],
        card_check: Annotated[bool, Form()] = True,
        selfie: Annotated[UploadFile | None, File()] = None,
    ):
        if consent != "yes":
            raise HTTPException(422, "Cần đồng ý gửi ảnh đến VNPT trước khi kiểm tra.")
        ready, message = provider_status()
        if not ready:
            raise HTTPException(503, message)
        if mode == "full" and selfie is None:
            raise HTTPException(422, "Cần có ảnh chân dung cho chế độ kiểm tra đầy đủ.")
        sid = request.cookies[COOKIE]
        if any(j.status == "running" and j.owner == sid for j in jobs.values()) or any(
            run.active and run.owner == sid for run in sdk_runs.values()
        ):
            raise HTTPException(409, "Một lượt kiểm tra đang chạy. Vui lòng đợi kết quả.")
        if (
            sum(j.status == "running" for j in jobs.values())
            + sum(run.active for run in sdk_runs.values())
            >= 2
        ):
            raise HTTPException(429, "Đang có nhiều lượt kiểm tra. Vui lòng thử lại sau.")
        if sum(j.owner == sid for j in jobs.values()) >= 10:
            raise HTTPException(429, "Phiên thử đã đạt giới hạn 10 lượt trong 20 phút.")
        now = time.time()
        while starts and starts[0] < now - 3600:
            starts.popleft()
        if len(starts) >= 20:
            raise HTTPException(
                429, "Service đã đạt giới hạn 20 lượt mỗi giờ để bảo vệ hạn mức thử."
            )
        images = {}
        uploaded = {"front": front, "back": back}
        if mode == "full":
            uploaded["selfie"] = selfie
        try:
            for key, upload in uploaded.items():
                data = await upload.read(5 * 1024 * 1024 + 1)
                images[key] = await asyncio.to_thread(normalize_image, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            for upload in (front, back, selfie):
                if upload:
                    await upload.close()
        # Validation awaits above: recheck admission before inserting a task.
        if sid not in sessions:
            raise HTTPException(401, "Phiên thử đã kết thúc.")
        if (
            any(j.owner == sid and j.status == "running" for j in jobs.values())
            or any(run.active and run.owner == sid for run in sdk_runs.values())
            or sum(j.status == "running" for j in jobs.values())
            + sum(run.active for run in sdk_runs.values())
            >= 2
        ):
            raise HTTPException(409, "Một lượt kiểm tra khác vừa bắt đầu. Vui lòng đợi.")
        if len(starts) >= 20:
            raise HTTPException(429, "Service đã đạt giới hạn lượt thử mỗi giờ.")
        starts.append(now)
        job = Job(secrets.token_hex(16), sid, mode, card_check)
        jobs[job.id] = job
        job.task = asyncio.create_task(run_job(job, provider_credentials, images))
        return {"id": job.id}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        job = jobs.get(job_id)
        if job is None or job.owner != request.cookies[COOKIE]:
            raise HTTPException(404, "Kết quả không tồn tại hoặc đã hết thời gian lưu tạm.")
        return job.response()

    sdk_routes, sdk_runs, sdk_sweep = sdk_router(
        cookie=COOKIE,
        sessions=sessions,
        credentials=provider_credentials,
        provider_status=provider_status,
        starts=starts,
        jobs=jobs,
        gateway_factory=sdk_gateway_factory,
    )
    app.include_router(sdk_routes)
    nfc_routes, nfc_sweep, nfc_close = nfc_router(
        config=config,
        cookie=COOKIE,
        sessions=sessions,
        origin=origin,
        client_factory=nfc_client_factory,
    )
    app.include_router(nfc_routes)
    app.include_router(vnpt_services_router())
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # Keep private state inspectable in unit tests without exposing it through HTTP.
    app.state.jobs = jobs
    app.state.sessions = sessions
    app.state.sdk_runs = sdk_runs
    return app
