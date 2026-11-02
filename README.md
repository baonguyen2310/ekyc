# eKYC Lab

Service thử nghiệm VNPT eKYC độc lập, dùng FastAPI + giao diện HTML/JS + nginx; triển khai trên Civo tại **https://tools.opskoo.com**. Không dùng API, tài khoản hoặc cơ sở dữ liệu Opskoo.

## Luồng đang có

- Đăng nhập bằng mã truy cập riêng. Có thể dùng link `https://tools.opskoo.com/#access=...`; trang xóa fragment trước khi gửi mã đăng nhập. Không ghi mã vào query string, access log hoặc localStorage.
- Backend đọc **Token ID, Token Key, Access token** từ `/srv/ekyc-lab/.env`. Người thử không nhập, xem hoặc sửa bộ Token trên giao diện/API. Không trả khóa về trình duyệt.
- Chụp bằng camera hoặc tải ảnh JPG/PNG/WebP (tối đa 5 MB/ảnh).
- **Đọc giấy tờ (OCR):** mặc định chỉ upload hai mặt rồi gọi OCR từng mặt, tối đa 4 request gửi đến VNPT. Kết quả được giữ riêng với Oval.
- **Kiểm tra ảnh nâng cao:** tùy chọn trong phần OCR, giữ luồng API ảnh cũ gồm ba ảnh, OCR, card liveness hai mặt, face liveness, mask, compare; tối đa 10 request. Bỏ card liveness còn 8 request.
- Hiển thị tiến trình, kết quả từng kiểm tra, thông tin trích xuất, phản hồi JSON và tải kết quả về máy.
- **Liveness Oval:** bộ Web SDK chính thức 3.2.1.0 chạy `SDK_FLOW=FACE`, quét khuôn mặt gần–xa (`DOUBLE_LIVENESS=true`) và kiểm tra che mặt. Không cần CCCD hoặc làm OCR trước; không gọi API giấy tờ hoặc so khớp. Một lượt thông thường gồm 3 upload và 2 API kiểm tra, giới hạn 14 request. Có kết quả riêng và nút kết thúc để tắt camera. Không tự chuyển sang liveness ảnh khi Oval lỗi.
- **Luồng đầy đủ:** tab riêng mở `SDK_FLOW=DOCUMENT_TO_FACE`: chụp hai mặt giấy tờ, OCR và kiểm tra giấy tờ, quét Oval, kiểm tra che mặt và so khớp ảnh khuôn mặt từ chính lượt quét với ảnh trên giấy tờ. Không cần upload ảnh chân dung riêng. Giới hạn 14 request/lượt; mỗi bước có kết quả và nhật ký riêng. Các bước chưa chạy không được đánh dấu đạt.
- Mỗi tab có kết quả độc lập và nút tải JSON. Backend giữ kết quả SDK mới nhất theo từng luồng trong cùng phiên; quay lại Lab sẽ chọn đúng tab đã chạy, kể cả khi luồng lỗi hoặc bị kết thúc sớm.
- SDK mở ở màn hình camera riêng cùng origin để có đủ diện tích và tách CSS; kết thúc sẽ trở về kết quả trong Lab. Các API SDK đi qua gateway có session/CSRF, endpoint cố định và chỉ nhận mã tệp được upload trong chính lượt đó. Cấu hình SDK không chứa khóa VNPT; backend thêm khóa từ `.env` khi gọi nhà cung cấp. Kết quả lấy từ phản hồi upstream, không nhận trạng thái đạt do callback phía client tự khai báo.
- Không đọc NFC, không xác thực C06/VNeID, không ghi vào kho Face ID. Không bật ghi/lưu video MP4; SDK vẫn tạo dữ liệu quét cần cho liveness oval.
- Khi API lỗi, SDK được dừng và thông báo bằng dialog nằm trên lớp loading của vendor. Các request sau bị chặn, không tự retry. Kết quả có nhật ký từng request, kể cả lần thất bại: HTTP từ VNPT, HTTP của Lab, loại lỗi và thời gian. Gateway có hạn tổng 25 giây, ngắn hơn hạn nginx 30 giây; phân biệt timeout, mất kết nối, JSON sai và thiếu mã upload. Không lưu body lỗi upstream hoặc ảnh/quét vào log.

