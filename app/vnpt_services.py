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
                "message": "Chưa kết nối IDCheck NFC. Chưa thể bắt đầu quét từ Lab.",
                "requirements": [
                    {
                        "label": "SDK/app đọc chip trên điện thoại hoặc đầu đọc thẻ",
                        "detail": "Chưa có bộ tích hợp NFC và cơ chế chuyển phiên cho Lab.",
                    },
                    {
                        "label": "API IDCheck và quyền thử nghiệm",
                        "detail": "Chưa xác nhận quyền IDCheck của tài khoản. Khóa OCR/Oval không xác nhận quyền này.",
                    },
                    {
                        "label": "Kết quả xác thực do backend nhận từ VNPT",
                        "detail": "Cần tài liệu API, cách xác thực và lấy kết quả chính thức trước khi triển khai.",
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
                        "detail": "Cần hợp đồng API và thông tin xác thực riêng. Đọc NFC hoặc OCR thành công không phải kết quả đối chiếu C06.",
                    },
                ],
            },
        }

    return router
