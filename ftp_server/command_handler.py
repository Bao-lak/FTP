"""
Module Xử lý Lệnh FTP (FTP Command Handler).
Thực hiện phân tích cú pháp (parse) và thực thi các lệnh FTP theo chuẩn RFC 959.
Bao gồm:
- Xác thực: USER, PASS
- Hệ thống & Cấu hình: SYST, FEAT, NOOP, QUIT, TYPE, OPTS
- Thư mục & Tệp: PWD, CWD, CDUP, MKD, RMD, DELE, RNFR, RNTO, SIZE, MDTM
- Kết nối dữ liệu: PORT, PASV
- Truyền dữ liệu: LIST, NLST, RETR, STOR, APPE, REST, ABOR
- Bảo mật FTPS: AUTH TLS, PBSZ, PROT
"""

import os
import time
import datetime
import stat
import logging
from typing import Optional, Callable
from .session import FTPSession

logger = logging.getLogger("FTPServer.CommandHandler")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class FTPCommandHandler:
    """Xử lý và phản hồi các lệnh FTP từ client gửi tới."""

    def __init__(
        self,
        session: FTPSession,
        server_event_callback: Optional[Callable[[str, dict], None]] = None,
        allow_anonymous: bool = True,
        ssl_context=None,
    ):
        self.session = session
        self.server_event_callback = server_event_callback
        self.allow_anonymous = allow_anonymous
        self.ssl_context = ssl_context
        self.running = True

    def send_response(self, code: int, message: str) -> None:
        """
        Gửi phản hồi chuẩn FTP kết thúc bằng \\r\\n tới client.
        Định dạng: <code> <message>\\r\\n
        """
        response_str = f"{code} {message}\r\n"
        try:
            self.session.client_socket.sendall(response_str.encode("utf-8", errors="replace"))
            logger.debug(f"[{self.session.client_address}] Phản hồi: {code} {message}")
        except Exception as e:
            logger.error(f"[{self.session.client_address}] Lỗi gửi phản hồi: {e}")
            self.running = False

    def send_multiline_response(self, code: int, lines: list) -> None:
        """Gửi phản hồi nhiều dòng theo chuẩn RFC 959 (ví dụ lệnh FEAT)."""
        if not lines:
            self.send_response(code, "OK")
            return
        first_line = f"{code}-{lines[0]}\r\n"
        middle_lines = "".join(f" {line}\r\n" for line in lines[1:-1])
        last_line = f"{code} {lines[-1]}\r\n" if len(lines) > 1 else f"{code} End\r\n"
        full_response = first_line + middle_lines + last_line
        try:
            self.session.client_socket.sendall(full_response.encode("utf-8", errors="replace"))
        except Exception as e:
            logger.error(f"Lỗi gửi phản hồi nhiều dòng: {e}")
            self.running = False

    def handle_command(self, raw_line: str) -> None:
        """Phân tách chuỗi lệnh thô từ client và gọi hàm xử lý tương ứng."""
        line = raw_line.strip()
        if not line:
            return

        parts = line.split(" ", 1)
        cmd = parts[0].upper()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # Ghi log sự kiện cho giao diện quản trị
        if self.server_event_callback:
            self.server_event_callback(
                "COMMAND_RECEIVED",
                {
                    "client": self.session.client_address,
                    "user": self.session.username,
                    "command": cmd,
                    "arg": arg if cmd != "PASS" else "******",
                },
            )

        handler_method = getattr(self, f"do_{cmd}", None)
        if handler_method:
            try:
                handler_method(arg)
            except PermissionError as pe:
                logger.warning(f"Lỗi quyền hạn khi thực thi {cmd}: {pe}")
                self.send_response(550, f"Quyền bị từ chối: {pe}")
                self._log_cmd(cmd, arg, 550)
            except Exception as e:
                logger.error(f"Lỗi ngoại lệ khi thực thi lệnh {cmd} ({arg}): {e}", exc_info=True)
                self.send_response(550, f"Lỗi nội bộ: {str(e)}")
                self._log_cmd(cmd, arg, 550)
        else:
            self.send_response(502, f"Lenh chua duoc ho tro ({cmd}).")
            self._log_cmd(cmd, arg, 502)

    def _log_cmd(self, cmd: str, arg: str, code: int) -> None:
        """Ghi nhận lệnh vào database."""
        try:
            safe_arg = "******" if cmd == "PASS" else arg
            self.session.db.log_command(
                self.session.username, self.session.client_address[0], cmd, safe_arg, code
            )
        except Exception:
            pass

    # ==================== CÁC LỆNH XÁC THỰC ====================

    def do_USER(self, arg: str) -> None:
        """Xử lý lệnh USER - Nhận tên tài khoản."""
        if not arg:
            self.send_response(501, "Cu phap sai: USER <username>")
            return

        username = arg.strip()
        self.session.user_pending = username

        if username.lower() == "anonymous":
            if not self.allow_anonymous:
                self.send_response(530, "Che do Anonymous da bi tat boi quan tri vien.")
                self._log_cmd("USER", username, 530)
                return
            self.send_response(331, "Anonymous login ok, gui email lam mat khau.")
        else:
            self.send_response(331, f"Yeu cau mat khau cho tai khoan {username}.")
        self._log_cmd("USER", username, 331)

    def do_PASS(self, arg: str) -> None:
        """Xử lý lệnh PASS - Xác thực mật khẩu trong SQLite Database."""
        if not self.session.user_pending:
            self.send_response(503, "Can gui lenh USER truoc.")
            return

        username = self.session.user_pending
        password = arg

        # Kiểm tra anonymous
        if username.lower() == "anonymous":
            if not self.allow_anonymous:
                self.send_response(530, "Tai khoan Anonymous khong duoc phep.")
                self._log_cmd("PASS", "", 530)
                return
            # Tìm hoặc gán user anonymous
            user_info = self.session.db.get_user("anonymous")
            if not user_info:
                # Nếu chưa tạo trong db, dùng thư mục mặc định
                default_anon_dir = os.path.abspath("./ftp_root/anonymous")
                os.makedirs(default_anon_dir, exist_ok=True)
                user_info = {
                    "id": 0,
                    "username": "anonymous",
                    "home_dir": default_anon_dir,
                    "can_read": 1,
                    "can_write": 0,
                    "can_delete": 0,
                    "is_active": 1,
                    "quota_bytes": 0,
                    "speed_limit_kb": 0,
                }
            self.session.user = user_info
            self.session.is_authenticated = True
            self.session.home_dir = user_info["home_dir"]
            self.session.current_directory = "/"
            self.session.session_id = self.session.db.log_session_start(
                username, self.session.client_address[0]
            )
            self.send_response(230, "Dang nhap Anonymous thanh cong.")
            self._log_cmd("PASS", "", 230)
            if self.server_event_callback:
                self.server_event_callback("CLIENT_LOGGED_IN", {"session": self.session})
            return

        # Xác thực người dùng bình thường qua database
        user_info = self.session.db.authenticate_user(username, password)
        if user_info:
            self.session.user = user_info
            self.session.is_authenticated = True
            self.session.home_dir = user_info["home_dir"]
            self.session.current_directory = "/"
            os.makedirs(self.session.home_dir, exist_ok=True)

            self.session.session_id = self.session.db.log_session_start(
                username, self.session.client_address[0]
            )
            self.send_response(230, f"Dang nhap thanh cong. Xin chao {username}!")
            self._log_cmd("PASS", "", 230)
            if self.server_event_callback:
                self.server_event_callback("CLIENT_LOGGED_IN", {"session": self.session})
        else:
            self.send_response(530, "Dang nhap that bai: Sai ten dang nhap hoac mat khau.")
            self._log_cmd("PASS", "", 530)

    def _require_auth(self) -> bool:
        """Hàm kiểm tra bắt buộc phải đăng nhập trước khi chạy lệnh khác."""
        if not self.session.is_authenticated:
            self.send_response(530, "Vui long dang nhap (USER va PASS) truoc.")
            return False
        return True

    # ==================== HỆ THỐNG & CẤU HÌNH ====================

    def do_SYST(self, arg: str) -> None:
        """Trả về loại hệ điều hành (chuẩn UNIX)."""
        self.send_response(215, "UNIX Type: L8")
        self._log_cmd("SYST", "", 215)

    def do_FEAT(self, arg: str) -> None:
        """Trả về danh sách tính năng mở rộng được hỗ trợ."""
        features = [
            "Features:",
            "MDTM",
            "REST STREAM",
            "SIZE",
            "UTF8",
            "AUTH TLS",
            "PBSZ",
            "PROT",
            "End",
        ]
        self.send_multiline_response(211, features)
        self._log_cmd("FEAT", "", 211)

    def do_OPTS(self, arg: str) -> None:
        """Xử lý các tùy chọn client gửi, ví dụ OPTS UTF8 ON."""
        parts = arg.upper().split()
        if parts and parts[0] == "UTF8":
            self.send_response(200, "Always in UTF8 mode.")
            self._log_cmd("OPTS", arg, 200)
        else:
            self.send_response(501, "Tuy chon khong hop le.")
            self._log_cmd("OPTS", arg, 501)

    def do_NOOP(self, arg: str) -> None:
        """Giữ kết nối (No Operation)."""
        self.send_response(200, "NOOP ok.")
        self._log_cmd("NOOP", "", 200)

    def do_QUIT(self, arg: str) -> None:
        """Client yêu cầu ngắt kết nối."""
        self.send_response(221, "Tam biet, hen gap lai!")
        self._log_cmd("QUIT", "", 221)
        self.running = False

    def do_TYPE(self, arg: str) -> None:
        """
        Chọn kiểu truyền:
        - TYPE A: ASCII (chuyển đổi ký tự xuống dòng)
        - TYPE I: Image/Binary (truyền nguyên vẹn từng byte)
        """
        mode = arg.upper()
        if mode in ("A", "ASCII"):
            self.session.transfer_type = "A"
            self.send_response(200, "Chuyen sang che do ASCII.")
            self._log_cmd("TYPE", "A", 200)
        elif mode in ("I", "BINARY", "L 8"):
            self.session.transfer_type = "I"
            self.send_response(200, "Chuyen sang che do BINARY.")
            self._log_cmd("TYPE", "I", 200)
        else:
            self.send_response(504, f"Che do TYPE khong ho tro: {arg}")
            self._log_cmd("TYPE", arg, 504)

    # ==================== ĐIỀU HƯỚNG THƯ MỤC ====================

    def do_PWD(self, arg: str) -> None:
        """In đường dẫn thư mục ảo hiện tại."""
        if not self._require_auth():
            return
        vpath = self.session.current_directory
        self.send_response(257, f'"{vpath}" la thu muc hien tai.')
        self._log_cmd("PWD", "", 257)

    def do_CWD(self, arg: str) -> None:
        """Đổi thư mục làm việc (Change Working Directory)."""
        if not self._require_auth():
            return
        if not arg:
            self.send_response(501, "Can chi dinh duong dan thu muc.")
            return

        real_path, vpath = self.session.resolve_path(arg)
        if os.path.exists(real_path) and os.path.isdir(real_path):
            self.session.current_directory = vpath
            self.send_response(250, f"Doi thu muc thanh cong sang {vpath}.")
            self._log_cmd("CWD", arg, 250)
        else:
            self.send_response(550, f"Thu muc khong ton tai: {arg}")
            self._log_cmd("CWD", arg, 550)

    def do_CDUP(self, arg: str) -> None:
        """Lên thư mục cha (Change to Parent Directory)."""
        self.do_CWD("..")

    # ==================== THAO TÁC TỆP & THƯ MỤC ====================

    def do_MKD(self, arg: str) -> None:
        """Tạo thư mục mới (Make Directory). Cần quyền WRITE."""
        if not self._require_auth():
            return
        if not self.session.check_permission("WRITE"):
            self.send_response(550, "Khong du quyen tao thu muc (Can quyen WRITE).")
            self._log_cmd("MKD", arg, 550)
            return

        if not arg:
            self.send_response(501, "Can nhap ten thu muc can tao.")
            return

        real_path, vpath = self.session.resolve_path(arg)
        try:
            os.makedirs(real_path, exist_ok=False)
            self.send_response(257, f'"{vpath}" thu muc da duoc tao.')
            self._log_cmd("MKD", arg, 257)
        except FileExistsError:
            self.send_response(550, "Thu muc da ton tai.")
            self._log_cmd("MKD", arg, 550)
        except Exception as e:
            self.send_response(550, f"Loi tao thu muc: {e}")
            self._log_cmd("MKD", arg, 550)

    def do_RMD(self, arg: str) -> None:
        """Xóa thư mục rỗng (Remove Directory). Cần quyền DELETE."""
        if not self._require_auth():
            return
        if not self.session.check_permission("DELETE"):
            self.send_response(550, "Khong du quyen xoa thu muc (Can quyen DELETE).")
            self._log_cmd("RMD", arg, 550)
            return

        real_path, _ = self.session.resolve_path(arg)
        try:
            if not os.path.exists(real_path):
                self.send_response(550, "Thu muc khong ton tai.")
                self._log_cmd("RMD", arg, 550)
                return
            if not os.path.isdir(real_path):
                self.send_response(550, "Duong dan chi dinh khong phai la thu muc.")
                self._log_cmd("RMD", arg, 550)
                return
            os.rmdir(real_path)
            self.send_response(250, "Xoa thu muc thanh cong.")
            self._log_cmd("RMD", arg, 250)
        except OSError as e:
            self.send_response(550, f"Khong the xoa thu muc (co the chua tep): {e}")
            self._log_cmd("RMD", arg, 550)

    def do_DELE(self, arg: str) -> None:
        """Xóa file (Delete File). Cần quyền DELETE."""
        if not self._require_auth():
            return
        if not self.session.check_permission("DELETE"):
            self.send_response(550, "Khong du quyen xoa tep (Can quyen DELETE).")
            self._log_cmd("DELE", arg, 550)
            return

        real_path, _ = self.session.resolve_path(arg)
        try:
            if not os.path.exists(real_path):
                self.send_response(550, "Tep khong ton tai.")
                self._log_cmd("DELE", arg, 550)
                return
            if os.path.isdir(real_path):
                self.send_response(550, "Day la thu muc, hay dung lenh RMD.")
                self._log_cmd("DELE", arg, 550)
                return
            os.remove(real_path)
            self.send_response(250, "Xoa tep thanh cong.")
            self._log_cmd("DELE", arg, 250)
        except Exception as e:
            self.send_response(550, f"Khong the xoa tep: {e}")
            self._log_cmd("DELE", arg, 550)

    def do_RNFR(self, arg: str) -> None:
        """Đổi tên file bước 1: Rename From. Cần quyền WRITE."""
        if not self._require_auth():
            return
        if not self.session.check_permission("WRITE"):
            self.send_response(550, "Khong du quyen doi ten (Can quyen WRITE).")
            return

        real_path, _ = self.session.resolve_path(arg)
        if os.path.exists(real_path):
            self.session.rnfr_path = real_path
            self.send_response(350, "Tep hop le, san sang nhan lenh RNTO.")
            self._log_cmd("RNFR", arg, 350)
        else:
            self.send_response(550, "Tep can doi ten khong ton tai.")
            self._log_cmd("RNFR", arg, 550)

    def do_RNTO(self, arg: str) -> None:
        """Đổi tên file bước 2: Rename To."""
        if not self._require_auth():
            return
        if not self.session.rnfr_path:
            self.send_response(503, "Can gui lenh RNFR truoc.")
            return

        dest_real_path, _ = self.session.resolve_path(arg)
        try:
            os.rename(self.session.rnfr_path, dest_real_path)
            self.send_response(250, "Doi ten thanh cong.")
            self._log_cmd("RNTO", arg, 250)
        except Exception as e:
            self.send_response(550, f"Doi ten that bai: {e}")
            self._log_cmd("RNTO", arg, 550)
        finally:
            self.session.rnfr_path = None

    def do_SIZE(self, arg: str) -> None:
        """Trả về kích thước tệp tính theo byte."""
        if not self._require_auth():
            return
        real_path, _ = self.session.resolve_path(arg)
        if os.path.isfile(real_path):
            size = os.path.getsize(real_path)
            self.send_response(213, str(size))
            self._log_cmd("SIZE", arg, 213)
        else:
            self.send_response(550, "Tep khong ton tai hoac la thu muc.")
            self._log_cmd("SIZE", arg, 550)

    def do_MDTM(self, arg: str) -> None:
        """Trả về thời gian sửa đổi gần nhất của tệp định dạng YYYYMMDDhhmmss."""
        if not self._require_auth():
            return
        real_path, _ = self.session.resolve_path(arg)
        if os.path.exists(real_path):
            mtime = os.path.getmtime(real_path)
            utc_time = datetime.datetime.utcfromtimestamp(mtime).strftime("%Y%m%d%H%M%S")
            self.send_response(213, utc_time)
            self._log_cmd("MDTM", arg, 213)
        else:
            self.send_response(550, "Khong tim thay tep.")
            self._log_cmd("MDTM", arg, 550)

    def do_REST(self, arg: str) -> None:
        """Thiết lập vị trí offset để tiếp tục truyền file (Resume Download/Upload)."""
        try:
            offset = int(arg)
            if offset < 0:
                raise ValueError
            self.session.rest_offset = offset
            self.send_response(350, f"Bat dau tiep tuc tai vi tri offset {offset}.")
            self._log_cmd("REST", str(offset), 350)
        except ValueError:
            self.send_response(501, "Gia tri offset phai la so nguyen duong.")
            self._log_cmd("REST", arg, 501)

    # ==================== KẾT NỐI DỮ LIỆU (PORT / PASV) ====================

    def do_PASV(self, arg: str) -> None:
        """
        Passive Mode: Server mở cổng ngẫu nhiên trong dải cấu hình và trả về mã 227.
        Ví dụ: 227 Entering Passive Mode (127,0,0,1,195,80) -> Cổng = 195*256 + 80 = 50000.
        """
        try:
            # Lấy địa chỉ IP server mà client đang kết nối tới
            server_ip = self.session.client_socket.getsockname()[0]
            ip_str, p1, p2 = self.session.data_manager.setup_passive(server_ip)
            ip_parts = ip_str.replace(".", ",")
            self.send_response(227, f"Entering Passive Mode ({ip_parts},{p1},{p2}).")
            self._log_cmd("PASV", "", 227)
        except Exception as e:
            logger.error(f"Lỗi thiết lập PASV: {e}")
            self.send_response(425, "Khong the mo cong Passive Mode.")
            self._log_cmd("PASV", "", 425)

    def do_PORT(self, arg: str) -> None:
        """
        Active Mode: Client thông báo IP và cổng của client cho Server kết nối tới.
        Cú pháp: PORT h1,h2,h3,h4,p1,p2
        """
        parts = arg.split(",")
        if len(parts) != 6:
            self.send_response(501, "Cu phap lenh PORT khong dung. Can: h1,h2,h3,h4,p1,p2")
            self._log_cmd("PORT", arg, 501)
            return

        try:
            ip = ".".join(parts[0:4])
            port = int(parts[4]) * 256 + int(parts[5])
            self.session.data_manager.setup_active(ip, port)
            self.send_response(200, "Lenh PORT thanh cong.")
            self._log_cmd("PORT", f"{ip}:{port}", 200)
        except Exception as e:
            self.send_response(501, f"Loi thong so lenh PORT: {e}")
            self._log_cmd("PORT", arg, 501)

    # ==================== TRUYỀN DỮ LIỆU (LIST, RETR, STOR) ====================

    def do_LIST(self, arg: str) -> None:
        """
        Gửi danh sách thư mục theo định dạng Unix 'ls -l'.
        Đảm bảo FileZilla và lệnh ftp của Ubuntu đọc và phân tích được.
        """
        if not self._require_auth():
            return
        if not self.session.check_permission("READ"):
            self.send_response(550, "Khong co quyen doc thu muc.")
            return

        target_arg = arg.strip()
        # Loại bỏ các flag phổ biến như -l, -la, -a mà một số client gửi kèm
        if target_arg.startswith("-"):
            tokens = target_arg.split(" ", 1)
            target_arg = tokens[1].strip() if len(tokens) > 1 else ""

        real_path, _ = self.session.resolve_path(target_arg)
        if not os.path.exists(real_path):
            self.send_response(550, "Thu muc khong ton tai.")
            self._log_cmd("LIST", arg, 550)
            return

        # 1. Thông báo mở kết nối dữ liệu
        self.send_response(150, "Dang mo ket noi du lieu cho danh sach thu muc.")
        self._log_cmd("LIST", arg, 150)

        data_sock = self.session.data_manager.open_data_socket()
        if not data_sock:
            self.send_response(425, "Khong the mo ket noi du lieu.")
            return

        try:
            listing_lines = []
            if os.path.isdir(real_path):
                entries = sorted(os.listdir(real_path))
                for entry in entries:
                    entry_path = os.path.join(real_path, entry)
                    line = self._format_ls_line(entry_path, entry)
                    listing_lines.append(line)
            else:
                line = self._format_ls_line(real_path, os.path.basename(real_path))
                listing_lines.append(line)

            data_str = "".join(f"{line}\r\n" for line in listing_lines)
            data_sock.sendall(data_str.encode("utf-8", errors="replace"))
            data_sock.close()
            self.send_response(226, "Gui danh sach thu muc thanh cong.")
        except Exception as e:
            logger.error(f"Lỗi khi gửi dữ liệu LIST: {e}")
            self.send_response(426, "Ket noi bi gian doan khi gui danh sach.")
        finally:
            try:
                data_sock.close()
            except Exception:
                pass
            self.session.data_manager.close()

    def do_NLST(self, arg: str) -> None:
        """Gửi danh sách tên tệp (Name List), mỗi tệp một dòng kết thúc bằng \\r\\n."""
        if not self._require_auth():
            return
        real_path, _ = self.session.resolve_path(arg)
        if not os.path.exists(real_path) or not os.path.isdir(real_path):
            self.send_response(550, "Thu muc khong ton tai.")
            return

        self.send_response(150, "Mo ket noi du lieu cho NLST.")
        data_sock = self.session.data_manager.open_data_socket()
        if not data_sock:
            self.send_response(425, "Khong the mo ket noi du lieu.")
            return

        try:
            entries = sorted(os.listdir(real_path))
            data_str = "".join(f"{entry}\r\n" for entry in entries)
            data_sock.sendall(data_str.encode("utf-8", errors="replace"))
            data_sock.close()
            self.send_response(226, "Gui NLST thanh cong.")
        except Exception as e:
            self.send_response(426, f"Loi khi gui NLST: {e}")
        finally:
            try:
                data_sock.close()
            except Exception:
                pass
            self.session.data_manager.close()

    def _format_ls_line(self, full_path: str, name: str) -> str:
        """
        Định dạng một dòng theo Unix `ls -l`.
        Ví dụ:
        drwxr-xr-x   2 ftp      ftp          4096 Nov 14 14:30 myfolder
        -rw-r--r--   1 ftp      ftp        102400 Nov 14 14:30 myfile.zip
        """
        try:
            st = os.stat(full_path)
            is_dir = stat.S_ISDIR(st.st_mode)
            perm_type = "d" if is_dir else "-"
            # Quyền cơ bản rwxr-xr-x hoặc rw-r--r--
            perms = perm_type + "rwxr-xr-x" if is_dir else perm_type + "rw-r--r--"
            nlinks = 2 if is_dir else 1
            owner = "ftp"
            group = "ftp"
            size = st.st_size

            # Format ngày tháng
            mtime = datetime.datetime.fromtimestamp(st.st_mtime)
            now = datetime.datetime.now()
            month = MONTHS[mtime.month - 1]
            day = mtime.day
            if (now - mtime).days > 180:
                time_str = f"{mtime.year:>5}"
            else:
                time_str = f"{mtime.hour:02d}:{mtime.minute:02d}"

            return f"{perms} {nlinks:>3} {owner:<8} {group:<8} {size:>8} {month} {day:>2} {time_str} {name}"
        except Exception:
            return f"-rw-r--r--   1 ftp      ftp             0 Jan  1 00:00 {name}"

    def do_RETR(self, arg: str) -> None:
        """
        Tải tệp từ Server về Client (Download).
        Hỗ trợ:
        - Kiểm tra quyền READ
        - Truyền file nhị phân theo khối buffer 8 KB
        - Tiếp tục tải dở dang (REST offset)
        - Giới hạn tốc độ tải (Speed Throttle)
        - Ghi log và thống kê vào database
        """
        if not self._require_auth():
            return
        if not self.session.check_permission("READ"):
            self.send_response(550, "Khong co quyen doc tai nguyen (Can quyen READ).")
            self._log_cmd("RETR", arg, 550)
            return

        real_path, _ = self.session.resolve_path(arg)
        if not os.path.exists(real_path) or os.path.isdir(real_path):
            self.send_response(550, f"Tep khong ton tai hoac la thu muc: {arg}")
            self._log_cmd("RETR", arg, 550)
            return

        file_size = os.path.getsize(real_path)
        offset = self.session.rest_offset
        self.session.rest_offset = 0  # Reset offset sau khi sử dụng

        if offset > file_size:
            self.send_response(550, f"Offset bat hop le ({offset} > {file_size}).")
            return

        self.send_response(
            150, f"Mo ket noi du lieu BINARY cho {arg} ({file_size - offset} bytes)."
        )
        self._log_cmd("RETR", arg, 150)

        data_sock = self.session.data_manager.open_data_socket()
        if not data_sock:
            self.send_response(425, "Khong the mo ket noi du lieu.")
            return

        start_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_ts = time.time()
        bytes_sent = 0
        status = "SUCCESS"

        try:
            with open(real_path, "rb") as f:
                if offset > 0:
                    f.seek(offset)
                buffer_size = 8192  # 8 KB Buffer
                while True:
                    chunk = f.read(buffer_size)
                    if not chunk:
                        break
                    data_sock.sendall(chunk)
                    bytes_sent += len(chunk)

                    # Điều tiết tốc độ nếu có cấu hình
                    self.session.throttle(bytes_sent, start_ts)

            data_sock.close()
            self.send_response(226, "Truyen tep hoan tat thanh cong.")
            self._log_cmd("RETR", arg, 226)
        except Exception as e:
            logger.error(f"Lỗi khi truyền tải tệp RETR: {e}")
            status = "FAILED"
            self.send_response(426, "Ket noi bi gian doan trong khi truyen file.")
        finally:
            try:
                data_sock.close()
            except Exception:
                pass
            self.session.data_manager.close()
            end_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.session.bytes_sent += bytes_sent
            # Lưu nhật ký truyền file vào SQLite
            self.session.db.log_transfer(
                self.session.username,
                os.path.basename(real_path),
                "DOWNLOAD",
                file_size,
                bytes_sent,
                status,
                start_time_str,
                end_time_str,
            )

    def do_STOR(self, arg: str) -> None:
        """
        Tải tệp từ Client lên Server (Upload).
        Hỗ trợ:
        - Kiểm tra quyền WRITE
        - Kiểm tra hạn mức dung lượng (Quota)
        - Lưu theo khối buffer 8 KB
        - Ghi log và thống kê vào database
        """
        self._store_file(arg, append=False)

    def do_APPE(self, arg: str) -> None:
        """Ghi nối tiếp vào tệp có sẵn (Append)."""
        self._store_file(arg, append=True)

    def _store_file(self, filename: str, append: bool = False) -> None:
        """Thực hiện lưu file cho cả lệnh STOR và APPE."""
        if not self._require_auth():
            return
        if not self.session.check_permission("WRITE"):
            self.send_response(550, "Khong co quyen ghi tep len may chu (Can quyen WRITE).")
            self._log_cmd("STOR" if not append else "APPE", filename, 550)
            return

        if not filename:
            self.send_response(501, "Can chi dinh ten tep.")
            return

        # Kiểm tra dung lượng quota trước khi nhận file
        if not self.session.check_quota(incoming_bytes=1024):
            self.send_response(552, "Vuot qua han muc dung luong luu tru (Quota exceeded).")
            self._log_cmd("STOR", filename, 552)
            return

        real_path, _ = self.session.resolve_path(filename)
        offset = self.session.rest_offset
        self.session.rest_offset = 0

        self.send_response(150, "Mo ket noi du lieu BINARY de nhan file.")
        self._log_cmd("STOR" if not append else "APPE", filename, 150)

        data_sock = self.session.data_manager.open_data_socket()
        if not data_sock:
            self.send_response(425, "Khong the mo ket noi du lieu.")
            return

        mode = "ab" if append else ("r+b" if offset > 0 and os.path.exists(real_path) else "wb")
        start_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_ts = time.time()
        bytes_received = 0
        status = "SUCCESS"

        try:
            with open(real_path, mode) as f:
                if offset > 0 and not append:
                    f.seek(offset)
                buffer_size = 8192
                while True:
                    chunk = data_sock.recv(buffer_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_received += len(chunk)

                    # Kiểm tra quota định kỳ trong lúc upload
                    if bytes_received % (1024 * 1024) == 0:  # Mỗi 1MB
                        if not self.session.check_quota(incoming_bytes=0):
                            raise OverflowError("Vuot qua han muc dung luong trong luc upload!")

                    # Điều tiết tốc độ
                    self.session.throttle(bytes_received, start_ts)

            data_sock.close()
            self.send_response(226, "Nhan tep va luu thanh cong.")
            self._log_cmd("STOR", filename, 226)
        except OverflowError as oe:
            logger.warning(f"Lỗi quota: {oe}")
            status = "FAILED"
            self.send_response(552, str(oe))
        except Exception as e:
            logger.error(f"Lỗi khi nhận dữ liệu STOR: {e}")
            status = "FAILED"
            self.send_response(426, "Ket noi bi gian doan khi dang truyen.")
        finally:
            try:
                data_sock.close()
            except Exception:
                pass
            self.session.data_manager.close()
            end_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.session.bytes_received += bytes_received

            # Lưu vào database
            final_size = os.path.getsize(real_path) if os.path.exists(real_path) else bytes_received
            self.session.db.log_transfer(
                self.session.username,
                os.path.basename(real_path),
                "UPLOAD",
                final_size,
                bytes_received,
                status,
                start_time_str,
                end_time_str,
            )

    def do_ABOR(self, arg: str) -> None:
        """Hủy bỏ thao tác truyền file hiện tại."""
        self.session.data_manager.close()
        self.send_response(226, "Da huy thao tac truyen du lieu.")
        self._log_cmd("ABOR", "", 226)

    # ==================== BẢO MẬT FTPS (TLS / SSL) ====================

    def do_AUTH(self, arg: str) -> None:
        """
        Nâng cấp kết nối điều khiển lên TLS (FTPS).
        Lệnh chuẩn: AUTH TLS
        """
        if arg.upper() == "TLS":
            if not self.ssl_context:
                self.send_response(502, "Server chua duoc cau hinh chung chi SSL/TLS.")
                self._log_cmd("AUTH", arg, 502)
                return

            self.send_response(234, "San sang bat dau bat tay TLS (Enabling TLS).")
            self._log_cmd("AUTH", arg, 234)
            try:
                # Nâng cấp socket điều khiển hiện tại bằng wrap_socket
                wrapped_sock = self.ssl_context.wrap_socket(
                    self.session.client_socket, server_side=True
                )
                self.session.client_socket = wrapped_sock
                self.session.tls_enabled = True
                logger.info(f"[{self.session.client_address}] Đã nâng cấp kênh điều khiển lên TLS!")
            except Exception as e:
                logger.error(f"Lỗi bắt tay TLS: {e}")
                self.running = False
        else:
            self.send_response(504, f"Co che xac thuc khong ho tro: {arg}")
            self._log_cmd("AUTH", arg, 504)

    def do_PBSZ(self, arg: str) -> None:
        """Protection Buffer Size: Chuẩn TLS yêu cầu tham số = 0."""
        if not self.session.tls_enabled:
            self.send_response(503, "Can thuc hien AUTH TLS truoc.")
            return
        self.send_response(200, "PBSZ=0 thanh cong.")
        self._log_cmd("PBSZ", arg, 200)

    def do_PROT(self, arg: str) -> None:
        """
        Protection Level:
        - PROT C: Clear (kênh dữ liệu không mã hóa)
        - PROT P: Private (kênh dữ liệu được mã hóa bằng TLS)
        """
        if not self.session.tls_enabled:
            self.send_response(503, "Can thuc hien AUTH TLS truoc.")
            return

        level = arg.upper()
        if level == "P":
            self.session.prot_p = True
            self.session.data_manager.set_ssl(self.ssl_context, prot_p=True)
            self.send_response(200, "Che do bao ve du lieu dat thanh P (Private - Encrypted).")
            self._log_cmd("PROT", "P", 200)
        elif level == "C":
            self.session.prot_p = False
            self.session.data_manager.set_ssl(None, prot_p=False)
            self.send_response(200, "Che do bao ve du lieu dat thanh C (Clear).")
            self._log_cmd("PROT", "C", 200)
        else:
            self.send_response(504, f"Che do PROT khong hop le: {level}")
            self._log_cmd("PROT", arg, 504)