Các endpoint và payload được đối chiếu với [tài liệu API](https://ekyc.vnpt.vn/admin-dashboard/vi/documents/api), [hướng dẫn Web SDK](https://ekyc.vnpt.vn/admin-dashboard/vi/documents/sdk-web) và bộ `web-sdk-version-3.2.1.0.zip` tải từ [VNPT](https://ekyc.vnpt.vn/admin-dashboard/vi/documents/download) ngày 02/11/2026. Bộ 3.2.1.0 dùng `SDK.launch` và `DOUBLE_LIVENESS`, khác ví dụ `ekycsdk.init`/`ADVANCE_LIVENESS_FACE` của bản 2.1.0 trong tài liệu web. Vendor assets được giữ nguyên tại `app/static/vendor/vnpt-3.2.1.0`; trang mẫu có token demo không được đưa vào service. `provenance.json` lưu nguồn và SHA-256 archive. Chưa kiểm thử sinh trắc học bằng giấy tờ và khuôn mặt thật trên luồng SDK.

## Chạy local

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
# Thay LAB_ACCESS_CODE bằng giá trị ngẫu nhiên; sửa origin khi chạy local.
```

Uvicorn đọc các biến môi trường cần thiết (không tự đọc `.env` trong chế độ chạy trực tiếp):

```sh
LAB_ACCESS_CODE='a-random-local-test-access-code-at-least-32-characters' \
PUBLIC_ORIGIN=http://127.0.0.1:8103 COOKIE_SECURE=false \
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8103
```

```sh
.venv/bin/pytest -q
.venv/bin/ruff check app tests
node --check app/static/app.js
node --test tests/sdk-client.test.cjs
```

## Triển khai trên Civo

Source: `/srv/ekyc-lab`. Tạo `.env` từ `.env.example`, quyền 600. Cấu hình `VNPT_TOKEN_ID`, `VNPT_TOKEN_KEY`, `VNPT_ACCESS_TOKEN` ở đây; Access token có thể kèm tiền tố `bearer`. Không commit `.env` hoặc bộ Token. `Public key CA` không cần cho luồng REST hiện tại; HTTPS được kiểm tra chứng thư theo mặc định của HTTP client.

```sh
cd /srv/ekyc-lab
sudo docker compose up -d --build
sudo docker compose ps
curl -fsS http://127.0.0.1:8102/healthz
```

VPS đã dùng Caddy ở 80/443. Giữ Caddy phụ trách HTTPS, thêm host `tools.opskoo.com` chuyển tới nginx ở loopback `127.0.0.1:8102`. nginx chuyển nội bộ tới FastAPI. Xem `deploy/caddy.snippet`, `deploy/nginx.conf`, `compose.yaml`.

Trước khi áp dụng cấu hình Caddy phải sao lưu, chạy `caddy validate`, rồi reload. Không đổi các host bao/tinh/production. Cần giữ **một worker API** vì phiên và kết quả đang ở RAM; không nhân bản worker nếu chưa chuyển state sang kho chung.

Khi Access token hết hạn, người quản trị cập nhật `.env` rồi chạy `sudo docker compose up -d --force-recreate api`. nginx phân giải DNS nội bộ Docker định kỳ để nhận container API mới. Không gọi API cấp Token chưa được VNPT cung cấp, không tự đăng nhập hoặc tự dùng session web thay cho API chính thức. Backend đọc trường `exp` của JWT như gợi ý hết hạn để chặn trước request; đây không phải kiểm tra chữ ký JWT hoặc xác minh danh tính.

## Dữ liệu và giới hạn

- Người thử cần đồng ý gửi các ảnh đã chọn đến VNPT trước khi chạy. Dịch vụ VNPT có chính sách lưu dữ liệu riêng.
- Ảnh được decode, giới hạn kích thước, bỏ EXIF rồi gửi; ảnh tạm của parser nằm trong tmpfs. Không lưu hồ sơ/ảnh lâu dài trên VPS.
- Riêng Web SDK, gateway chuyển nguyên tệp canvas và blob quét của SDK để không làm hỏng dữ liệu quét. Mỗi tệp tối đa 5 MB, chỉ giữ trong thời gian chuyển request; kết quả không lưu base64 ảnh hoặc blob `scan3d`.
- Kết quả và dữ liệu JSON giữ trong RAM tối đa 20 phút (dọn mỗi 30 giây); phiên thử tối đa 8 giờ. Bộ Token nằm ở cấu hình backend, độc lập với phiên trình duyệt. Thoát phiên xóa kết quả và dừng task đang chạy, không xóa cấu hình backend; không thu hồi được request đã gửi hoặc xóa bản đã nằm ở VNPT.
- Không ghi request body, kết quả hoặc Token vào log. API yêu cầu session cookie HttpOnly/Secure/SameSite và Origin/CSRF hợp lệ; kết quả chỉ xem được trong chính phiên tạo nó.
- Mỗi phiên chạy tối đa một job, toàn service hai job cùng lúc; tối đa 20 job/giờ. Tắt retry tự động để tránh lặp request tính phí/hạn mức.
- Đếm số request đã gửi, không khẳng định đây là số credit VNPT trừ. Kết nối lỗi/timeout có thể vẫn đã dùng quota ở phía VNPT.
- HTTP thành công không phải “xác minh thành công”. Payload thiếu/không rõ đánh dấu cần xem lại; NOMATCH/liveness failure không chuyển thành pass.
- Bộ test tự động dùng transport giả và ảnh trắng tổng hợp, không tiêu hao hạn mức VNPT. Riêng chẩn đoán thủ công ngày 02/11/2026 đã gửi 3 request thật với ảnh trắng tổng hợp: upload 200; OCR 400/IDG-00010445 (không nhận diện loại giấy tờ); liveness 400/IDG-00010003 (không tìm thấy khuôn mặt). Chưa thử danh tính/ảnh cá nhân thực tế. `deploy/probe-vnpt.py` là script chạy thủ công có tiêu hao request, không chạy lúc khởi động.

## Dừng và quay lui

```sh
cd /srv/ekyc-lab
sudo docker compose down
```

Gỡ riêng hai block `tools.opskoo.com` trong Caddy hoặc phục hồi bản cấu hình sao lưu, validate và reload. Service không tạo database hoặc migration trong Opskoo.
