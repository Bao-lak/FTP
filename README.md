# BÁO CÁO VÀ HƯỚNG DẪN DỰ ÁN: MÁY CHỦ VÀ MÁY KHÁCH FTP (RFC 959)
**Môn học:** Mạng Máy Tính (Computer Networks) - Lab 2  
**Môi trường:** Ubuntu 22.04 LTS trở lên | Python 3.9+  
**Giao diện đồ họa:** Tkinter & ttk (Phong cách FileZilla)

---

## 1. TỔNG QUAN HỆ THỐNG

Dự án cài đặt trọn vẹn giao thức truyền tệp tin **FTP (File Transfer Protocol - RFC 959)** từ tầng Socket mạng của Python mà **KHÔNG sử dụng** các thư viện dựng sẵn như `pyftpdlib` hay `ftplib` phía Server. Hệ thống gồm 2 thành phần chính:

1. **FTP Server (Đa luồng - Multi-threaded):**
   - Kênh điều khiển (Control Connection) lắng nghe cổng `2121` (cấu hình được). Mỗi client kết nối được phân bổ một luồng (`threading.Thread`) riêng biệt.
   - Hỗ trợ đầy đủ cả hai chế độ truyền dữ liệu: **Passive Mode (PASV)** và **Active Mode (PORT)** trong dải cổng dữ liệu ngẫu nhiên (50000 - 50100).
   - Danh sách thư mục định dạng chuẩn Unix `ls -l` tương thích 100% với FileZilla, lệnh `ftp` trên Linux và curl.
   - Truyền tải file nhị phân (Binary) theo từng khối buffer **8 KB**, hỗ trợ tệp lớn, tiếp tục tải dở dang (**REST - Resume**), điều tiết tốc độ (Speed Throttling) và hạn mức dung lượng (Quota).
   - **Bảo mật đường dẫn:** Cô lập mỗi người dùng trong thư mục gốc (Home Dir / Chroot Jail), ngăn chặn triệt để tấn công Path Traversal (`../`).
   - **Cơ sở dữ liệu SQLite (`ftp_server.db`):** Quản lý người dùng, mật khẩu băm bảo mật (PBKDF2-HMAC-SHA256 kèm Salt), phân quyền (Đọc / Ghi / Xóa), ghi nhật ký phiên đăng nhập (Sessions), nhật ký lệnh (Command Logs) và lịch sử truyền file (Transfers).
   - **Giao diện quản trị Tkinter (ServerUI):** Khởi động/dừng server, quản lý User (CRUD), theo dõi client online (có nút ngắt kết nối cưỡng bức), xem log thời gian thực và biểu đồ thống kê lưu lượng vẽ bằng Canvas.

2. **FTP Client (Phong cách FileZilla):**
   - Lập trình thuần Socket kết nối trực tiếp đến Server theo chuẩn RFC 959.
   - Thanh kết nối nhanh (Host, Port, User, Pass, Mode, TLS).
   - Khung chia đôi (Dual-pane): Cục bộ (Local Site) và Máy chủ (Remote Site) dùng `ttk.Treeview`.
   - Các thao tác: Duyệt thư mục, tải lên (Upload), tải về (Download), tạo thư mục, đổi tên, xóa, làm mới.
   - Hàng đợi truyền file kèm thanh tiến trình (`ttk.Progressbar`) và tốc độ truyền thời gian thực chạy trên luồng nền không gây đơ giao diện.

---

## 2. CẤU TRÚC DỰ ÁN

```text
/home/bao/FTP/
├── ftp_server/
│   ├── __init__.py
│   ├── database.py         # Quản trị SQLite, băm mật khẩu, ghi log, an toàn đa luồng
│   ├── data_connection.py  # Quản lý kết nối dữ liệu Active (PORT) & Passive (PASV)
│   ├── session.py          # Quản lý phiên làm việc, chống Path Traversal, Quota & Throttle
│   ├── command_handler.py  # Bộ phân tích và thực thi lệnh FTP chuẩn RFC 959
│   ├── server.py           # Socket Server đa luồng, quản lý vòng đời client & FTPS
│   └── server_ui.py        # Giao diện Tkinter quản trị Server 5 Tab hiện đại
├── ftp_client/
│   ├── __init__.py
│   ├── client.py           # FTP Client thuần Socket RFC 959 (PASV, PORT, RETR, STOR)
│   └── client_ui.py        # Giao diện Client Tkinter 2 khung duyệt file như FileZilla
├── ftp_root/               # Thư mục chứa dữ liệu của Server
│   ├── admin/              # Thư mục gốc của tài khoản admin
│   ├── anonymous/          # Thư mục công khai của anonymous (chỉ đọc)
│   └── student/            # Thư mục của tài khoản student (đọc/ghi, cấm xóa)
├── init_db.py              # Script tạo bảng SQLite và dữ liệu/tài khoản ban đầu
├── main_server.py          # Điểm chạy FTP Server (Hỗ trợ cả GUI và CLI --headless)
├── main_client.py          # Điểm chạy FTP Client (Hỗ trợ cả GUI và CLI --cli)
├── test_ftp_suite.py       # Bộ 12 kịch bản kiểm thử tự động toàn diện
├── requirements.txt        # Tệp danh mục thư viện
└── README.md               # Tài liệu hướng dẫn & bộ câu hỏi vấn đáp
```

