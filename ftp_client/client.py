"""
Module Lõi FTP Client (Tự cài đặt hoàn toàn bằng Socket - RFC 959).
Hỗ trợ:
- Kết nối kênh điều khiển (Control Connection).
- Chế độ Passive Mode (PASV) và Active Mode (PORT).
- Chế độ truyền Binary (TYPE I) và ASCII (TYPE A).
- Tiếp tục tải (Resume - REST).
- Duyệt thư mục và phân tích (parse) danh sách Unix 'ls -l'.
- Tải lên (STOR), Tải về (RETR), Tạo thư mục (MKD), Xóa (DELE, RMD), Đổi tên (RNFR/RNTO).
- Hỗ trợ mã hóa FTPS (AUTH TLS, PBSZ 0, PROT P).
- Báo lỗi rõ ràng bằng tiếng Việt.
"""

import os
import re
import socket
import ssl
import time
import logging
from typing import Optional, List, Dict, Any, Tuple, Callable

logger = logging.getLogger("FTPClient")


class FTPClientError(Exception):
    """Lớp ngoại lệ tùy chỉnh cho các lỗi phía FTP Client."""
    pass


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

    def _log(self, message: str, tag: str = "INFO") -> None:
        """Ghi log nội bộ và gọi callback lên giao diện nếu có."""
        logger.debug(f"[{tag}] {message}")
        if self.log_callback:
            try:
                self.log_callback(message, tag)
            except Exception:
                pass

    # ==================== GỬI VÀ NHẬN KÊNH ĐIỀU KHIỂN ====================

    def _send_raw(self, cmd_line: str) -> None:
        """Gửi chuỗi lệnh thô kết thúc bằng \\r\\n qua socket điều khiển."""
        if not self.control_socket:
            raise FTPClientError("Chưa kết nối tới máy chủ FTP!")
        safe_display = cmd_line
        if cmd_line.upper().startswith("PASS "):
            safe_display = "PASS ******"
        self._log(f"-> {safe_display}", "CMD")
        data = (cmd_line + "\r\n").encode("utf-8")
        self.control_socket.sendall(data)

    def _recv_response(self) -> Tuple[int, str]:
        """
        Đọc và phân tích mã phản hồi từ server.
        Hỗ trợ cả phản hồi đơn dòng (ví dụ: '200 OK\\r\\n')
        và nhiều dòng (ví dụ: '220-Welcome\\r\\n ... \\r\\n220 Ready\\r\\n').
        """
        if not self.control_socket:
            raise FTPClientError("Chưa kết nối tới máy chủ FTP!")

        response_lines = []
        code = 0

        while True:
            line_bytes = b""
            while not line_bytes.endswith(b"\n"):
                chunk = self.control_socket.recv(1)
                if not chunk:
                    raise FTPClientError("Mất kết nối với máy chủ FTP!")
                line_bytes += chunk

            line = line_bytes.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                continue

            response_lines.append(line)

            # Kiểm tra xem dòng hiện tại có phải là dòng kết thúc không
            # Chuẩn RFC 959: Dòng cuối có định dạng '123 message', dòng tiếp diễn có '123-message'
            if len(line) >= 3 and line[:3].isdigit():
                if len(line) == 3 or line[3] == " ":
                    code = int(line[:3])
                    break

        full_message = "\n".join(response_lines)
        tag = "SUCCESS" if code < 400 else "ERROR"
        self._log(f"<- {full_message}", tag)
        return code, full_message

    def send_cmd(self, cmd: str) -> Tuple[int, str]:
        """Gửi lệnh và chờ nhận mã phản hồi."""
        self._send_raw(cmd)
        return self._recv_response()

    # ==================== KẾT NỐI VÀ ĐĂNG NHẬP ====================

    def connect(self, host: str, port: int = 2121, timeout: float = 10.0) -> str:
        """Thiết lập kết nối TCP tới FTP Server trên cổng điều khiển."""
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
            self.disconnect()
            raise FTPClientError(f"Không thể kết nối tới {host}:{port}: {e}")

    def enable_ftps(self) -> None:
        """Kích hoạt mã hóa FTPS bằng lệnh AUTH TLS."""
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
            data_sock.settimeout(15.0)
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
                listen_sock.settimeout(15.0)
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
        code, msg = self.send_cmd("PWD")
        if code == 257:
            # Trích xuất đường dẫn bên trong dấu nháy kép: "path"
            match = re.search(r'"([^"]+)"', msg)
            if match:
                return match.group(1)
        return "/"

    def cwd(self, path: str) -> None:
        """Chuyển thư mục trên máy chủ."""
        code, msg = self.send_cmd(f"CWD {path}")
        if code != 250:
            raise FTPClientError(f"Không thể đổi thư mục ({code}): {msg}")

    def cdup(self) -> None:
        """Lên thư mục cha trên máy chủ."""
        code, msg = self.send_cmd("CDUP")
        if code != 250:
            raise FTPClientError(f"Không thể lên thư mục cha: {msg}")

    def mkd(self, dirname: str) -> None:
        """Tạo thư mục mới trên máy chủ."""
        code, msg = self.send_cmd(f"MKD {dirname}")
        if code != 257:
            raise FTPClientError(f"Tạo thư mục thất bại: {msg}")

    def rmd(self, dirname: str) -> None:
        """Xóa thư mục rỗng trên máy chủ."""
        code, msg = self.send_cmd(f"RMD {dirname}")
        if code != 250:
            raise FTPClientError(f"Xóa thư mục thất bại: {msg}")

    def dele(self, filename: str) -> None:
        """Xóa tệp trên máy chủ."""
        code, msg = self.send_cmd(f"DELE {filename}")
        if code != 250:
            raise FTPClientError(f"Xóa tệp thất bại: {msg}")

    def rename(self, old_name: str, new_name: str) -> None:
        """Đổi tên tệp hoặc thư mục trên máy chủ (RNFR / RNTO)."""
        code, msg = self.send_cmd(f"RNFR {old_name}")
        if code != 350:
            raise FTPClientError(f"Không thể đổi tên (RNFR): {msg}")

        code, msg = self.send_cmd(f"RNTO {new_name}")
        if code != 250:
            raise FTPClientError(f"Không thể đổi tên (RNTO): {msg}")

    def list_dir(self, path: str = "") -> List[Dict[str, Any]]:
        """
        Lấy danh sách các tệp và thư mục trên máy chủ qua lệnh LIST.
        Phân tích định dạng Unix 'ls -l' chuẩn thành danh sách các dict.
        """
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

        # Phân tích cú pháp từng dòng
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
        Ví dụ: drwxr-xr-x 2 ftp ftp 4096 Nov 14 14:30 myfolder
        """
        line = line.strip()
        if not line or line.startswith("total "):
            return None

        parts = line.split(None, 8)
        if len(parts) < 9:
            # Fallback nếu tên có ít cột
            return {"name": line, "size": 0, "is_dir": False, "mtime": "", "type": "Tệp"}

        perms = parts[0]
        is_dir = perms.startswith("d")
        try:
            size = int(parts[4])
        except ValueError:
            size = 0

        mtime = f"{parts[5]} {parts[6]} {parts[7]}"
        name = parts[8]

        # Bỏ qua '.' và '..'
        if name in (".", ".."):
            return None

        return {
            "name": name,
            "size": size,
            "is_dir": is_dir,
            "mtime": mtime,
            "type": "Thư mục" if is_dir else "Tệp",
            "perms": perms,
        }

    # ==================== TRUYỀN TẬP TIN (UPLOAD / DOWNLOAD) ====================

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        resume: bool = False,
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Tải tệp từ Server về máy cục bộ.
        Hỗ trợ tiếp tục tải (REST) và báo cáo tiến độ theo thời gian thực.
        """
        # Xác định kích thước tệp hiện có để hỗ trợ resume
        offset = 0
        if resume and os.path.exists(local_path):
            offset = os.path.getsize(local_path)

        if offset > 0:
            code, msg = self.send_cmd(f"REST {offset}")
            if code != 350:
                self._log("Server không hỗ trợ REST, bắt đầu tải lại từ đầu.", "WARN")
                offset = 0

        # Lấy kích thước tệp từ server qua lệnh SIZE
        total_remote_size = 0
        code, msg = self.send_cmd(f"SIZE {remote_path}")
        if code == 213:
            try:
                total_remote_size = int(msg.split()[1])
            except Exception:
                pass

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
                    if cancel_check and cancel_check():
                        self._log("Người dùng đã hủy quá trình tải tệp.", "WARN")
                        data_sock.close()
                        self.send_cmd("ABOR")
                        return False

                    chunk = data_sock.recv(8192)  # 8 KB Buffer
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_transferred += len(chunk)

                    now = time.time()
                    if now - last_update >= 0.1:  # Cập nhật tiến độ mỗi 100ms
                        elapsed = now - start_time
                        speed = (
                            (bytes_transferred - offset) / elapsed if elapsed > 0 else 0
                        )
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
        progress_callback: Optional[Callable[[int, int, float], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """
        Tải tệp từ máy cục bộ lên Server (STOR).
        Truyền theo khối nhị phân 8 KB và báo cáo tiến trình.
        """
        if not os.path.exists(local_path):
            raise FTPClientError(f"Tệp cục bộ không tồn tại: {local_path}")

        total_size = os.path.getsize(local_path)
        data_sock, accept_fn = self._open_data_connection()

        code, msg = self.send_cmd(f"STOR {remote_path}")
        if code not in (125, 150):
            data_sock.close()
            raise FTPClientError(f"Không thể upload tệp: {msg}")

        if accept_fn:
            data_sock = accept_fn()

        bytes_sent = 0
        start_time = time.time()
        last_update = start_time

        try:
            with open(local_path, "rb") as f:
                while True:
                    if cancel_check and cancel_check():
                        self._log("Người dùng đã hủy quá trình tải lên.", "WARN")
                        data_sock.close()
                        self.send_cmd("ABOR")
                        return False

                    chunk = f.read(8192)
                    if not chunk:
                        break
                    data_sock.sendall(chunk)
                    bytes_sent += len(chunk)

                    now = time.time()
                    if now - last_update >= 0.1:
                        elapsed = now - start_time
                        speed = bytes_sent / elapsed if elapsed > 0 else 0
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
            speed = bytes_sent / elapsed if elapsed > 0 else 0
            progress_callback(bytes_sent, total_size, speed)

        self._log(f"Tải tệp '{os.path.basename(local_path)}' lên server thành công!", "SUCCESS")
        return True

    def disconnect(self) -> None:
        """Ngắt kết nối an toàn khỏi FTP Server."""
        if self.is_connected:
            try:
                self.send_cmd("QUIT")
            except Exception:
                pass
            try:
                if self.control_socket:
                    self.control_socket.close()
            except Exception:
                pass
            self.control_socket = None
            self.is_connected = False
            self.is_logged_in = False
            self.tls_active = False
            self._log("Đã ngắt kết nối.", "INFO")

