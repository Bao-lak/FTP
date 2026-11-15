"""
Module Lõi FTP Server (FTPServer).
Lắng nghe kết nối TCP trên cổng điều khiển (mặc định 2121).
Mỗi client kết nối được phân bổ một luồng riêng biệt (threading) để xử lý.
Hỗ trợ quản lý phiên, giới hạn số kết nối tối đa cho mỗi user, ngắt kết nối cưỡng bức,
ghi nhật ký ra file ftp_server.log và gửi sự kiện đến giao diện Tkinter qua Queue.
"""

import os
import sys
import socket
import ssl
import logging
import threading
import subprocess
from typing import Optional, Dict, Tuple, Callable
from queue import Queue

from .database import DatabaseManager
from .session import FTPSession
from .command_handler import FTPCommandHandler

# Cấu hình logging ra cả console và file ftp_server.log
logger = logging.getLogger("FTPServer")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    # File handler
    file_handler = logging.FileHandler("ftp_server.log", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)


class FTPServer:
    """Máy chủ FTP đa luồng tuân thủ RFC 959 hoàn toàn bằng socket."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 2121,
        root_dir: str = "./ftp_root",
        db_path: str = "ftp_server.db",
        pasv_ports: Tuple[int, int] = (50000, 50100),
        allow_anonymous: bool = True,
        enable_tls: bool = False,
        event_queue: Optional[Queue] = None,
    ):
        self.host = host
        self.port = port
        self.root_dir = os.path.abspath(root_dir)
        self.pasv_ports = pasv_ports
        self.allow_anonymous = allow_anonymous
        self.enable_tls = enable_tls
        self.event_queue = event_queue

        # Quản lý Database SQLite
        self.db = DatabaseManager(db_path)

        # Quản lý danh sách client đang kết nối: {client_id: FTPSession}
        self.active_sessions: Dict[int, FTPSession] = {}
        self.sessions_lock = threading.Lock()
        self._next_client_id = 1

        # Trạng thái server
        self.is_running = False
        self.server_socket: Optional[socket.socket] = None
        self.server_thread: Optional[threading.Thread] = None

        # Khởi tạo SSLContext nếu có bật TLS
        self.ssl_context: Optional[ssl.SSLContext] = None
        if self.enable_tls:
            self._setup_tls()

    def _setup_tls(self) -> None:
        """Tạo hoặc nạp chứng chỉ SSL/TLS tự ký cho tính năng FTPS."""
        cert_file = "server.crt"
        key_file = "server.key"

        # Nếu chưa có chứng chỉ, tự động tạo bằng OpenSSL
        if not (os.path.exists(cert_file) and os.path.exists(key_file)):
            logger.info("Đang tạo chứng chỉ SSL tự ký cho FTPS...")
            try:
                cmd = [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    key_file,
                    "-out",
                    cert_file,
                    "-days",
                    "365",
                    "-nodes",
                    "-subj",
                    "/CN=FTP-Server/O=ComputerNetworks/C=VN",
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logger.info("Đã tạo chứng chỉ server.crt và server.key thành công!")
            except Exception as e:
                logger.error(f"Không thể tạo chứng chỉ SSL: {e}. Tắt chế độ TLS.")
                self.ssl_context = None
                return

        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
            self.ssl_context = ctx
            logger.info("Đã cấu hình chứng chỉ SSL/TLS cho FTPS sẵn sàng.")
        except Exception as e:
            logger.error(f"Lỗi nạp chứng chỉ SSL: {e}")
            self.ssl_context = None

    def emit_event(self, event_type: str, data: dict) -> None:
        """Gửi sự kiện thông báo về UI thread thông qua queue.Queue."""
        if self.event_queue:
            try:
                self.event_queue.put({"type": event_type, "data": data})
            except Exception:
                pass

    def start(self) -> bool:
        """Khởi động FTP Server trên một luồng nền."""
        if self.is_running:
            return True

        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(50)
            self.is_running = True

            logger.info(f"FTP Server đã khởi động thành công tại {self.host}:{self.port}")
            self.emit_event(
                "SERVER_STARTED",
                {"host": self.host, "port": self.port, "root_dir": self.root_dir},
            )

            # Chạy luồng lắng nghe kết nối
            self.server_thread = threading.Thread(
                target=self._listen_loop, name="ServerListenerThread", daemon=True
            )
            self.server_thread.start()
            return True
        except OSError as e:
            logger.error(f"Không thể mở cổng {self.port}: {e}")
            self.emit_event("SERVER_ERROR", {"error": f"Cổng {self.port} bị chiếm hoặc lỗi: {e}"})
            self.stop()
            return False

    def _listen_loop(self) -> None:
        """Vòng lặp chấp nhận (accept) các kết nối mới từ client."""
        while self.is_running and self.server_socket:
            try:
                client_sock, client_addr = self.server_socket.accept()
                with self.sessions_lock:
                    client_id = self._next_client_id
                    self._next_client_id += 1

                logger.info(f"Client mới kết nối: {client_addr} (ID: {client_id})")

                # Tạo luồng xử lý riêng cho từng client
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, client_addr, client_id),
                    name=f"ClientThread-{client_id}",
                    daemon=True,
                )
                client_thread.start()
            except OSError:
                break
            except Exception as e:
                if self.is_running:
                    logger.error(f"Lỗi trong vòng lặp accept: {e}")

    def _handle_client(
        self, client_sock: socket.socket, client_addr: Tuple[str, int], client_id: int
    ) -> None:
        """Xử lý toàn bộ vòng đời của một client kết nối."""
        session = FTPSession(
            client_socket=client_sock,
            client_address=client_addr,
            db_manager=self.db,
            pasv_ip=self.host if self.host != "0.0.0.0" else "127.0.0.1",
            pasv_ports=self.pasv_ports,
        )

        with self.sessions_lock:
            self.active_sessions[client_id] = session

        self.emit_event(
            "CLIENT_CONNECTED",
            {
                "client_id": client_id,
                "ip": client_addr[0],
                "port": client_addr[1],
                "user": "Chưa đăng nhập",
            },
        )

        def event_callback(event_type: str, data: dict):
            # Callback từ CommandHandler
            if event_type == "CLIENT_LOGGED_IN":
                # Kiểm tra số lượng kết nối tối đa cho user này
                username = session.username
                max_conn = session.user.get("max_connections", 5) if session.user else 5
                active_count = sum(
                    1
                    for s in self.active_sessions.values()
                    if s.is_authenticated and s.username == username
                )
                if active_count > max_conn:
                    logger.warning(
                        f"User '{username}' vượt quá số kết nối tối đa ({active_count} > {max_conn})!"
                    )
                    handler.send_response(421, f"Qua so luong ket noi cho phep ({max_conn}).")
                    handler.running = False
                    return

                self.emit_event(
                    "CLIENT_LOGGED_IN",
                    {
                        "client_id": client_id,
                        "user": session.username,
                        "home_dir": session.home_dir,
                        "vpath": session.current_directory,
                    },
                )
            elif event_type == "COMMAND_RECEIVED":
                data["client_id"] = client_id
                self.emit_event("COMMAND_RECEIVED", data)

        handler = FTPCommandHandler(
            session=session,
            server_event_callback=event_callback,
            allow_anonymous=self.allow_anonymous,
            ssl_context=self.ssl_context,
        )

        # Gửi thông điệp chào mừng chuẩn mã 220
        handler.send_response(220, "(Custom FTP Server) San sang phuc vu - RFC 959 Compliant.")

        recv_buffer = ""
        try:
            while self.is_running and handler.running:
                # Đọc dữ liệu từ kênh điều khiển
                data = session.client_socket.recv(4096)
                if not data:
                    break  # Client đóng kết nối

                recv_buffer += data.decode("utf-8", errors="replace")

                # Xử lý các lệnh kết thúc bằng \r\n hoặc \n
                while "\n" in recv_buffer:
                    line, recv_buffer = recv_buffer.split("\n", 1)
                    line = line.strip("\r")
                    if line:
                        handler.handle_command(line)

        except (ConnectionResetError, BrokenPipeError):
            logger.info(f"Client {client_addr} ngắt kết nối đột ngột.")
        except Exception as e:
            logger.error(f"Lỗi ngoại lệ trong phiên client {client_addr}: {e}")
        finally:
            logger.info(f"Đóng kết nối client ID: {client_id} ({client_addr})")
            session.close()
            with self.sessions_lock:
                self.active_sessions.pop(client_id, None)

            self.emit_event("CLIENT_DISCONNECTED", {"client_id": client_id, "ip": client_addr[0]})

    def disconnect_client(self, client_id: int) -> bool:
        """Cưỡng bức ngắt kết nối một client cụ thể (quản trị viên click Disconnect)."""
        with self.sessions_lock:
            session = self.active_sessions.get(client_id)
            if session:
                try:
                    session.client_socket.sendall(
                        b"421 Quan tri vien da ngat ket noi cua ban.\r\n"
                    )
                except Exception:
                    pass
                session.close()
                return True
        return False

    def stop(self) -> None:
        """Dừng hoàn toàn FTP Server và đóng tất cả các kết nối hiện hành."""
        if not self.is_running:
            return

        logger.info("Đang dừng FTP Server...")
        self.is_running = False

        # Đóng tất cả phiên client
        with self.sessions_lock:
            for session in list(self.active_sessions.values()):
                try:
                    session.client_socket.sendall(b"421 Server dang bao tri hoac dung.\r\n")
                except Exception:
                    pass
                session.close()
            self.active_sessions.clear()

        # Đóng socket server
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None

        logger.info("FTP Server đã dừng.")
        self.emit_event("SERVER_STOPPED", {})

