# VNPT NFC và IDCheck — ghi nhận ngày 05/11/2026

Đã đọc console sau khi người dùng đăng nhập. Chỉ xem dashboard/tài liệu và tải bộ tích hợp; không mở lượt quét, gọi API xác minh hoặc tiêu hao quota.

## Các dịch vụ tách riêng

| Mục trên console | Ghi nhận | Giới hạn của kết luận |
| --- | --- | --- |
| eKYC | 68 request khả dụng | Luồng OCR/Oval đang dùng; không suy ra quyền NFC hoặc IDCheck. |
| NFC | Trial, 100 request khả dụng | Có quota NFC trên console, chưa kiểm chứng request thực tế hoặc cách tính phí cho một lượt hoàn chỉnh. |
| IDCheck | 0 request khả dụng | Không coi 100 NFC là 100 lượt IDCheck/C06. Chưa có căn cứ xác nhận quota VNeID. |

Số liệu là ảnh chụp trạng thái ngày kiểm tra, không phải bộ đếm tự cập nhật từ nhà cung cấp. Mở tab Lab không truy vấn dashboard hoặc API nhà cung cấp.

## Bộ tích hợp đã tải được

Nguồn: [VNPT — Tải SDK](https://ekyc.vnpt.vn/admin-dashboard/vi/documents/download), mục SDK NFC, qua phiên đăng nhập của người dùng.

- iOS: `[VNPT][iOS] Hướng dẫn tích hợp SDK NFC v2.2.8.docx`, phiên bản 2.2.8, ngày 11/06/2026 theo bảng tải.
- Android: `[VNPT][Android] Hướng dẫn tích hợp SDK NFC v1.8.5.pdf`, phiên bản 1.8.5, ngày 04/05/2026.
- Android: `SDK_NFC_v1.8.5.zip`, gồm `vnpt_nfc_sdk-release-v1.8.5.aar` và `scanqr_ic_sdk-release-v1.0.6.aar`. Đây là thư viện để nhúng vào app Android; archive không chứa APK hoặc ứng dụng trung chuyển đã cài được.

Các tệp được tải về Downloads trên máy Mac. Không đưa binary SDK vào GitHub hoặc công khai qua Lab khi chưa đánh giá điều kiện phân phối. SHA-256 của các tệp nằm trong `vnpt-nfc-provenance.json` cùng thư mục tài liệu này.

## Điều đã xác nhận từ tài liệu

Tài liệu Android trang 18 mô tả `IS_ENABLE_VERIFY_CHIP`: xác thực chip với C06, mặc định `false`; để thực hiện cần quét hết thông tin trong chip. Trang 19 mô tả `IS_ENABLE_ADD_IDCHECK_DATA` và `IS_ENABLE_UPLOAD_DG` là các tùy chọn riêng, mặc định `false`. Trang 23 mô tả `VERIFY_CHIP_RESULT` là kết quả xác thực với C06.

Vì vậy đọc NFC và bước xác thực qua C06 được phân biệt trong cấu hình SDK. Việc có quota NFC không chứng minh có quota IDCheck/C06; quota cũng chưa chứng minh backend hoặc CSCA Việt Nam đã hoạt động đúng trong một lượt thực tế.

Tài liệu iOS mô tả tạo `ICMainNFCReaderViewController`, delegate nhận kết quả; có các trường `transactionId`, `transactionPartnerId`, `dataGroupsResult` và `dataNFCResult`, cùng cấu hình `baseUrl`, `urlUploadDataNFC`, `headersRequest`. Không có đặc tả web tạo phiên rồi chuyển qua một app sẵn có và backend tự lấy kết quả giống Signicat trong các tài liệu đã đọc.

[Hướng dẫn Web SDK](https://ekyc.vnpt.vn/admin-dashboard/vi/documents/sdk-web) đang liệt kê các luồng `DOCUMENT_TO_FACE`, `FACE_TO_DOCUMENT`, `FACE`, `DOCUMENT`, cấu hình OCR, kiểm tra khuôn mặt và quét QR. Phần Website tại trang tải chỉ có SDK eKYC; không thấy SDK NFC dành cho trình duyệt trong danh mục đó. Không suy ra VNPT hoàn toàn không có giải pháp web–app riêng, nhưng chưa có tài liệu để triển khai luồng ấy.

## Vướng mắc còn lại để chạy từ Lab

1. App trên điện thoại: app có nhúng SDK, hoặc ứng dụng/luồng chuyển phiên chính thức VNPT cung cấp. Web SDK OCR/Oval không thể thay phần này.
2. Kết nối backend: thông tin xác thực của dịch vụ NFC và tài liệu request/response cho dữ liệu chip, kiểm tra nguồn kết quả. Không nhập khóa dài hạn trong app/web hoặc nhận JSON client tự khai báo làm xác minh thành công.
3. Nếu bật IDCheck: quota/quyền IDCheck và đặc tả backend xác thực C06. Để tắt mặc định trong luồng NFC độc lập.
4. Nếu tích hợp VNeID: đăng ký/cấp quyền kết nối và tài liệu riêng về khởi tạo, consent, chuyển hướng, callback, lấy và kiểm chứng kết quả. Không coi IDCheck là đăng nhập VNeID.

Lab hiện chỉ hiển thị trạng thái chuẩn bị. Chưa có app NFC VNPT nối vào Lab, chưa gọi API NFC/IDCheck và chưa chạy E2E. Không tạo QR/deep link hoặc kết quả giả để che phần tích hợp chưa có.
