# eKYC Lab

Service thử nghiệm eKYC độc lập, dùng FastAPI + giao diện HTML/JS + nginx; triển khai trên Civo tại **https://tools.opskoo.com**. VNPT phục vụ OCR/Oval; tab NFC dùng Signicat ReadID Ready qua Assure API. Hai tab VNPT IDCheck và VNeID/C06 hiện mới là giao diện chuẩn bị tích hợp, chưa thực hiện xác minh. Không dùng API, tài khoản hoặc cơ sở dữ liệu Opskoo.

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
- Các luồng VNPT không đọc NFC, không xác thực C06/VNeID, không ghi vào kho Face ID. Không bật ghi/lưu video MP4; SDK vẫn tạo dữ liệu quét cần cho liveness oval. NFC là tab sandbox riêng, cần cấu hình tài khoản Signicat; xem bên dưới.
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
node --test tests/nfc-client.test.cjs
node --test tests/vnpt-services-client.test.cjs
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

## NFC · ReadID Ready (03/11/2026)

Tab `/?tab=nfc` tích hợp app **ReadID Ready của Signicat**, không dùng ReadID Me. Luồng được viết theo [Capture/Assure](https://developer.signicat.com/docs/id-document-and-biometric-verification/services/capture/) và [API reference](https://developer.signicat.com/apis/id-document-and-biometric-verification/):

1. Backend đổi Client ID/Secret lấy OAuth token tại host cố định `api.signicat.com`. Chỉ chấp nhận token nhận trực tiếp qua HTTPS có claim `sandbox=true`; từ chối production trước khi tạo dossier. Không dùng khóa mẫu trong tài liệu nhà cung cấp.
2. Tạo dossier với mã tham chiếu ngẫu nhiên của Lab, sau đó gọi Capture với `provider=readid`, `processType=ready`, `sdk=native` và domain sandbox đã cấu hình.
3. Mở URL Capture được trả về. Chính Signicat hiển thị QR/hướng dẫn chuyển sang ReadID Ready; Lab không tự đoán deep link hay dựng QR giả. URL phải là HTTPS, đúng domain sandbox, đúng đường dẫn Capture và không chứa userinfo/control characters.
4. Người thử làm theo hướng dẫn trong app. Khi quay về Lab, bấm **Lấy kết quả**. Backend chỉ đọc các process `readid/ready` trong dossier đã tạo cho chính phiên Lab. Không nhận `processId`, `dossierId`, trạng thái hoặc kết quả từ client/redirect để quyết định đạt.
5. Hiển thị riêng trạng thái nhà cung cấp, `chipVerification`, `chipCloneDetection`, các trường định danh, JSON đã lọc và nhật ký request. Thiếu/không rõ kết quả chip là **chưa có kết quả**, không tự đánh dấu đạt. Không có liveness/face match/C06/VNeID trong tab này.

**Chưa kiểm chứng end-to-end với ReadID Ready hoặc CCCD Việt Nam.** Sandbox đã được cấu hình ngày 03/11/2026: domain `opskoo-ekyc-lab.sandbox.signicat.com`, client `ekyc-lab-nfc` chỉ có quyền Assure API, khóa ở `.env` backend. OAuth thật trả 200 và token xác nhận sandbox; tạo hồ sơ rỗng trả 201, đọc danh sách hồ sơ trả 200. Hồ sơ rỗng đã được xóa sau kiểm tra. Chưa gọi Capture, chưa tạo lượt xác minh ReadID, chưa gửi ảnh hoặc đọc CCCD. Bộ unit test vẫn dùng `MockTransport` và dữ liệu giả. Các bước Ready chi tiết trong [hướng dẫn của Signicat](https://developer.signicat.com/docs/id-document-and-biometric-verification/provider-specific-integrations/signicat-readid/integration-guide-readid/) vẫn ghi là chưa được tài liệu hóa đầy đủ; API reference có `ready` và Capture, nhưng quyền ReadID Ready cụ thể còn cần kiểm chứng bằng lượt thử có chủ đích.

### Cấu hình backend

Trong [Signicat Dashboard](https://dashboard.signicat.com/), tạo sandbox account, domain sandbox, OAuth client; cấp **Assure API** và quyền **ReadID Ready**. Theo [quick start chính thức](https://developer.signicat.com/docs/id-document-and-biometric-verification/quick-start-guides/quick-start-guide-assure-api/), một số provider có thể trả dữ liệu mô phỏng trong sandbox. Sandbox mặc định nên dùng dữ liệu thử; nếu dùng giấy tờ thật, cần làm rõ sandbox riêng và xử lý dữ liệu với Signicat trước.

Điền vào `/srv/ekyc-lab/.env` (không nhập trên client, không commit):

```dotenv
SIGNICAT_NFC_ENABLED=true
SIGNICAT_CLIENT_ID=<sandbox-client-id>
SIGNICAT_CLIENT_SECRET=<sandbox-client-secret>
SIGNICAT_CAPTURE_DOMAIN=<ten-tai-khoan>.sandbox.signicat.com
```

Recreate API sau khi cập nhật. Thiếu cấu hình thì tab vẫn mở được nhưng nút tạo phiên bị vô hiệu hóa và API trả 503, không tạo kết quả giả. Không kích hoạt production hoặc giả định gói miễn phí bao gồm NFC. Bản hiện tại chỉ nhận domain `*.sandbox.signicat.com`; custom domain cần điều chỉnh có chủ đích sau khi được cấp và xác minh.

Không gọi nhà cung cấp khi khởi động, GET trạng thái hoặc mở tab. Tạo phiên và lấy kết quả là thao tác thủ công, không tự retry, không auto-poll. Mỗi phiên Lab có tối đa một lượt NFC chưa kết thúc, tối đa 20 lượt mới/giờ toàn Lab; tối đa 15 lần cập nhật/lượt, cách nhau ít nhất 5 giây. Request có deadline tổng 25 giây; thất bại/timeout không tự tạo lại dossier.

Kết quả/liên kết riêng chỉ nằm trong RAM và trong phiên Lab tạo nó, tối đa 20 phút. Thoát Lab xóa bản tại VPS; không xóa hoặc hủy dossier tại Signicat. Bản JSON hiển thị/tải về chỉ có các trường được phép, không có token/ảnh/chip blob. Số request hiển thị chỉ tính backend Lab, không gồm request app ReadID gửi và không khẳng định số lượt tính phí. Camera/NFC chạy trong app, không phải Web NFC trên trình duyệt.

## VNPT IDCheck và VNeID/C06 — phần chuẩn bị (05/11/2026)

- `/?tab=idcheck`: tab riêng mô tả luồng đọc chip NFC bằng SDK/app trên điện thoại hoặc thiết bị đọc thẻ, rồi nhận kết quả IDCheck tại backend Lab.
- `/?tab=vneid`: tab riêng phân biệt xác thực qua VNeID với dịch vụ xác thực căn cước RAR-C06. Không coi hai dịch vụ là cùng một API, hoặc lấy kết quả NFC/OCR thay cho xác nhận của dịch vụ này.
- **Cả hai chưa có luồng chạy thật.** Nút bắt đầu bị vô hiệu hóa và giao diện liệt kê phần tích hợp còn thiếu. Không tạo QR, deep link, dữ liệu xác minh hoặc trạng thái đạt giả. Không nhận kết quả xác minh do client tự gửi.
- `GET /api/vnpt-services/status` yêu cầu phiên Lab, chỉ trả tình trạng triển khai của Lab; không truy vấn quyền tài khoản hoặc gửi request tới VNPT/C06. Trạng thái `integration_pending` không có nghĩa tài khoản bị VNPT từ chối cấp quyền. Khóa OCR/Oval hiện có không được dùng làm bằng chứng về quyền IDCheck/VNeID.

Theo [VNPT IDCheck](https://vnptai.io/ekyc/vi/solution-idcheck), nhà cung cấp có SDK NFC cho iOS/Android, hỗ trợ thiết bị đọc thẻ và API xác thực với RAR-C06. [Trang gói cước](https://ekyc.vnpt.vn/vi/idcheck-12) còn nêu SDK Web nhưng không đủ thông tin để kết luận trình duyệt tự đọc được chip hoặc có sẵn luồng chuyển sang app giống ReadID Ready. Bộ Web SDK 3.2.1.0 đang dùng tại Lab là bộ OCR/Oval; không thêm giá trị `SDK_FLOW` chưa được tài liệu hóa cho NFC.

[Cổng tích hợp xác thực điện tử quốc gia](https://congxacthucdientuquocgia.gov.vn/vi) mô tả dịch vụ xác thực căn cước và dịch vụ xác thực điện tử VNeID cùng quy trình đăng ký khai thác. Chưa có hợp đồng API/SDK hoặc thông tin cấp quyền dịch vụ này cho Lab. Chưa xác nhận tài khoản VNPT hiện tại có quyền IDCheck; ngày kiểm tra, [trang tài liệu tải SDK](https://ekyc.vnpt.vn/admin-dashboard/vi/documents/download) yêu cầu đăng nhập lại.

Để hoàn tất cần: (1) tài liệu IDCheck và SDK/app/đầu đọc có thể nhận phiên từ Lab; (2) thông tin xác thực và quyền thử nghiệm IDCheck, môi trường và hạn mức; (3) đặc tả backend tạo phiên/lấy kết quả và kiểm chứng nguồn kết quả; (4) nếu thử VNeID, đặc tả chuyển hướng, consent, callback và thông tin kết nối được cấp cho dịch vụ đó. Chỉ triển khai adapter thực thi sau khi có hợp đồng kỹ thuật cụ thể, không tự đoán endpoint hoặc tái sử dụng phiên đăng nhập console làm API.

Thay đổi này chỉ chạy kiểm thử với dữ liệu tổng hợp và transport giả. Không khởi tạo xác minh, upload giấy tờ/khuôn mặt hoặc tiêu hao request VNPT/Signicat.
