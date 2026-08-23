# VAM Portfolio Allocator — Web Edition

Phiên bản web của công cụ phân bổ danh mục đầu tư VAM, chạy bằng Streamlit.
Logic tính toán trong `vam_core.py` được giữ nguyên 100% từ bản desktop gốc.

## Chạy thử trên máy tính cá nhân

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Trình duyệt sẽ tự mở tại `http://localhost:8501`.

## Deploy miễn phí lên Streamlit Community Cloud

1. Tạo một repository mới trên GitHub (public hoặc private), ví dụ `vam-web`.
2. Đẩy 3 file này lên repo: `streamlit_app.py`, `vam_core.py`, `requirements.txt`.
   ```bash
   git init
   git add streamlit_app.py vam_core.py requirements.txt
   git commit -m "VAM web app"
   git branch -M main
   git remote add origin https://github.com/<username>/vam-web.git
   git push -u origin main
   ```
3. Vào **https://share.streamlit.io** → đăng nhập bằng GitHub → **New app**.
4. Chọn repo `vam-web`, branch `main`, file chính là `streamlit_app.py` → **Deploy**.
5. Sau khoảng 1-2 phút, bạn sẽ có URL dạng `https://<tên-app>.streamlit.app` — có thể gắn link này vào website cá nhân (nhúng bằng `<iframe>` hoặc để link trực tiếp).

## Thiết lập Google Sheets (lưu log vĩnh viễn)

Mặc định nếu không cấu hình gì, log chỉ tồn tại trong phiên làm việc (mất khi tải lại trang).
Làm theo các bước dưới đây để mọi lần tính toán được ghi thẳng lên một Google Sheet của bạn — xem lại được từ bất kỳ thiết bị nào, vĩnh viễn.

### Bước 1 — Tạo Google Sheet

1. Vào **https://sheets.google.com** → tạo một sheet mới, đặt tên bất kỳ (ví dụ "VAM Log").
2. Lấy **Sheet ID** từ URL — là đoạn chuỗi dài giữa `/d/` và `/edit`:
   `https://docs.google.com/spreadsheets/d/`**`1AbCdEfGhIjKlMnOpQrStUvWxYz...`**`/edit`

### Bước 2 — Tạo Service Account trên Google Cloud (miễn phí)

1. Vào **https://console.cloud.google.com** → tạo project mới (hoặc dùng project có sẵn).
2. Vào **APIs & Services → Library**, bật 2 API: **Google Sheets API** và **Google Drive API**.
3. Vào **APIs & Services → Credentials → Create Credentials → Service Account**. Đặt tên bất kỳ, bỏ qua các bước phân quyền (không cần).
4. Sau khi tạo xong, mở service account vừa tạo → tab **Keys → Add Key → Create new key → JSON**. File JSON sẽ tự động tải về máy — **giữ file này cẩn thận, không chia sẻ công khai**.
5. Mở file JSON đó, copy giá trị `client_email` (dạng `xxx@xxx.iam.gserviceaccount.com`).

### Bước 3 — Chia sẻ quyền chỉnh sửa Sheet cho Service Account

Mở lại Google Sheet ở Bước 1 → nút **Share** → dán email `client_email` vừa copy vào → chọn quyền **Editor** → Send.

### Bước 4 — Khai báo Secrets cho app

**Khi chạy thử trên máy cá nhân:** tạo file `.streamlit/secrets.toml` trong cùng thư mục project:

```toml
[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "xxx@xxx.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."

[sheets]
sheet_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz..."
worksheet_name = "log"
```

Copy toàn bộ các trường tương ứng trực tiếp từ file JSON đã tải ở Bước 2 (đừng tự gõ tay, dễ sai định dạng `private_key`).

**Khi deploy lên Streamlit Community Cloud:** vào app đã deploy → **⋮ (menu) → Settings → Secrets** → dán y hệt nội dung trên vào ô Secrets → Save. App sẽ tự khởi động lại.

`.streamlit/secrets.toml` chứa thông tin nhạy cảm — nếu dùng Git, thêm dòng `.streamlit/secrets.toml` vào file `.gitignore` để không lỡ đẩy lên GitHub public.

### Kiểm tra đã hoạt động

Sau khi cấu hình xong, mở app: nếu bên dưới bảng "Nhật ký lịch sử tính toán" hiện dòng chữ **"✅ Đang lưu vĩnh viễn lên Google Sheets"** là đã kết nối thành công. Nếu chưa cấu hình hoặc cấu hình sai, app vẫn chạy bình thường nhưng log chỉ lưu tạm trong phiên (không báo lỗi crash).

## Lưu ý

- Gói miễn phí Streamlit Community Cloud: app sẽ "ngủ" nếu không có ai truy cập trong 12 giờ, tự đánh thức khi có người vào lại (mất vài giây khởi động).
- Google Sheets API có hạn mức miễn phí rất cao (hàng trăm request/phút) — dùng cho một công cụ cá nhân sẽ không bao giờ chạm giới hạn.
- Nếu dùng tính năng "Tự động lấy dữ liệu AI" qua Gemini, mỗi người dùng tự nhập API Key riêng của họ — key chỉ tồn tại trong phiên trình duyệt, không được lưu trên máy chủ.
