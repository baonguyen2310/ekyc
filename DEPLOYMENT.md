# Triển khai thử nghiệm — 02/11/2026

- VPS: SSH `civo-001` (`212.2.248.238`). Source: `/srv/ekyc-lab`.
- URL: https://tools.opskoo.com. Truy cập bằng mã riêng; không ghi mã trong tài liệu này.
- DNS `tools.opskoo.com` đã trỏ tới VPS. `dev.opskoo.com` cũng có DNS nhưng không sử dụng.
- HTTPS: Caddy đang có trên VPS → nginx tại `127.0.0.1:8102` → FastAPI nội bộ Docker.
- Containers: `ekyc-lab-api-1`, `ekyc-lab-nginx-1`, tự khởi động lại theo Docker Compose. API có healthcheck; chỉ chạy một worker.
- Caddyfile trước khi thêm domain được giữ ở `/etc/caddy/backups/Caddyfile.before-ekyc-lab-20261101T182937Z` (thời gian UTC). Chỉ thêm host tools và chuyển HTTP sang HTTPS.
- Không sửa 4 repo hoặc database của bao/tinh/production. Kiểm tra sau khi thêm route: cả `bao.opskoo.com/vi`, `tinh.opskoo.com/vi`, `opskoo.com/vi` trả HTTP 200.

## Đã kiểm tra

- 42 test tự động qua: luồng ảnh đầy đủ/OCR và gateway Web SDK; tách phiên và kết quả, TTL, CSRF, mã truy cập sai có Unicode, không lộ Token trong cấu hình/lỗi, xác thực file ảnh, chặn endpoint và mã tệp ngoài lượt SDK, giữ nguyên blob quét, giới hạn request, không coi phản hồi thiếu hoặc NOMATCH là pass và không tự retry.
- Ruff check/format và cú pháp JavaScript qua.
- Docker build thành công trên VPS; FastAPI healthcheck healthy; nginx phục vụ `/healthz` trả 200.
- Truy cập HTTPS ngoài VPS trả HTTP/2 200; HTTP chuyển 308 sang HTTPS.
- Kiểm tra HTTPS ở bản đầu: đăng nhập, CSRF và thoát phiên hoạt động. Bản cập nhật bỏ hoàn toàn route `/api/credentials`; cấu hình chỉ còn ở backend. Test mới kiểm tra không trả khóa về client, chặn trước khi Token hết hạn/thiếu cấu hình và giữ cấu hình khi đổi phiên người thử.
- Chrome ở bản đầu: mở được app bằng link riêng; chế độ OCR bỏ ảnh chân dung và hiện 4 request. Bản cập nhật đã xóa màn hình thiết lập Token khỏi client. Ảnh giao diện được cập nhật tại `evidence/ekyc-lab-desktop.jpg` ở bản local.
- Mức RAM quan sát khi idle: API khoảng 40 MB, nginx khoảng 2–3 MB; đây không phải đo tải.

## Cập nhật cấu hình backend và kiểm tra VNPT thật

Theo yêu cầu người dùng, bộ Token thử nghiệm đã lưu trong `/srv/ekyc-lab/.env`, quyền 600, không ghi giá trị vào tài liệu/source/log. Backend đọc bộ Token khi khởi động; client không có form hoặc API để xem/sửa khóa. Public key CA không cần cho luồng REST đang dùng.

Đã thử đúng **3 request thật** bằng ảnh trắng tổng hợp 800×600, không có giấy tờ hoặc khuôn mặt của người thật:

| API | HTTP | Kết quả |
| --- | --- | --- |
| Upload ảnh | 200 | `IDG-00000000`, nhận được mã ảnh. |
| OCR mặt trước | 400 | `IDG-00010445`, loại giấy tờ không hợp lệ. |
| Face liveness | 400 | `IDG-00010003`, không tìm thấy khuôn mặt. |

Các phản hồi cho thấy bộ Token dùng được và request đã tới bước xử lý ảnh của OCR/liveness; không gặp lỗi 401/403/429 ở ba request này. Chưa kiểm chứng ảnh hợp lệ cho kết quả đúng, hoặc quyền tất cả API khác trong gói thử. Không suy ra mọi tính năng đều được miễn phí.

Giao diện hiển thị thông báo cụ thể cho hai lỗi ảnh đã quan sát thay vì khiến người dùng hiểu nhầm là lỗi gói cước. Không tự động retry.