---

## 3. HƯỚNG DẪN CÀI ĐẶT VÀ KHỞI CHẠY TRÊN UBUNTU (22.04+)

Mở Terminal trên Ubuntu và thực hiện tuần tự các bước sau:

### Bước 1: Cập nhật hệ điều hành và cài đặt các gói cần thiết
```bash
sudo apt update
sudo apt install -y python3 python3-tk python3-venv python3-pip openssl ftp filezilla
```

### Bước 2: Thiết lập môi trường ảo (Virtualenv)
```bash
cd /home/bao/FTP
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Bước 3: Khởi tạo Database và dữ liệu mẫu
```bash
python3 init_db.py
```
*Lệnh này sẽ tự động sinh file `ftp_server.db` và tạo sẵn 3 tài khoản mẫu:*
- **admin** | Mật khẩu: `123456` | Toàn quyền (Đọc, Ghi, Xóa).
- **student** | Mật khẩu: `student123` | Quyền Đọc, Ghi (Không có quyền Xóa).
- **anonymous** | Không cần mật khẩu | Quyền Chỉ Đọc.

### Bước 4: Khởi chạy Máy chủ (FTP Server)
```bash
# Chạy giao diện đồ họa quản trị:
python3 main_server.py

# Hoặc nếu chạy trên Ubuntu Server không có giao diện màn hình (Headless):
python3 main_server.py --headless --port 2121
```

### Bước 5: Khởi chạy Máy khách (FTP Client)
Mở một cửa sổ Terminal mới:
```bash
source venv/bin/activate
python3 main_client.py
```

---

## 4. HƯỚNG DẪN KIỂM THỬ ĐA DẠNG ĐỂ CHỨNG MINH ĐÚNG CHUẨN RFC 959

### 4.1. Chạy bộ kiểm thử tự động (Automated Test Suite)
Hệ thống tích hợp sẵn script kiểm thử 12 chức năng cốt lõi:
```bash
python3 test_ftp_suite.py
```
Kết quả kiểm tra xác nhận: Xác thực tài khoản, PWD/CWD/CDUP, Passive Mode, Active Mode, Upload STOR, Đổi tên RNFR/RNTO, Download RETR, Tiếp tục tải dở dang REST, Xóa tệp/thư mục, Phân quyền RBAC, Chặn Path Traversal, và Ghi nhận SQLite.

### 4.2. Kiểm thử bằng lệnh `ftp` có sẵn của Ubuntu
Mở Terminal và gõ:
```bash
ftp localhost 2121
```
Thực hiện các thao tác trong phiên FTP:
```text
Name (localhost:bao): admin
Password: 123456
ftp> ls
ftp> pwd
ftp> cd tailieu
ftp> pwd
ftp> cdup
ftp> get welcome.txt
ftp> put welcome.txt uploaded_by_cli.txt
ftp> quit
```
*Server sẽ phản hồi chính xác các mã `220`, `331`, `230`, `227`, `150`, `226`, `257`, `250`, `221`.*

### 4.3. Kiểm thử bằng ứng dụng FileZilla
1. Mở FileZilla trên Ubuntu (`filezilla &`).
2. Nhập thông số tại thanh Kết nối nhanh:
   - **Host:** `127.0.0.1` (hoặc `localhost`)
   - **Username:** `admin`
   - **Password:** `123456`
   - **Port:** `2121`
3. Nhấn **Quickconnect**. FileZilla sẽ tải cây thư mục từ xa chuẩn xác! Bạn có thể kéo thả tệp giữa hai khung máy tính và server.

### 4.4. Kiểm thử giữa 2 máy tính khác nhau trong cùng mạng LAN
Khi chạy Server trên Máy A (ví dụ IP: `192.168.1.50`) và muốn Máy B trong mạng LAN truy cập:
1. Mở cổng trên tường lửa của Ubuntu (Máy A):
   ```bash
   sudo ufw allow 2121/tcp
   sudo ufw allow 50000:50100/tcp
   sudo ufw status
   ```
2. Trên Máy A, mở `main_server.py`, tại ô "Địa chỉ IP lắng nghe" nhập `0.0.0.0` và nhấn **Khởi động Server**.
3. Trên Máy B, mở FTP Client hoặc FileZilla, điền Host là `192.168.1.50`, Port `2121`, User `admin`, Pass `123456` để kết nối và truyền nhận file.

---

## 5. BỘ CÂU HỎI VẤN ĐÁP THI LAB MẠNG MÁY TÍNH (KÈM ĐÁP ÁN NGẮN GỌN)

### Câu 1: Sự khác biệt cơ bản giữa Active Mode (PORT) và Passive Mode (PASV)?
- **Active Mode (PORT):**
  + Client mở một cổng ngẫu nhiên và gửi lệnh `PORT h1,h2,h3,h4,p1,p2` để thông báo cho Server biết.
  + **Server là bên chủ động gọi `connect()`** tới cổng đó của Client để mở kênh dữ liệu.
  + *Nhược điểm:* Dễ bị tường lửa (Firewall) hoặc NAT phía Client chặn kết nối đến.
- **Passive Mode (PASV):**
  + Client gửi lệnh `PASV`. Server mở một cổng lắng nghe trong dải định sẵn và trả về `227 Entering Passive Mode (h1,h2,h3,h4,p1,p2)`.
  + **Client là bên chủ động gọi `connect()`** tới cổng dữ liệu của Server.
  + *Ưu điểm:* Là chuẩn mặc định ngày nay vì vượt qua được tường lửa/NAT của mạng gia đình và doanh nghiệp.

### Câu 2: Vì sao giao thức FTP sử dụng 2 kết nối riêng biệt (Control Connection và Data Connection)?
- **Kênh điều khiển (Control Connection - Cổng 2121):** Duy trì liên tục suốt phiên làm việc, chỉ dùng để gửi các chuỗi lệnh văn bản ASCII ngắn (USER, PASS, CWD, PWD) và nhận mã trạng thái.
- **Kênh dữ liệu (Data Connection):** Được tạo mới độc lập cho mỗi lần truyền file hoặc gửi danh sách thư mục (LIST, RETR, STOR) và đóng ngay sau khi truyền xong (báo hiệu EOF).
- **Lý do tách biệt (Out-of-band control):** Giúp Client có thể gửi lệnh điều khiển (như `ABOR` - hủy tải) ngay cả khi đường truyền dữ liệu đang bận truyền một tệp tin lớn nhiều Gigabyte mà không làm tắc nghẽn luồng truyền tải.

### Câu 3: Ý nghĩa các mã trạng thái phản hồi FTP phổ biến?
- `150`: File status okay, chuẩn bị mở kết nối dữ liệu (Opening data connection).
- `200`: Lệnh thành công (Command okay, ví dụ: TYPE, NOOP, PORT).
- `220`: Máy chủ đã sẵn sàng phục vụ (Service ready for new user).
- `221`: Đóng kết nối điều khiển, tạm biệt (Closing control connection).
- `226`: Truyền dữ liệu hoàn tất thành công, đóng socket dữ liệu (Closing data connection).
- `227`: Vào chế độ Passive Mode thành công, trả về IP và Port (Entering Passive Mode).
- `230`: Đăng nhập thành công (User logged in, proceed).
- `250`: Thao tác tệp/thư mục thành công (Requested file action okay).
- `257`: Tạo thành công đường dẫn hoặc in thư mục hiện tại (Pathname created / PWD).
- `331`: Nhận username hợp lệ, yêu cầu gửi tiếp mật khẩu (User name okay, need password).
- `350`: Yêu cầu thông tin bổ sung để hoàn tất (Pending further information: REST, RNFR).
- `425`: Không thể mở kết nối dữ liệu (Can't open data connection).
- `530`: Chưa đăng nhập hoặc sai thông tin xác thực (Not logged in / Login incorrect).
- `550`: Lệnh không được thực hiện do file không tồn tại hoặc không đủ quyền hạn.
- `552`: Vượt quá hạn mức dung lượng lưu trữ (Exceeded storage allocation / Quota).

### Câu 4: Phân biệt chế độ truyền TYPE A và TYPE I?
- **TYPE A (ASCII Mode):** Dùng để truyền văn bản thuần. Hệ thống sẽ tự động chuyển đổi ký tự xuống dòng giữa các hệ điều hành khác nhau (ví dụ: `\r\n` của Windows sang `\n` của Linux/Unix).
- **TYPE I (Image / Binary Mode):** Truyền dữ liệu nguyên vẹn từng byte nhị phân theo khối (raw bytes). Đây là chế độ bắt buộc khi truyền file thực thi, ảnh, video, nhạc, file nén (.zip, .pdf) để không bị hỏng cấu trúc tệp.

### Câu 5: Vì sao giao thức FTP bắt buộc sử dụng giao thức truyền tải TCP thay vì UDP?
- FTP truyền tải các tệp tin quan trọng đòi hỏi **tính toàn vẹn dữ liệu 100%**.
- **TCP (Transmission Control Protocol)** cung cấp kết nối tin cậy (Reliable), kiểm soát luồng (Flow control), kiểm soát tắc nghẽn (Congestion control), đảm bảo các gói tin đến nơi đúng thứ tự và tự động truyền lại gói tin bị mất mát (Retransmission). Nếu dùng UDP, các gói tin có thể bị mất, trùng lặp hoặc sai thứ tự dẫn tới tệp tin sau khi tải về bị hỏng (corrupted).

### Câu 6: Trình bày cơ chế chống tấn công Directory Traversal (Path Traversal / `../`) trong mã nguồn?
- Trong lớp `FTPSession.resolve_path()`:
  1. **Bước 1 - Chuẩn hóa đường dẫn ảo (Virtual Path):** Dùng thuật toán chuẩn hóa ngăn chặn việc lùi vượt quá thư mục gốc ảo `/`. Bất kỳ yêu cầu lùi quá cấp độ `/` đều bị giữ lại ở `/`.
  2. **Bước 2 - Ánh xạ đĩa cứng & Kiểm tra Canonical Path:** Ghép đường dẫn ảo vào `home_dir` thực tế trên đĩa cứng và phân giải toàn bộ liên kết mềm (symlink) bằng `os.path.realpath()`.
  3. **Bước 3 - Kiểm tra tiền tố an toàn (Jail check):** 
     ```python
     if not (real_path == real_home or real_path.startswith(real_home + os.sep)):
         raise PermissionError("Truy cập bị từ chối: Phát hiện nỗ lực thoát khỏi thư mục gốc!")
     ```
     Nếu đường dẫn kết quả nằm ngoài thư mục `home_dir`, chương trình sẽ chặn đứng ngay lập tức và ghi log cảnh báo an ninh.

---

## 6. SƠ ĐỒ LUỒNG HOẠT ĐỘNG: TỪ KẾT NỐI ĐẾN KHI TẢI XONG MỘT FILE

Dưới đây là trình tự trao đổi bản tin chuẩn giữa **Client** và **Server** khi tải một tệp (`RETR file.txt` ở chế độ Passive):

```text
CLIENT                                                          SERVER
  |                                                                |
  | -------- [1] TCP Syn (Cổng 2121) ----------------------------> |
  | <------- [2] TCP Syn-Ack (Kết nối Control được mở) ---------- |
  | <------- [3] 220 (MyFTPServer 1.0) San sang phuc vu ---------- |
  |                                                                |
  | -------- [4] USER admin\r\n ---------------------------------> |
  | <------- [5] 331 Yeu cau mat khau cho tai khoan admin. ------- |
  |                                                                |
  | -------- [6] PASS 123456\r\n --------------------------------> | (Tra cứu SQLite)
  | <------- [7] 230 Dang nhap thanh cong. Xin chao admin! ------- |
  |                                                                |
  | -------- [8] TYPE I\r\n (Chế độ nhị phân) -------------------> |
  | <------- [9] 200 Chuyen sang che do BINARY. ------------------ |
  |                                                                |
  | -------- [10] PASV\r\n --------------------------------------> | (Mở cổng 50025)
  | <------- [11] 227 Entering Passive Mode (127,0,0,1,195,105) - | (195*256+105=50025)
  |                                                                |
  | -------- [12] TCP Connect tới IP 127.0.0.1 cổng 50025 -------> | (Mở Data Socket)
  |                                                                |
  | -------- [13] RETR welcome.txt\r\n --------------------------> | (Kiểm tra quyền READ)
  | <------- [14] 150 Mo ket noi du lieu BINARY cho welcome.txt - |
  |                                                                |
  | <======= [15] DỮ LIỆU FILE (Khối buffer 8 KB qua cổng 50025) == |
  |                                                                |
  | <------- [16] Đóng kết nối Data Socket (Báo hiệu hết file) ---- |
  | <------- [17] 226 Truyen tep hoan tat thanh cong. ------------ | (Ghi log SQLite)
  |                                                                |
  | -------- [18] QUIT\r\n --------------------------------------> |
  | <------- [19] 221 Tam biet, hen gap lai! --------------------- |
  | -------- [20] Đóng kết nối Control Socket -------------------> |
```

