"""VNPT's documented server-side API; no arbitrary upstream URLs or face database writes."""

import io
import json
import math
import re
from dataclasses import dataclass, field

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

BASE_URL = "https://api.idg.vnpt.vn"
SUCCESS = "IDG-00000000"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20_000_000


@dataclass(repr=False)
class Credentials:
    token_id: str = field(repr=False)
    token_key: str = field(repr=False)
    access_token: str = field(repr=False)


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.safe_message = message


def normalize_image(data: bytes) -> bytes:
    """Decode the actual image, bound dimensions, remove metadata and emit JPEG."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Mỗi ảnh cần có dung lượng từ 1 byte đến 5 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Chỉ hỗ trợ ảnh JPG, PNG và WebP.")
            if source.width * source.height > 20_000_000:
                raise ValueError("Ảnh quá lớn. Vui lòng dùng ảnh dưới 20 megapixel.")
            if min(source.size) < 240:
                raise ValueError("Ảnh quá nhỏ. Vui lòng chụp lại rõ hơn.")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((2400, 2400))
            result = io.BytesIO()
            image.save(result, "JPEG", quality=92)
            if result.tell() > MAX_IMAGE_BYTES:
                raise ValueError("Ảnh quá lớn sau khi xử lý. Vui lòng chụp lại.")
            return result.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(
            "Không đọc được ảnh. Vui lòng dùng ảnh JPG, PNG hoặc WebP hợp lệ."
        ) from exc


class VnptClient:
    def __init__(self, credentials: Credentials, *, transport=None):
        self.client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "Token-id": credentials.token_id,
                "Token-key": credentials.token_key,
                "Authorization": f"Bearer {credentials.access_token}",
                "mac-address": "EKYCLAB",
            },
            timeout=httpx.Timeout(45, connect=10),
            follow_redirects=False,
            transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, path: str, **kwargs) -> dict:
        try:
            response = await self.client.post(path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "TIMEOUT",
                "VNPT phản hồi quá lâu. Chưa xác định được kết quả; thử lại có thể dùng thêm request.",
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(
                "CONNECTION", "Không kết nối được đến VNPT. Vui lòng thử lại sau."
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderError(
                "AUTH", "VNPT từ chối quyền truy cập. Kiểm tra bộ Token và Access token còn hạn."
            )
        if response.status_code == 429:
            raise ProviderError(
                "RATE_LIMIT",
                "VNPT đang giới hạn request hoặc hạn mức. Kiểm tra gói cước trước khi thử lại.",
            )
        if response.status_code == 400:
            try:
                payload = response.json()
                code = payload.get("message") if isinstance(payload, dict) else None
            except ValueError:
                code = None
            known = {
                "IDG-00010445": "VNPT không nhận diện được loại giấy tờ. Chụp rõ toàn bộ CCCD/CMND và đúng mặt giấy tờ.",
                "IDG-00010003": "VNPT không tìm thấy khuôn mặt. Chụp lại ảnh chân dung rõ mặt, đủ sáng và không che mặt.",
            }
            if isinstance(code, str) and code in known:
                raise ProviderError(code, f"{known[code]} (mã {code})")
            suffix = (
                f", mã {code}"
                if isinstance(code, str) and re.fullmatch(r"IDG-[0-9]{8}", code)
                else ""
            )
            raise ProviderError(
                "INVALID_INPUT",
                f"VNPT từ chối dữ liệu gửi lên (HTTP 400{suffix}). Kiểm tra ảnh và định dạng yêu cầu.",
            )
        if not 200 <= response.status_code < 300:
            raise ProviderError(
                "UPSTREAM_HTTP",
                f"VNPT trả lỗi HTTP {response.status_code}. Kiểm tra quyền API và hạn mức trong console.",
            )
        if len(response.content) > 2_000_000:
            raise ProviderError("INVALID_RESPONSE", "Kết quả VNPT vượt kích thước cho phép.")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            obj = payload.get("object")
            if isinstance(obj, str):
                obj = json.loads(obj)
            if payload.get("message") != SUCCESS or not isinstance(obj, dict):
                code = payload.get("message", "UNKNOWN")
                code = (
                    code
                    if isinstance(code, str) and re.fullmatch(r"IDG-[0-9]{8}", code)
                    else "UNKNOWN"
                )
                raise ProviderError(
                    code,
                    f"VNPT chưa xử lý được bước này (mã {code}). Kiểm tra ảnh, quyền API và hạn mức.",
                )
            return obj
        except (ValueError, TypeError) as exc:
            raise ProviderError("INVALID_RESPONSE", "Không đọc được kết quả từ VNPT.") from exc

    async def upload(self, image: bytes, title: str) -> str:
        obj = await self.request(
            "/file-service/v1/addFile",
            files={"file": (f"{title}.jpg", image, "image/jpeg")},
            data={"title": title, "description": "Internal eKYC integration test"},
        )
        value = obj.get("hash")
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ProviderError("INVALID_HASH", "VNPT không trả về mã ảnh hợp lệ.")
        return value

    async def check(self, path: str, payload: dict) -> dict:
        return await self.request(path, json=payload)


def check_result(key: str, obj: dict) -> tuple[str, str]:
    """Missing/unknown outputs stay 'review'; transport success is never a biometric pass."""
    if key.startswith("ocr_"):
        # Back OCR uses msg_back; older response variants use msg instead.
        # Prefer the side-specific field so a conflicting generic OK cannot mask an error.
        status_key = "msg_back" if key == "ocr_back" and "msg_back" in obj else "msg"
        if obj.get(status_key) != "OK":
            return "review", "VNPT chưa đọc đầy đủ thông tin. Kiểm tra ảnh và kết quả chi tiết."
        if key == "ocr_front" and not all(
            isinstance(obj.get(k), str) and obj[k] not in {"", "-"} for k in ("id", "name")
        ):
            return "review", "Thiếu họ tên hoặc số giấy tờ trong kết quả."
        if key == "ocr_back" and not any(
            isinstance(obj.get(k), str) and obj[k].strip() not in {"", "-"}
            for k in ("issue_date", "issue_place", "recent_location", "origin_location")
        ):
            return "review", "VNPT chưa trả thông tin đọc được từ mặt sau."
        tampering = obj.get("tampering", {})
        if not isinstance(tampering, dict):
            return "review", "Cần kiểm tra lại cảnh báo giấy tờ."
        if obj.get("id_fake_warning") == "yes" or tampering.get("is_legal") == "no":
            return "fail", "VNPT trả cảnh báo về thông tin giấy tờ."
        if obj.get("expire_warning") == "yes" or obj.get("back_expire_warning") == "yes":
            return "fail", "VNPT cảnh báo giấy tờ hết hạn."
        warnings = obj.get("warning") or obj.get("warning_msg") or tampering.get("warning")
        if warnings:
            return "review", "Đã đọc thông tin nhưng có cảnh báo cần xem lại."
        return "pass", "Đã đọc thông tin giấy tờ."
    if key in {"card_front", "card_back", "liveness"}:
        flags = [obj.get(k) for k in ("face_swapping", "fake_liveness", "fake_print_photo")]
        if any(v is True or (isinstance(v, str) and v.lower() == "true") for v in flags):
            return "fail", "VNPT phát hiện dấu hiệu giả mạo."
        if obj.get("liveness") == "failure":
            return "fail", "Chưa đạt kiểm tra liveness của VNPT."
        if obj.get("liveness") == "success":
            return "pass", "Đạt kiểm tra liveness trên ảnh của VNPT."
    if key == "mask":
        if obj.get("masked") == "yes":
            return "fail", "Khuôn mặt bị che. Vui lòng chụp lại."
        if obj.get("masked") == "no":
            return "pass", "Khuôn mặt không bị che."
    if key == "compare":
        if obj.get("msg") == "NOMATCH":
            return "fail", "VNPT trả kết quả khuôn mặt không khớp."
        if obj.get("msg") == "MATCH":
            try:
                score = float(obj.get("prob"))
                if math.isfinite(score) and 0 <= score <= 100:
                    return "pass", f"VNPT trả kết quả MATCH; điểm so khớp {score:.2f}."
            except (ValueError, TypeError):
                pass
    return "review", "Kết quả chưa đầy đủ hoặc chưa nhận diện được. Vui lòng xem dữ liệu VNPT."


FIELD_LABELS = {
    "id": "Số giấy tờ",
    "name": "Họ và tên",
    "birth_day": "Ngày sinh",
    "gender": "Giới tính",
    "nationality": "Quốc tịch",
    "card_type": "Loại giấy tờ",
    "origin_location": "Quê quán",
    "recent_location": "Nơi thường trú",
    "issue_date": "Ngày cấp",
    "issue_place": "Nơi cấp",
    "valid_date": "Có giá trị đến",
}


def extracted_fields(front: dict, back: dict) -> list[dict]:
    merged = dict(front)
    merged.update({k: v for k, v in back.items() if isinstance(v, str) and v not in {"", "-"}})
    return [
        {"label": label, "value": str(merged[key])[:1000]}
        for key, label in FIELD_LABELS.items()
        if merged.get(key)
    ]