VNPT hiển thị Access token có hạn 8 giờ và yêu cầu liên hệ để có API sinh Access token. Đây là điểm cần làm rõ nếu muốn tự động hóa lâu dài. Khi Token hết hạn, người quản trị cập nhật `.env` và recreate container API. Không thêm thao tác này vào client. Riêng Web SDK có thể tải trực tiếp từ bảng Website tại trang tải SDK; ngày 02/11/2026 đã tải và kiểm tra `web-sdk-version-3.2.1.0.zip`, không phải liên hệ mới lấy được bộ tệp.

Lab có hai luồng: API ảnh và Camera · Web SDK Oval. Chưa có NFC hoặc C06/VNeID. Chưa dùng CCCD/selfie cá nhân để kiểm thử sinh trắc học của luồng SDK.

Kiểm tra HTTPS sau bản cập nhật: backend báo sẵn sàng; route sửa Token cũ trả 404; so sánh với bộ khóa thực tế xác nhận khóa không nằm trong HTML, JavaScript hoặc phản hồi trạng thái. Phiên chẩn đoán riêng đã đăng xuất; cấu hình backend vẫn giữ nguyên. Chrome đã xác nhận không còn trường nhập Token và người dùng vào được màn hình chọn ảnh.

## Sửa diễn giải OCR mặt sau — 02/11/2026

VNPT trả trạng thái mặt sau trong `msg_back`, còn service trước đó chỉ kiểm tra `msg`. Đã sửa để ưu tiên `msg_back` cho mặt sau và hỗ trợ `msg` ở biến thể cũ, vẫn giữ kiểm tra thiếu dữ liệu/cảnh báo/hết hạn. Kiểm tra trực tiếp JSON người dùng cung cấp tại máy local cho kết quả OCR mặt sau đạt; không sao chép thông tin định danh vào source hoặc test, không gửi lại ảnh/JSON cho VNPT. Fixture và test luồng đầy đủ dùng schema `msg_back` cùng dữ liệu giả để tránh lặp lỗi.

## Web SDK Oval — 02/11/2026

Đã build/restart service với bộ Web SDK chính thức 3.2.1.0. Giữ luồng API ảnh; bổ sung luồng chụp giấy tờ và oval gần–xa với `DOUBLE_LIVENESS=true`. Camera mở ở màn hình riêng `/sdk`, có nút kết thúc và trở về kết quả trong Lab. Không bật lưu MP4. SDK gọi gateway cùng origin; backend giữ khóa và chuyển request đến các endpoint cố định của VNPT, gồm `/ai/v1/web/face/liveness-3d`. Blob quét và các marker công khai của SDK được giữ đúng định dạng gốc. Kết quả của Lab lấy từ phản hồi backend nhận được; không tin dữ liệu đạt do client tự khai báo.

Đã kiểm tra khung oval, hoạt ảnh/video hướng dẫn và thao tác kết thúc lượt trên trình duyệt ở môi trường local. Kiểm tra HTTPS sau triển khai: health 200, backend VNPT sẵn sàng, cấu hình oval bật, SDK bundle tải 200, cấu hình SDK/HTML/bundle không chứa bộ khóa thực tế. Lượt chẩn đoán kết thúc với 0 request đến VNPT và các bước chưa chạy được đánh dấu cần xem lại; phiên chẩn đoán đã đăng xuất. Chưa thử giấy tờ và khuôn mặt thật hoặc khẳng định tài khoản đã được VNPT chấp nhận cho API liveness nâng cao.

Chrome trên bản HTTPS đã đi từ nút “Bắt đầu với camera” tới màn hình SDK “Chụp mặt trước”; nút “Kết thúc và xem kết quả” đưa về Lab và hiển thị đúng 0 request cùng các bước chưa có kết quả. Đã kiểm tra lại chế độ ảnh đầy đủ và OCR (ẩn chân dung, hiện 4 request). Ảnh màn hình triển khai: `evidence/ekyc-lab-web-sdk-live.jpg` tại bản local.

Đã giữ nguyên `.env` quyền 600 khi cập nhật source. Bản source trước SDK ở `/srv/ekyc-lab-backups/before-web-sdk-20261102.tar.gz` (không gồm `.env`); image trước đó được giữ bằng tag `ekyc-lab-api:before-web-sdk-20261102`. Không thay đổi host hay repo Opskoo.
