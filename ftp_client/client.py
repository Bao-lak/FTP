"""
Module Lõi FTP Client (Tự cài đặt hoàn toàn bằng Socket - RFC 959).
Hỗ trợ đầy đủ:
- Kênh điều khiển (Control Connection) và phản hồi đa dòng RFC 959.
- Chế độ Passive Mode (PASV) và Active Mode (PORT).
- Chế độ truyền Binary (TYPE I) và ASCII (TYPE A).
- Tiếp tục tải (Resume - REST) cho cả Upload (STOR) và Download (RETR).
- Duyệt thư mục và phân tích định dạng Unix 'ls -l' chuẩn hóa YYYY-MM-DD HH:MM.
- Thao tác tệp: STOR, RETR, MKD, RMD, DELE, RNFR/RNTO, SIZE, MDTM.
- Lệnh mở rộng tùy chỉnh:
  + XSHA256: Kiểm tra toàn vẹn băm SHA-256 từ xa.
  + XQUOTA: Lấy dung lượng đã dùng / tối đa từ SQLite database của server.
  + XHISTORY: Lấy danh sách lịch sử truyền file từ database.
- Tạm dừng (Pause), Tiếp tục (Resume), Hủy (Cancel) luồng truyền dữ liệu.
- Hỗ trợ mã hóa FTPS (AUTH TLS, PBSZ 0, PROT P).
- Đồng bộ hóa đa luồng với RLock tránh xung đột kênh điều khiển.
- Báo lỗi chi tiết bằng tiếng Việt.
"""

import os
import re
import socket
import ssl
import time
import datetime
import hashlib
import logging
import threading
from typing import Optional, List, Dict, Any, Tuple, Callable

logger = logging.getLogger("FTPClient")


class FTPClientError(Exception):
    """Lớp ngoại lệ tùy chỉnh cho các lỗi phía FTP Client."""
    pass


