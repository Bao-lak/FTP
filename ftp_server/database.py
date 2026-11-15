"""
Module Quản lý Cơ sở dữ liệu SQLite cho FTP Server.
Bao gồm quản lý người dùng, phân quyền, lưu nhật ký lệnh và lịch sử truyền file.
Tuân thủ thread-safe và sử dụng câu lệnh tham số hóa (?) để chống SQL Injection.
"""

import os
import sqlite3
import hashlib
import secrets
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple


class DatabaseManager:
    """Quản lý kết nối và thao tác với SQLite Database."""

    def __init__(self, db_path: str = "ftp_server.db"):
        self.db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Tạo kết nối SQLite mới, trả về dạng Dict row."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Khởi tạo các bảng cơ sở dữ liệu nếu chưa tồn tại."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 1. Bảng người dùng
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        home_dir TEXT NOT NULL,
                        can_read INTEGER DEFAULT 1,
                        can_write INTEGER DEFAULT 1,
                        can_delete INTEGER DEFAULT 1,
                        is_active INTEGER DEFAULT 1,
                        quota_bytes INTEGER DEFAULT 0,
                        max_connections INTEGER DEFAULT 5,
                        speed_limit_kb INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 2. Bảng phiên đăng nhập
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        ip_address TEXT,
                        connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        disconnected_at TIMESTAMP,
                        status TEXT DEFAULT 'ACTIVE'
                    )
                """)

                # 3. Bảng nhật ký lệnh FTP
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS command_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        ip_address TEXT,
                        command TEXT,
                        argument TEXT,
                        response_code INTEGER,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # 4. Bảng lịch sử truyền file
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS transfers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT,
                        filename TEXT,
                        direction TEXT,
                        file_size INTEGER,
                        bytes_transferred INTEGER,
                        status TEXT,
                        start_time TIMESTAMP,
                        end_time TIMESTAMP
                    )
                """)

                conn.commit()
            finally:
                conn.close()

    # ==================== BĂM VÀ XÁC THỰC MẬT KHẨU ====================

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Băm mật khẩu bằng PBKDF2-HMAC-SHA256 kết hợp Salt ngẫu nhiên.
        Định dạng lưu: salt_hex$hash_hex
        """
        salt = secrets.token_hex(16)
        pwd_hash = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
        ).hex()
        return f"{salt}${pwd_hash}"

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        """Kiểm tra mật khẩu nhập vào khớp với hash lưu trong database."""
        try:
            if "$" not in stored_hash:
                return False
            salt, original_hash = stored_hash.split("$", 1)
            pwd_hash = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
            ).hex()
            return secrets.compare_digest(pwd_hash, original_hash)
        except Exception:
            return False

    # ==================== QUẢN LÝ NGƯỜI DÙNG ====================

    def add_user(
        self,
        username: str,
        password: str,
        home_dir: str,
        can_read: int = 1,
        can_write: int = 1,
        can_delete: int = 1,
        is_active: int = 1,
        quota_bytes: int = 0,
        max_connections: int = 5,
        speed_limit_kb: int = 0,
    ) -> bool:
        """Thêm người dùng mới vào hệ thống."""
        pwd_hash = self.hash_password(password) if password else ""
        norm_home = os.path.abspath(home_dir)
        os.makedirs(norm_home, exist_ok=True)

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, home_dir, can_read, can_write, 
                        can_delete, is_active, quota_bytes, max_connections, speed_limit_kb
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        pwd_hash,
                        norm_home,
                        can_read,
                        can_write,
                        can_delete,
                        is_active,
                        quota_bytes,
                        max_connections,
                        speed_limit_kb,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
            finally:
                conn.close()

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Xác thực thông tin đăng nhập của user.
        Trả về dict thông tin user nếu hợp lệ, None nếu thất bại.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
                row = cursor.fetchone()
                if not row:
                    return None

                user_dict = dict(row)
                if not user_dict.get("is_active"):
                    return None

                # Tài khoản anonymous không yêu cầu mật khẩu
                if username.lower() == "anonymous":
                    return user_dict

                if self.verify_password(password, user_dict["password_hash"]):
                    return user_dict
                return None
            finally:
                conn.close()

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Lấy thông tin người dùng theo username."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
                row = cursor.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def list_users(self) -> List[Dict[str, Any]]:
        """Lấy danh sách tất cả người dùng."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users ORDER BY id ASC")
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def update_user(
        self,
        user_id: int,
        home_dir: str,
        can_read: int,
        can_write: int,
        can_delete: int,
        is_active: int,
        quota_bytes: int,
        max_connections: int,
        speed_limit_kb: int,
        new_password: Optional[str] = None,
    ) -> bool:
        """Cập nhật thông tin và quyền hạn người dùng."""
        norm_home = os.path.abspath(home_dir)
        os.makedirs(norm_home, exist_ok=True)

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if new_password:
                    new_hash = self.hash_password(new_password)
                    cursor.execute(
                        """
                        UPDATE users SET
                            home_dir = ?, can_read = ?, can_write = ?, can_delete = ?,
                            is_active = ?, quota_bytes = ?, max_connections = ?, 
                            speed_limit_kb = ?, password_hash = ?
                        WHERE id = ?
                        """,
                        (
                            norm_home,
                            can_read,
                            can_write,
                            can_delete,
                            is_active,
                            quota_bytes,
                            max_connections,
                            speed_limit_kb,
                            new_hash,
                            user_id,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE users SET
                            home_dir = ?, can_read = ?, can_write = ?, can_delete = ?,
                            is_active = ?, quota_bytes = ?, max_connections = ?, 
                            speed_limit_kb = ?
                        WHERE id = ?
                        """,
                        (
                            norm_home,
                            can_read,
                            can_write,
                            can_delete,
                            is_active,
                            quota_bytes,
                            max_connections,
                            speed_limit_kb,
                            user_id,
                        ),
                    )
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def delete_user(self, user_id: int) -> bool:
        """Xóa người dùng khỏi database."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def update_password(self, user_id: int, new_password: str) -> bool:
        """Đổi mật khẩu cho người dùng."""
        new_hash = self.hash_password(new_password)
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id)
                )
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    # ==================== PHIÊN & NHẬT KÝ ====================

    def log_session_start(self, username: str, ip_address: str) -> int:
        """Ghi nhận phiên kết nối mới và trả về ID phiên."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO sessions (username, ip_address, status) VALUES (?, ?, 'ACTIVE')",
                    (username, ip_address),
                )
                conn.commit()
                return cursor.lastrowid or 0
            finally:
                conn.close()

    def log_session_end(self, session_id: int, status: str = "CLOSED") -> None:
        """Cập nhật thời gian kết thúc phiên kết nối."""
        if not session_id:
            return
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE sessions 
                    SET disconnected_at = CURRENT_TIMESTAMP, status = ?
                    WHERE id = ?
                    """,
                    (status, session_id),
                )
                conn.commit()
            finally:
                conn.close()

    def log_command(
        self, username: str, ip_address: str, command: str, argument: str, response_code: int
    ) -> None:
        """Ghi nhật ký một lệnh FTP vừa thực thi."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO command_logs (username, ip_address, command, argument, response_code)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (username, ip_address, command, argument, response_code),
                )
                conn.commit()
            finally:
                conn.close()

    def log_transfer(
        self,
        username: str,
        filename: str,
        direction: str,
        file_size: int,
        bytes_transferred: int,
        status: str,
        start_time: str,
        end_time: str,
    ) -> None:
        """Ghi nhật ký một lần truyền file (upload/download)."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO transfers (
                        username, filename, direction, file_size, 
                        bytes_transferred, status, start_time, end_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        username,
                        filename,
                        direction,
                        file_size,
                        bytes_transferred,
                        status,
                        start_time,
                        end_time,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_recent_transfers(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Lấy danh sách các lần truyền file gần đây nhất."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, username, filename, direction, file_size, 
                           bytes_transferred, status, start_time, end_time
                    FROM transfers ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    def get_transfer_statistics(self) -> List[Dict[str, Any]]:
        """Tổng hợp dung lượng và số lần upload/download theo từng user."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT 
                        username,
                        SUM(CASE WHEN direction = 'UPLOAD' AND status = 'SUCCESS' THEN bytes_transferred ELSE 0 END) as total_upload_bytes,
                        SUM(CASE WHEN direction = 'DOWNLOAD' AND status = 'SUCCESS' THEN bytes_transferred ELSE 0 END) as total_download_bytes,
                        COUNT(CASE WHEN direction = 'UPLOAD' THEN 1 END) as upload_count,
                        COUNT(CASE WHEN direction = 'DOWNLOAD' THEN 1 END) as download_count
                    FROM transfers
                    GROUP BY username
                    """
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

