"""
Module Quản lý Kết nối Dữ liệu (Data Connection) cho FTP Server.
Hỗ trợ cả 2 chế độ:
1. Passive Mode (PASV) - Client kết nối tới Server.
2. Active Mode (PORT) - Server chủ động kết nối tới Client.
Hỗ trợ mã hóa SSL/TLS (FTPS) khi client kích hoạt lệnh PROT P.
"""

import socket
import ssl
import random
import logging
from typing import Optional, Tuple

logger = logging.getLogger("FTPServer.DataConnection")


class DataConnectionManager:
    """
    Quản lý việc thiết lập và mở kết nối dữ liệu (Data Socket) cho mỗi phiên FTP.
    Mỗi lệnh truyền dữ liệu (LIST, RETR, STOR, ...) sẽ mở một socket dữ liệu mới
    và đóng lại sau khi truyền xong theo đúng RFC 959.
    """

    def __init__(self, pasv_ip: str = "127.0.0.1", min_port: int = 50000, max_port: int = 50100):
        self.pasv_ip = pasv_ip
        self.min_port = min_port
        self.max_port = max_port

        # Trạng thái Passive Mode (PASV)
        self.passive_listener: Optional[socket.socket] = None
        self.passive_port: Optional[int] = None

        # Trạng thái Active Mode (PORT)
        self.active_host: Optional[str] = None
        self.active_port: Optional[int] = None

        # Chế độ hiện tại: 'PASV' hoặc 'PORT'
        self.mode: Optional[str] = None

        # Cấu hình SSL nếu dùng FTPS
        self.ssl_context: Optional[ssl.SSLContext] = None
        self.prot_p: bool = False  # True nếu dữ liệu cần mã hóa TLS

    def set_ssl(self, ssl_context: Optional[ssl.SSLContext], prot_p: bool) -> None:
        """Cấu hình bảo mật TLS cho kênh dữ liệu."""
        self.ssl_context = ssl_context
        self.prot_p = prot_p

    def setup_passive(self, server_ip: Optional[str] = None) -> Tuple[str, int, int]:
        """
        Khởi tạo Passive Mode (lệnh PASV):
        Server mở một cổng ngẫu nhiên trong dải [min_port, max_port] và lắng nghe.
        Trả về (ip_str, p1, p2) để phản hồi mã 227 cho client.
        Công thức tính cổng: Port = p1 * 256 + p2.
        """
        self.close()  # Đóng các kết nối dữ liệu cũ nếu còn sót
        ip_to_use = server_ip or self.pasv_ip

        # Nếu server_ip là 0.0.0.0, dùng loopback hoặc IP mạng nội bộ thích hợp
        if ip_to_use in ("0.0.0.0", ""):
            ip_to_use = "127.0.0.1"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Thử tìm một cổng trống ngẫu nhiên trong dải cho phép
        ports = list(range(self.min_port, self.max_port + 1))
        random.shuffle(ports)

        bound = False
        for p in ports:
            try:
                sock.bind(("0.0.0.0", p))
                self.passive_port = p
                bound = True
                break
            except OSError:
                continue

        if not bound:
            # Nếu toàn bộ dải cổng cấu hình bị bận, để hệ điều hành cấp phát cổng bất kỳ
            sock.bind(("0.0.0.0", 0))
            self.passive_port = sock.getsockname()[1]

        sock.listen(1)
        sock.settimeout(20.0)  # Chờ client kết nối trong tối đa 20 giây
        self.passive_listener = sock
        self.mode = "PASV"

        p1 = self.passive_port // 256
        p2 = self.passive_port % 256

        logger.debug(f"Đã mở cổng PASV {self.passive_port} (p1={p1}, p2={p2}) trên IP {ip_to_use}")
        return ip_to_use, p1, p2

    def setup_active(self, host: str, port: int) -> None:
        """
        Khởi tạo Active Mode (lệnh PORT):
        Client gửi cho Server thông tin IP và Cổng mà Client đang lắng nghe.
        Server sẽ lưu lại để chủ động kết nối tới khi cần truyền dữ liệu.
        """
        self.close()
        self.active_host = host
        self.active_port = port
        self.mode = "PORT"
        logger.debug(f"Đã thiết lập Active Mode (PORT) tới {host}:{port}")

    def open_data_socket(self) -> Optional[socket.socket]:
        """
        Thực hiện mở và trả về socket dữ liệu để truyền file hoặc danh sách thư mục.
        - Trong PASV: Gọi accept() trên socket đang lắng nghe.
        - Trong PORT: Gọi connect() tới địa chỉ client đã cung cấp.
        Nếu PROT P được bật, socket sẽ được nâng cấp mã hóa qua SSL/TLS.
        """
        data_sock = None
        try:
            if self.mode == "PASV":
                if not self.passive_listener:
                    logger.error("Không có socket lắng nghe PASV!")
                    return None
                logger.debug("Đang chờ kết nối từ Client tới cổng PASV...")
                data_sock, client_addr = self.passive_listener.accept()
                logger.debug(f"Client đã kết nối dữ liệu PASV từ {client_addr}")
                # Đóng listener ngay sau khi accept để giải phóng cổng
                try:
                    self.passive_listener.close()
                except Exception:
                    pass
                self.passive_listener = None

            elif self.mode == "PORT":
                if not self.active_host or not self.active_port:
                    logger.error("Chưa nhận được thông tin PORT từ client!")
                    return None
                logger.debug(f"Server đang chủ động kết nối tới Client tại {self.active_host}:{self.active_port}...")
                data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                data_sock.settimeout(15.0)
                data_sock.connect((self.active_host, self.active_port))
                logger.debug("Kết nối dữ liệu Active (PORT) thành công!")
            else:
                logger.error("Chưa chọn chế độ truyền dữ liệu (PASV hoặc PORT)!")
                return None

            # Thiết lập timeout cho socket dữ liệu
            if data_sock:
                data_sock.settimeout(30.0)

                # Nâng cấp lên SSL/TLS nếu có yêu cầu PROT P
                if self.prot_p and self.ssl_context:
                    logger.debug("Đang nâng cấp socket dữ liệu lên TLS (PROT P)...")
                    data_sock = self.ssl_context.wrap_socket(data_sock, server_side=True)

            return data_sock

        except Exception as e:
            logger.error(f"Lỗi khi mở socket dữ liệu: {e}")
            if data_sock:
                try:
                    data_sock.close()
                except Exception:
                    pass
            return None
        finally:
            # Đảm bảo reset trạng thái sau khi đã dùng xong hoặc lỗi
            self.mode = None

    def close(self) -> None:
        """Đóng an toàn mọi socket đang mở và reset trạng thái."""
        if self.passive_listener:
            try:
                self.passive_listener.close()
            except Exception:
                pass
            self.passive_listener = None

        self.passive_port = None
        self.active_host = None
        self.active_port = None
        self.mode = None