def parse_unix_mtime(mtime_str: str) -> str:
    """
    Chuẩn hóa thời gian sửa đổi Unix 'ls -l' về định dạng duy nhất YYYY-MM-DD HH:MM.
    Ví dụ đầu vào: 'Nov 14 21:39', 'Nov 14  2024', '2026-11-14 21:39'.
    """
    if not mtime_str:
        return ""
    mtime_str = " ".join(mtime_str.split())  # Chuẩn hóa khoảng trắng kép
    # Nếu đã đúng định dạng YYYY-MM-DD HH:MM
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", mtime_str):
        return mtime_str

    current_year = datetime.datetime.now().year
    # Dạng 1: "Nov 14 21:39" (thời gian trong vòng 6 tháng)
    try:
        dt = datetime.datetime.strptime(f"{current_year} {mtime_str}", "%Y %b %d %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass

    # Dạng 2: "Nov 14 2024" (tệp cũ hơn 6 tháng)
    try:
        dt = datetime.datetime.strptime(mtime_str, "%b %d %Y")
        return dt.strftime("%Y-%m-%d 00:00")
    except ValueError:
        pass

    return mtime_str


class CustomFTPClient:
    """FTP Client được lập trình thuần túy bằng module socket của Python."""

    def __init__(self, log_callback: Optional[Callable[[str, str], None]] = None):
        self.host: str = ""
        self.port: int = 2121
        self.control_socket: Optional[socket.socket] = None
        self.mode: str = "PASV"  # Mặc định Passive Mode (phù hợp với NAT/Firewall)
        self.log_callback = log_callback  # Hàm nhận log để hiển thị lên UI

        self.is_connected: bool = False
        self.is_logged_in: bool = False
        self.current_user: str = ""
        self.tls_active: bool = False
        self.prot_p: bool = False

        # Khóa đồng bộ hóa chống tranh chấp socket điều khiển giữa các luồng
        self._lock = threading.RLock()

    def _log(self, message: str, tag: str = "INFO") -> None:
        """Ghi log nội bộ và gọi callback lên giao diện nếu có."""
        logger.debug(f"[{tag}] {message}")
        if self.log_callback:
            try:
                self.log_callback(message, tag)
            except Exception:
                pass

    def _close_sockets_silently(self) -> None:
        """Đóng socket điều khiển ngầm khi gặp lỗi mạng, tránh lặp đệ quy."""
        self.is_connected = False
        self.is_logged_in = False
        self.tls_active = False
        self.prot_p = False
        if self.control_socket:
            try:
                self.control_socket.close()
            except Exception:
                pass
            self.control_socket = None

    # ==================== GỬI VÀ NHẬN KÊNH ĐIỀU KHIỂN ====================

    def _send_raw(self, cmd_line: str) -> None:
        """Gửi chuỗi lệnh thô kết thúc bằng \\r\\n qua socket điều khiển."""
        if not self.control_socket or not self.is_connected:
            raise FTPClientError("Chưa kết nối tới máy chủ FTP!")
        safe_display = cmd_line
        if cmd_line.upper().startswith("PASS "):
            safe_display = "PASS ******"
        self._log(f"-> {safe_display}", "CMD")
        data = (cmd_line + "\r\n").encode("utf-8")
        try:
            self.control_socket.sendall(data)
        except socket.error as e:
            self._close_sockets_silently()
            raise FTPClientError(f"Mất kết nối điều khiển khi gửi lệnh: {e}")

    def _recv_response(self) -> Tuple[int, str]:
        """
        Đọc và phân tích mã phản hồi từ server.
        Hỗ trợ cả phản hồi đơn dòng (ví dụ: '200 OK\\r\\n')
        và nhiều dòng (ví dụ: '220-Welcome\\r\\n ... \\r\\n220 Ready\\r\\n').
        """
        if not self.control_socket or not self.is_connected:
            raise FTPClientError("Chưa kết nối tới máy chủ FTP!")

        response_lines = []
        code = 0

        while True:
            line_bytes = b""
            while not line_bytes.endswith(b"\n"):
                try:
                    chunk = self.control_socket.recv(1)
                except socket.error as e:
                    self._close_sockets_silently()
                    raise FTPClientError(f"Lỗi đọc phản hồi từ server: {e}")

                if not chunk:
                    self._close_sockets_silently()
                    raise FTPClientError("Mất kết nối với máy chủ FTP!")
                line_bytes += chunk

            line = line_bytes.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue

            response_lines.append(line)

            # Chuẩn RFC 959: Dòng kết thúc có dạng '123 message', dòng tiếp diễn có '123-message'
            if len(line) >= 3 and line[:3].isdigit():
                if len(line) == 3 or line[3] == " ":
                    code = int(line[:3])
                    break

        full_message = "\n".join(response_lines)
        tag = "SUCCESS" if code < 400 else "ERROR"
        self._log(f"<- {full_message}", tag)
        return code, full_message

    def send_cmd(self, cmd: str) -> Tuple[int, str]:
        """Gửi lệnh và chờ nhận mã phản hồi an toàn với khóa luồng."""
        with self._lock:
            self._send_raw(cmd)
            return self._recv_response()

    # ==================== KẾT NỐI VÀ ĐĂNG NHẬP ====================

    def connect(self, host: str, port: int = 2121, timeout: float = 10.0) -> str:
        """Thiết lập kết nối TCP tới FTP Server trên cổng điều khiển."""
        with self._lock:
            self.disconnect()
            self.host = host
            self.port = port

            try:
                self._log(f"Đang kết nối tới {host}:{port}...", "INFO")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                self.control_socket = sock
                self.is_connected = True

                # Đọc lời chào 220 từ server
                code, msg = self._recv_response()
                if code != 220:
                    raise FTPClientError(f"Máy chủ phản hồi mã không hợp lệ: {msg}")

                self._log(f"Đã kết nối thành công tới FTP Server ({host}:{port})!", "SUCCESS")
                return msg
            except socket.error as e:
                self._close_sockets_silently()
                raise FTPClientError(f"Không thể kết nối tới {host}:{port}: {e}")

    def enable_ftps(self) -> None:
        """Kích hoạt mã hóa FTPS bằng lệnh AUTH TLS."""
        with self._lock:
            if not self.is_connected:
                raise FTPClientError("Chưa kết nối tới server!")

            code, msg = self.send_cmd("AUTH TLS")
            if code != 234:
                raise FTPClientError(f"Server không hỗ trợ FTPS: {msg}")

            # Bắt tay TLS
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # Cho phép chứng chỉ tự ký của server

            self.control_socket = ctx.wrap_socket(self.control_socket, server_hostname=self.host)
            self.tls_active = True
            self._log("Đã kích hoạt bảo mật kênh điều khiển (TLS)!", "SUCCESS")

            # Cấu hình bảo vệ dữ liệu PROT P
            self.send_cmd("PBSZ 0")
            code, _ = self.send_cmd("PROT P")
            if code == 200:
                self.prot_p = True
                self._log("Đã bảo mật kênh dữ liệu (PROT P)!", "SUCCESS")

    def login(self, username: str = "anonymous", password: str = "") -> bool:
        """Đăng nhập vào tài khoản FTP."""
        with self._lock:
            if not self.is_connected:
                raise FTPClientError("Chưa kết nối tới server!")

            self.current_user = username
            code, msg = self.send_cmd(f"USER {username}")

            if code == 331:
                # Server yêu cầu mật khẩu
                code, msg = self.send_cmd(f"PASS {password}")

            if code == 230:
                self.is_logged_in = True
                self._log(f"Đăng nhập thành công với tài khoản: {username}", "SUCCESS")
                # Thiết lập mặc định kiểu nhị phân (Binary)
                self.send_cmd("TYPE I")
                return True
            else:
                self.is_logged_in = False
                raise FTPClientError(f"Đăng nhập thất bại ({code}): {msg}")

    def set_mode(self, mode: str) -> None:
        """Chọn chế độ truyền dữ liệu: 'PASV' (Passive) hoặc 'PORT' (Active)."""
        with self._lock:
            mode = mode.upper()
            if mode in ("PASV", "PORT"):
                self.mode = mode
                self._log(f"Đã chuyển sang chế độ dữ liệu: {self.mode}", "INFO")
            else:
                raise ValueError("Chế độ chỉ có thể là 'PASV' hoặc 'PORT'")

    # ==================== THIẾT LẬP KÊNH DỮ LIỆU (DATA CONNECTION) ====================

    def _open_data_connection(self) -> Tuple[socket.socket, Optional[Callable[[], socket.socket]]]:
        """
        Mở socket dữ liệu tùy theo chế độ PASV hoặc PORT:
        - Nếu PASV: gửi lệnh PASV, lấy IP/Port và connect ngay.
        - Nếu PORT: mở socket lắng nghe trên client, gửi lệnh PORT và trả về hàm accept().
        Lưu ý: Hàm này được gọi bên trong các phương thức đã giữ self._lock.
        """
        if self.mode == "PASV":
            code, msg = self.send_cmd("PASV")
            if code != 227:
                raise FTPClientError(f"Không thể vào chế độ Passive Mode: {msg}")

            # Trích xuất 6 số: (h1,h2,h3,h4,p1,p2)
            match = re.search(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)", msg)
            if not match:
                raise FTPClientError(f"Phản hồi PASV không đúng định dạng: {msg}")

            nums = [int(x) for x in match.groups()]
            pasv_ip = f"{nums[0]}.{nums[1]}.{nums[2]}.{nums[3]}"
            pasv_port = nums[4] * 256 + nums[5]

            # Nếu IP trả về là local hoặc không định tuyến được, kết nối trực tiếp đến host của server
            target_ip = self.host if pasv_ip in ("0.0.0.0", "127.0.0.1") else pasv_ip

            data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            data_sock.settimeout(20.0)
            data_sock.connect((target_ip, pasv_port))

            # Nâng cấp TLS nếu PROT P được bật
            if self.tls_active and self.prot_p:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                data_sock = ctx.wrap_socket(data_sock, server_hostname=self.host)

            return data_sock, None

        elif self.mode == "PORT":
            # Active Mode: Client lắng nghe và Server kết nối tới
            listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Lấy địa chỉ IP cục bộ của client mà socket điều khiển đang dùng
            client_ip = self.control_socket.getsockname()[0]
            listen_sock.bind((client_ip, 0))
            listen_sock.listen(1)
            port = listen_sock.getsockname()[1]

            h1, h2, h3, h4 = client_ip.split(".")
            p1 = port // 256
            p2 = port % 256

            code, msg = self.send_cmd(f"PORT {h1},{h2},{h3},{h4},{p1},{p2}")
            if code != 200:
                listen_sock.close()
                raise FTPClientError(f"Lệnh PORT thất bại: {msg}")

            def accept_data_socket() -> socket.socket:
                listen_sock.settimeout(20.0)
                try:
                    sock, _ = listen_sock.accept()
                    listen_sock.close()
                    if self.tls_active and self.prot_p:
                        ctx = ssl.create_default_context()
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                        sock = ctx.wrap_socket(sock, server_hostname=self.host)
                    return sock
                except Exception as e:
                    listen_sock.close()
                    raise FTPClientError(f"Server không thể kết nối tới client qua PORT: {e}")

            return listen_sock, accept_data_socket

        else:
            raise FTPClientError("Chế độ truyền dữ liệu không xác định!")

    # ==================== ĐIỀU HƯỚNG & DUYỆT THƯ MỤC ====================

    def pwd(self) -> str:
        """Lấy đường dẫn thư mục hiện tại trên máy chủ."""
        with self._lock:
            code, msg = self.send_cmd("PWD")
            if code == 257:
                match = re.search(r'"([^"]+)"', msg)
                if match:
                    return match.group(1)
            return "/"

    def cwd(self, path: str) -> None:
        """Chuyển thư mục trên máy chủ."""
        with self._lock:
            code, msg = self.send_cmd(f"CWD {path}")
            if code != 250:
                raise FTPClientError(f"Không thể đổi thư mục ({code}): {msg}")

    def cdup(self) -> None:
        """Lên thư mục cha trên máy chủ."""
        with self._lock:
            code, msg = self.send_cmd("CDUP")
            if code != 250:
                raise FTPClientError(f"Không thể lên thư mục cha: {msg}")

    def mkd(self, dirname: str) -> None:
        """Tạo thư mục mới trên máy chủ."""
        with self._lock:
            code, msg = self.send_cmd(f"MKD {dirname}")
            if code != 257:
                raise FTPClientError(f"Tạo thư mục thất bại ({code}): {msg}")

    def rmd(self, dirname: str) -> None:
        """Xóa thư mục rỗng trên máy chủ."""
        with self._lock:
            code, msg = self.send_cmd(f"RMD {dirname}")
            if code != 250:
                raise FTPClientError(f"Xóa thư mục thất bại ({code}): {msg}")

    def dele(self, filename: str) -> None:
        """Xóa tệp trên máy chủ."""
        with self._lock:
            code, msg = self.send_cmd(f"DELE {filename}")
            if code != 250:
                raise FTPClientError(f"Xóa tệp thất bại ({code}): {msg}")

    def rename(self, old_name: str, new_name: str) -> None:
        """Đổi tên tệp hoặc thư mục trên máy chủ (RNFR / RNTO)."""
        with self._lock:
            code, msg = self.send_cmd(f"RNFR {old_name}")
            if code != 350:
                raise FTPClientError(f"Không thể đổi tên (RNFR {code}): {msg}")

            code, msg = self.send_cmd(f"RNTO {new_name}")
            if code != 250:
                raise FTPClientError(f"Không thể đổi tên (RNTO {code}): {msg}")

    def get_size(self, remote_path: str) -> int:
        """Lấy kích thước tệp từ server bằng lệnh SIZE."""
        with self._lock:
            code, msg = self.send_cmd(f"SIZE {remote_path}")
            if code == 213:
                try:
                    return int(msg.split()[1])
                except (IndexError, ValueError):
                    return 0
            return 0

    def get_mtime(self, remote_path: str) -> str:
        """Lấy thời gian sửa đổi bằng lệnh MDTM (RFC 3659)."""
        with self._lock:
            code, msg = self.send_cmd(f"MDTM {remote_path}")
            if code == 213:
                raw = msg.split()[-1]
                try:
                    dt = datetime.datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
                    return dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    return raw
            return ""

    def list_dir(self, path: str = "") -> List[Dict[str, Any]]:
        """
        Lấy danh sách các tệp và thư mục trên máy chủ qua lệnh LIST.
        Phân tích định dạng Unix 'ls -l' chuẩn thành danh sách các dict.
        Được bao trọn bởi _lock để đảm bảo tính toàn vẹn phiên điều khiển.
        """
        with self._lock:
            data_sock, accept_fn = self._open_data_connection()
            cmd = f"LIST {path}".strip()
            code, msg = self.send_cmd(cmd)

            if code not in (125, 150):
                if data_sock:
                    data_sock.close()
                raise FTPClientError(f"Không thể nhận danh sách thư mục ({code}): {msg}")

            if accept_fn:
                data_sock = accept_fn()

            raw_data = b""
            try:
                while True:
                    chunk = data_sock.recv(4096)
                    if not chunk:
                        break
                    raw_data += chunk
            finally:
                data_sock.close()

            # Đọc phản hồi hoàn tất 226
            code2, msg2 = self._recv_response()
            if code2 != 226:
                self._log(f"Cảnh báo sau khi nhận LIST: {msg2}", "WARN")

            lines = raw_data.decode("utf-8", errors="replace").splitlines()
            items = []
            for line in lines:
                parsed = self._parse_list_line(line)
                if parsed:
                    items.append(parsed)

            return items

    def _parse_list_line(self, line: str) -> Optional[Dict[str, Any]]:
        """
        Phân tích một dòng định dạng Unix 'ls -l'.
        Chuẩn hóa thời gian về YYYY-MM-DD HH:MM và loại hiển thị 'Thư mục' / 'Tệp'.
        """
        line = line.strip()
        if not line or line.startswith("total "):
            return None

        parts = line.split(None, 8)
        if len(parts) < 9:
            return {"name": line, "size": 0, "is_dir": False, "mtime": "", "type": "Tệp"}

        perms = parts[0]
        is_dir = perms.startswith("d")
        try:
            size = int(parts[4])
        except ValueError:
            size = 0

        raw_mtime = f"{parts[5]} {parts[6]} {parts[7]}"
        formatted_mtime = parse_unix_mtime(raw_mtime)
        name = parts[8]

        # Bỏ qua '.' và '..'
        if name in (".", ".."):
            return None

        return {
            "name": name,
            "size": size,
            "is_dir": is_dir,
            "mtime": formatted_mtime,
            "type": "Thư mục" if is_dir else "Tệp",
            "perms": perms,
        }

    def makedirs_remote(self, remote_dir_path: str) -> None:
        """Tạo cây thư mục lồng nhau trên remote (tương tự mkdir -p)."""
        with self._lock:
            parts = [p for p in remote_dir_path.replace("\\", "/").split("/") if p]
            curr = "/" if remote_dir_path.startswith("/") else ""
            for p in parts:
                curr = f"{curr}/{p}" if curr and curr != "/" else f"/{p}"
                try:
                    self.mkd(curr)
                except FTPClientError:
                    pass  # Đã tồn tại thư mục

    # ==================== TRUYỀN TẬP TIN (UPLOAD / DOWNLOAD / RESUME) ====================

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        offset: int = 0,
        resume: bool = False,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Tải tệp từ Server về máy cục bộ.
        Hỗ trợ tiếp tục tải (REST), tạm dừng, hủy và báo cáo tiến trình theo thời gian thực.
        """
        with self._lock:
            # Xác định kích thước tệp hiện có để hỗ trợ resume
            if resume and os.path.exists(local_path):
                offset = os.path.getsize(local_path)

            if offset > 0:
                code, msg = self.send_cmd(f"REST {offset}")
                if code != 350:
                    self._log("Server không hỗ trợ REST, bắt đầu tải lại từ đầu.", "WARN")
                    offset = 0

            # Lấy kích thước tệp từ server
            total_remote_size = self.get_size(remote_path)

            data_sock, accept_fn = self._open_data_connection()
            code, msg = self.send_cmd(f"RETR {remote_path}")
            if code not in (125, 150):
                data_sock.close()
                raise FTPClientError(f"Không thể tải tệp RETR: {msg}")

            if accept_fn:
                data_sock = accept_fn()

            mode = "ab" if offset > 0 else "wb"
            bytes_transferred = offset
            start_time = time.time()
            last_update = start_time

            try:
                with open(local_path, mode) as f:
                    while True:
                        # Kiểm tra hủy
                        if cancel_check and cancel_check():
                            self._log("Người dùng đã hủy quá trình tải tệp.", "WARN")
                            data_sock.close()
                            try:
                                self.send_cmd("ABOR")
                            except Exception:
                                pass
                            return False

                        # Kiểm tra tạm dừng
                        while pause_check and pause_check():
                            if cancel_check and cancel_check():
                                data_sock.close()
                                return False
                            time.sleep(0.2)

                        chunk = data_sock.recv(8192)  # 8 KB Buffer
                        if not chunk:
                            break
                        f.write(chunk)
                        bytes_transferred += len(chunk)

                        now = time.time()
                        if now - last_update >= 0.15:  # Cập nhật tiến độ mỗi 150ms
                            elapsed = now - start_time
                            speed = (bytes_transferred - offset) / elapsed if elapsed > 0 else 0
                            if progress_callback:
                                progress_callback(bytes_transferred, total_remote_size, speed)
                            last_update = now

            finally:
                data_sock.close()

            code2, msg2 = self._recv_response()
            if code2 != 226:
                raise FTPClientError(f"Lỗi khi hoàn tất truyền tệp: {msg2}")

            if progress_callback:
                elapsed = time.time() - start_time
                speed = (bytes_transferred - offset) / elapsed if elapsed > 0 else 0
                progress_callback(bytes_transferred, total_remote_size, speed)

            self._log(f"Tải tệp '{remote_path}' về máy thành công!", "SUCCESS")
            return True

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        offset: int = 0,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Tải tệp từ máy cục bộ lên Server (STOR).
        Hỗ trợ tiếp tục tải (REST <offset>), kiểm tra quota 552, tạm dừng và hủy.
        """
        with self._lock:
            if not os.path.exists(local_path):
                raise FTPClientError(f"Tệp cục bộ không tồn tại: {local_path}")

            total_size = os.path.getsize(local_path)

            # Hỗ trợ resume upload nếu offset > 0
            if offset > 0:
                code, msg = self.send_cmd(f"REST {offset}")
                if code != 350:
                    self._log("Server không hỗ trợ REST cho upload, truyền từ đầu.", "WARN")
                    offset = 0

            data_sock, accept_fn = self._open_data_connection()

            code, msg = self.send_cmd(f"STOR {remote_path}")
            if code == 552:
                data_sock.close()
                raise FTPClientError(f"Dung lượng vượt quá hạn mức Quota cho phép ({msg})!")
            if code not in (125, 150):
                data_sock.close()
                raise FTPClientError(f"Không thể upload tệp ({code}): {msg}")

            if accept_fn:
                data_sock = accept_fn()

            bytes_sent = offset
            start_time = time.time()
            last_update = start_time

            try:
                with open(local_path, "rb") as f:
                    if offset > 0:
                        f.seek(offset)

                    while True:
                        # Kiểm tra hủy
                        if cancel_check and cancel_check():
                            self._log("Người dùng đã hủy quá trình tải lên.", "WARN")
                            data_sock.close()
                            try:
                                self.send_cmd("ABOR")
                            except Exception:
                                pass
                            return False

                        # Kiểm tra tạm dừng
                        while pause_check and pause_check():
                            if cancel_check and cancel_check():
                                data_sock.close()
                                return False
                            time.sleep(0.2)

                        chunk = f.read(8192)
                        if not chunk:
                            break
                        data_sock.sendall(chunk)
                        bytes_sent += len(chunk)

                        now = time.time()
                        if now - last_update >= 0.15:
                            elapsed = now - start_time
                            speed = (bytes_sent - offset) / elapsed if elapsed > 0 else 0
                            if progress_callback:
                                progress_callback(bytes_sent, total_size, speed)
                            last_update = now
            finally:
                data_sock.close()

            code2, msg2 = self._recv_response()
            if code2 != 226:
                raise FTPClientError(f"Lỗi khi hoàn tất upload: {msg2}")

            if progress_callback:
                elapsed = time.time() - start_time
                speed = (bytes_sent - offset) / elapsed if elapsed > 0 else 0
                progress_callback(bytes_sent, total_size, speed)

            self._log(f"Tải tệp '{os.path.basename(local_path)}' lên server thành công!", "SUCCESS")
            return True

    # ==================== LỆNH MỞ RỘNG (XSHA256, XQUOTA, XHISTORY) ====================

    def get_remote_sha256(self, remote_path: str) -> Optional[str]:
        """
        Lấy mã băm SHA-256 của tệp từ server qua lệnh mở rộng XSHA256.
        Phản hồi chuẩn: 213 <64_hex_chars>
        """
        with self._lock:
            code, msg = self.send_cmd(f"XSHA256 {remote_path}")
            if code == 213:
                parts = msg.split()
                if len(parts) >= 2:
                    return parts[1].strip()
            return None

    @staticmethod
    def compute_local_sha256(local_path: str) -> Optional[str]:
        """Tính mã băm SHA-256 của tệp trên máy cục bộ theo khối 64 KB."""
        if not os.path.exists(local_path) or os.path.isdir(local_path):
            return None
        hasher = hashlib.sha256()
        try:
            with open(local_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest().lower()
        except Exception:
            return None

    def get_quota_info(self) -> Optional[Tuple[float, float, float]]:
        """
        Lấy thông tin dung lượng Quota từ server qua lệnh XQUOTA.
        Trả về tuple: (used_mb, max_mb, percent).
        """
        with self._lock:
            code, msg = self.send_cmd("XQUOTA")
            if code == 213:
                # Dạng 1: 213 <used_bytes> <max_bytes>
                parts = msg.split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    used_b = int(parts[1])
                    max_b = int(parts[2])
                    used_mb = round(used_b / (1024 * 1024), 2)
                    max_mb = round(max_b / (1024 * 1024), 2) if max_b > 0 else 0.0
                    pct = round((used_b / max_b * 100), 1) if max_b > 0 else 0.0
                    return used_mb, max_mb, pct

                # Dạng 2: 213 Quota: 12.34 MB / 100.00 MB used
                match = re.search(r"([\d\.]+)\s*MB\s*/\s*([\d\.]+)\s*MB", msg)
                if match:
                    used_mb = float(match.group(1))
                    max_mb = float(match.group(2))
                    pct = (used_mb / max_mb * 100) if max_mb > 0 else 0.0
                    return used_mb, max_mb, pct
            return None

    def get_transfer_history(self, limit: int = 50) -> List[Dict[str, str]]:
        """
        Lấy lịch sử truyền file của user hiện tại từ database qua lệnh XHISTORY <n>.
        Định dạng mỗi dòng từ server:
        filename|direction|file_size|bytes_transferred|status|start_time
        """
        with self._lock:
            code, msg = self.send_cmd(f"XHISTORY {limit}")
            history = []
            if code == 213:
                for line in msg.splitlines():
                    line = line.strip()
                    if not line or line.startswith("213-") or line.startswith("213 End"):
                        continue
                    parts = line.split("|")
                    if len(parts) >= 6:
                        history.append({
                            "file": parts[0],
                            "direction": parts[1].upper(),
                            "size": parts[2],
                            "time": parts[5],
                            "status": parts[4],
                        })
                    elif len(parts) >= 5:
                        history.append({
                            "file": parts[0],
                            "direction": parts[1].upper(),
                            "size": parts[2],
                            "time": parts[3],
                            "status": parts[4],
                        })
            return history

    def disconnect(self) -> None:
        """Ngắt kết nối an toàn khỏi FTP Server."""
        with self._lock:
            was_connected = self.is_connected
            if self.is_connected and self.control_socket:
                try:
                    self.control_socket.sendall(b"QUIT\r\n")
                except Exception:
                    pass
            self._close_sockets_silently()
            if was_connected:
                self._log("Đã ngắt kết nối.", "INFO")
