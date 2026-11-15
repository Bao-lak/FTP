"""
Module Quản lý Phiên làm việc (Session) cho FTP Client.
Lưu trữ trạng thái đăng nhập, thư mục hiện tại, quyền hạn, cơ chế bảo mật
chống tấn công Directory Traversal ("../"), kiểm tra dung lượng (quota)
và giới hạn tốc độ truyền (speed throttle).
"""

import os
import time
import logging
from typing import Optional, Dict, Any, Tuple
from .data_connection import DataConnectionManager

logger = logging.getLogger("FTPServer.Session")


class FTPSession:
    """Đại diện cho một phiên kết nối FTP của client."""

    def __init__(
        self,
        client_socket,
        client_address: Tuple[str, int],
        db_manager,
        pasv_ip: str = "127.0.0.1",
        pasv_ports: Tuple[int, int] = (50000, 50100),
    ):
        self.client_socket = client_socket
        self.client_address = client_address
        self.db = db_manager

        # Trạng thái người dùng
        self.session_id: int = 0
        self.user: Optional[Dict[str, Any]] = None
        self.is_authenticated: bool = False
        self.user_pending: Optional[str] = None  # Tạm lưu username sau lệnh USER

        # Thư mục gốc trên đĩa và thư mục ảo hiện tại (luôn bắt đầu bằng '/')
        self.home_dir: str = ""
        self.current_directory: str = "/"  # Virtual path

        # Cấu hình truyền file
        self.transfer_type: str = "I"  # 'A' = ASCII, 'I' = Binary (Mặc định nhị phân)
        self.rest_offset: int = 0      # Vị trí offset cho lệnh REST (tiếp tục tải)
        self.rnfr_path: Optional[str] = None  # Lưu đường dẫn nguồn của lệnh RNFR

        # Quản lý kết nối dữ liệu (Data Connection)
        self.data_manager = DataConnectionManager(
            pasv_ip=pasv_ip, min_port=pasv_ports[0], max_port=pasv_ports[1]
        )

        # Bảo mật FTPS / TLS
        self.tls_enabled: bool = False
        self.prot_p: bool = False

        # Thống kê lưu lượng
        self.bytes_sent: int = 0
        self.bytes_received: int = 0
        self.connected_at: float = time.time()

    @property
    def username(self) -> str:
        """Trả về tên đăng nhập của phiên hoặc 'Anonymous' / 'Unauthenticated'."""
        if self.user:
            return self.user.get("username", "Unknown")
        return self.user_pending or "Chưa đăng nhập"

    # ==================== BẢO MẬT ĐƯỜNG DẪN (CHỐNG PATH TRAVERSAL) ====================

    def resolve_path(self, user_input_path: str) -> Tuple[str, str]:
        """
        Ánh xạ đường dẫn ảo mà Client yêu cầu sang đường dẫn thực tế trên đĩa cứng (Chroot Jail).
        Bảo vệ nghiêm ngặt chống tấn công Directory Traversal ("../" hoặc symlink).
        
        Quy trình xử lý 2 lớp:
        1. Lớp ảo (Virtual Path):
           - Chuẩn hóa đường dẫn bằng posixpath (coi thư mục gốc '/' là home_dir của user).
           - Loại bỏ hoàn toàn khả năng lùi vượt quá thư mục gốc ảo '/'.
        2. Lớp đĩa cứng (Real Disk Path):
           - Ghép đường dẫn ảo vào self.home_dir trên ổ đĩa.
           - Sử dụng os.path.realpath() để giải quyết các liên kết (symlinks) và "..".
           - Kiểm tra tiền tố (startswith): Nếu đường dẫn sau khi giải quyết không nằm trong
             hoặc bắt đầu bằng realpath của home_dir, ném ngoại lệ PermissionError ngay lập tức!
             
        Trả về Tuple: (real_disk_path, virtual_display_path)
        """
        if not self.home_dir:
            raise PermissionError("Chưa đăng nhập, không có quyền truy cập thư mục!")

        # 1. Chuẩn hóa đường dẫn ảo
        path_str = user_input_path.strip()
        if not path_str:
            target_vpath = self.current_directory
        elif path_str.startswith("/"):
            # Đường dẫn tuyệt đối ảo (tính từ gốc của user)
            target_vpath = os.path.normpath(path_str)
        else:
            # Đường dẫn tương đối từ thư mục hiện tại
            target_vpath = os.path.normpath(os.path.join(self.current_directory, path_str))

        # Đảm bảo đường dẫn ảo luôn bắt đầu bằng '/' và không cho phép thoát khỏi '/'
        parts = [p for p in target_vpath.split("/") if p and p != "."]
        safe_parts = []
        for part in parts:
            if part == "..":
                if safe_parts:
                    safe_parts.pop()
            else:
                safe_parts.append(part)

        normalized_vpath = "/" + "/".join(safe_parts)

        # 2. Ánh xạ sang đường dẫn thực tế trên ổ đĩa
        rel_to_home = normalized_vpath.lstrip("/")
        real_home = os.path.realpath(self.home_dir)
        candidate_real_path = os.path.realpath(os.path.join(real_home, rel_to_home))

        # 3. KIỂM TRA CHẶN PATH TRAVERSAL:
        # Đường dẫn thực tế bắt buộc phải trùng hoặc nằm bên trong real_home
        if not (candidate_real_path == real_home or candidate_real_path.startswith(real_home + os.sep)):
            logger.warning(
                f"[CẢNH BÁO BẢO MẬT] Phát hiện tấn công Path Traversal từ {self.client_address}: "
                f"Input='{user_input_path}', Candidate='{candidate_real_path}', Home='{real_home}'"
            )
            raise PermissionError("Truy cập bị từ chối: Phát hiện nỗ lực thoát khỏi thư mục gốc!")

        return candidate_real_path, normalized_vpath

    # ==================== KIỂM TRA QUYỀN HẠN (RBAC) ====================

    def check_permission(self, perm_type: str) -> bool:
        """
        Kiểm tra quyền hạn người dùng trước khi thực thi thao tác:
        - perm_type = 'READ': RETR, LIST, NLST, SIZE
        - perm_type = 'WRITE': STOR, APPE, MKD
        - perm_type = 'DELETE': DELE, RMD
        """
        if not self.is_authenticated or not self.user:
            return False

        if perm_type == "READ":
            return bool(self.user.get("can_read", 1))
        elif perm_type == "WRITE":
            return bool(self.user.get("can_write", 1))
        elif perm_type == "DELETE":
            return bool(self.user.get("can_delete", 1))
        return False

    # ==================== QUẢN LÝ DUNG LƯỢNG (QUOTA) ====================

    def get_used_storage(self) -> int:
        """Tính tổng số byte dữ liệu hiện có trong thư mục của user."""
        if not self.home_dir or not os.path.exists(self.home_dir):
            return 0
        total_size = 0
        for dirpath, _, filenames in os.walk(self.home_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        return total_size

    def check_quota(self, incoming_bytes: int = 0) -> bool:
        """
        Kiểm tra hạn mức dung lượng:
        Nếu quota_bytes > 0 và tổng dung lượng vượt quá quota -> Trả về False.
        """
        if not self.user:
            return True
        quota = self.user.get("quota_bytes", 0)
        if quota <= 0:
            return True  # 0 nghĩa là không giới hạn dung lượng

        used = self.get_used_storage()
        return (used + incoming_bytes) <= quota

    # ==================== GIỚI HẠN TỐC ĐỘ (SPEED THROTTLE) ====================

    def throttle(self, bytes_transferred: int, start_time: float) -> None:
        """
        Điều tiết tốc độ truyền dữ liệu nếu user có cấu hình speed_limit_kb > 0.
        Tính toán độ trễ cần thiết để duy trì tốc độ mong muốn.
        """
        if not self.user:
            return
        speed_limit_kb = self.user.get("speed_limit_kb", 0)
        if speed_limit_kb <= 0:
            return

        target_bytes_per_sec = speed_limit_kb * 1024
        elapsed = time.time() - start_time
        if elapsed <= 0:
            return

        expected_time = bytes_transferred / target_bytes_per_sec
        if expected_time > elapsed:
            sleep_time = expected_time - elapsed
            # Chỉ ngủ nếu độ trễ đáng kể để không làm chậm CPU không cần thiết
            if sleep_time > 0.005:
                time.sleep(min(sleep_time, 1.0))

    def close(self) -> None:
        """Đóng phiên làm việc và giải phóng tài nguyên."""
        self.data_manager.close()
        if self.session_id:
            try:
                self.db.log_session_end(self.session_id, "CLOSED")
            except Exception:
                pass
        try:
            self.client_socket.close()
        except Exception:
            pass

