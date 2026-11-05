"""Availability of separate VNPT services; never probe or consume provider quota.

OCR credentials do not establish IDCheck or VNeID entitlement. Execution must
stay unavailable until their actual API/SDK contracts and credentials exist.
"""

from fastapi import APIRouter


def vnpt_services_router():
    router = APIRouter(prefix="/api/vnpt-services")

    @router.get("/status")
    async def status():
        return {
            "idcheck": {
                "ready": False,
                "state": "integration_pending",
                "message": "Đã có SDK và quota NFC thử nghiệm; còn thiếu app kết nối với Lab.",
                "quota_snapshot": {
                    "checked_on": "05/11/2026",
                    "nfc": 100,
                    "idcheck": 0,
                    "ekyc": 68,
                },
                "requirements": [
                    {
                        "label": "SDK NFC chính thức đã tải được",
                        "detail": "iOS 2.2.8 và Android 1.8.5. Đây là thư viện nhúng vào app, không phải app đã cài được để nhận phiên của Lab.",
                    },
                    {
                        "label": "Còn thiếu cầu nối web → app → backend",
                        "detail": "Cần app chứa SDK hoặc luồng chuyển sang app do VNPT cung cấp. Chưa thấy luồng app có sẵn trong tài liệu đã đọc.",
                    },
                    {
                        "label": "NFC và IDCheck được tách riêng",
                        "detail": "Đọc chip NFC và xác thực với C06 là hai bước riêng; bước C06 mặc định tắt trong SDK. Quota NFC không xác nhận có quota cho bước IDCheck/C06.",
                    },
                    {
                        "label": "Kết nối backend NFC còn cần hoàn tất",
                        "detail": "Chưa cấu hình khóa NFC riêng hoặc kiểm chứng giao thức nhận kết quả tại Lab. Không dùng kết quả client tự khai báo để xác nhận thẻ thật.",
                    },
                ],
            },
            "vneid": {
                "ready": False,
                "state": "integration_pending",
                "message": "Chưa kết nối dịch vụ VNeID/C06. Chưa có lượt xác thực từ Lab.",
                "requirements": [
                    {
                        "label": "Dịch vụ và phạm vi dữ liệu được cấp",
                        "detail": "Chưa có cấu hình kết nối VNeID/C06 cho Lab, trực tiếp hoặc qua nhà cung cấp.",
                    },
                    {
                        "label": "Luồng VNeID và sự đồng ý chia sẻ",
                        "detail": "Cần tài liệu khởi tạo giao dịch, chuyển sang VNeID và nhận kết quả cho đơn vị tích hợp.",
                    },
                    {
                        "label": "API xác thực căn cước qua RAR-C06",
                        "detail": "Console IDCheck đang ghi nhận 0 request khả dụng ngày 05/11/2026. Hạn mức VNeID chưa được xác nhận; cần tài liệu và quyền kết nối riêng.",
                    },
                ],
            },
        }

    return router
