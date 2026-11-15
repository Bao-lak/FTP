"""
Module Quản lý Cơ sở dữ liệu SQLite cho FTP Client (Site Manager & Recent Connections).
Lưu trữ danh sách các Server đã lưu (Site Manager) và lịch sử các kết nối gần đây.
"""

import os
import sqlite3
import threading
from typing import Optional, List, Dict, Any


class ClientDatabaseManager:
    """Quản lý kết nối và thao tác với SQLite Database phía Client (ftp_client.db)."""

    def __init__(self, db_path: str = "ftp_client.db"):
        self.db_path = os.path.abspath(db_path)
        self._lock = threading.Lock()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Tạo kết nối SQLite mới, trả về dạng Dict row."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Khởi tạo bảng sites và recent_connections nếu chưa có."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()

                # Bảng sites (Site Manager giống FileZilla)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sites (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        host TEXT NOT NULL,
                        port INTEGER DEFAULT 2121,
                        username TEXT,
                        password TEXT,
                        mode TEXT DEFAULT 'PASV',
                        use_tls INTEGER DEFAULT 0,
                        notes TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Bảng recent_connections (Lịch sử kết nối và thư mục remote cuối cùng)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS recent_connections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        site_id INTEGER,
                        host TEXT NOT NULL,
                        port INTEGER DEFAULT 2121,
                        username TEXT,
                        mode TEXT DEFAULT 'PASV',
                        use_tls INTEGER DEFAULT 0,
                        last_remote_dir TEXT DEFAULT '/',
                        connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
                    )
                """)

                conn.commit()
            finally:
                conn.close()

    # ==================== QUẢN LÝ SITES (SITE MANAGER) ====================

    def add_site(
        self,
        name: str,
        host: str,
        port: int = 2121,
        username: str = "",
        password: str = "",
        mode: str = "PASV",
        use_tls: int = 0,
        notes: str = "",
    ) -> int:
        """Thêm một site mới vào Site Manager."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO sites (name, host, port, username, password, mode, use_tls, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, host, port, username, password, mode, use_tls, notes),
                )
                conn.commit()
                return cursor.lastrowid or 0
            finally:
                conn.close()

    def update_site(
        self,
        site_id: int,
        name: str,
        host: str,
        port: int,
        username: str,
        password: str,
        mode: str,
        use_tls: int,
        notes: str,
    ) -> bool:
        """Cập nhật thông tin site đã lưu."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE sites SET
                        name = ?, host = ?, port = ?, username = ?,
                        password = ?, mode = ?, use_tls = ?, notes = ?
                    WHERE id = ?
                    """,
                    (name, host, port, username, password, mode, use_tls, notes, site_id),
                )
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def delete_site(self, site_id: int) -> bool:
        """Xóa site khỏi danh sách đã lưu."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sites WHERE id = ?", (site_id,))
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def list_sites(self) -> List[Dict[str, Any]]:
        """Lấy danh sách tất cả các sites đã lưu."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sites ORDER BY name ASC")
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()

    get_sites = list_sites

    def get_site(self, site_id: int) -> Optional[Dict[str, Any]]:
        """Lấy thông tin chi tiết của một site theo ID."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    # ==================== LỊCH SỬ KẾT NỐI (RECENT CONNECTIONS) ====================

    def record_connection(
        self,
        host: str,
        port: int,
        username: str,
        last_remote_dir: str = "/",
        site_id: Optional[int] = None,
        mode: str = "PASV",
        use_tls: int = 0,
    ) -> None:
        """Ghi nhận một kết nối mới hoặc cập nhật thời gian kết nối gần đây."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # Kiểm tra xem host + port + username đã có chưa
                cursor.execute(
                    """
                    SELECT id FROM recent_connections 
                    WHERE host = ? AND port = ? AND username = ?
                    """,
                    (host, port, username),
                )
                existing = cursor.fetchone()
                if existing:
                    cursor.execute(
                        """
                        UPDATE recent_connections 
                        SET connected_at = CURRENT_TIMESTAMP, last_remote_dir = ?, site_id = ?, mode = ?, use_tls = ?
                        WHERE id = ?
                        """,
                        (last_remote_dir, site_id, mode, use_tls, existing["id"]),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO recent_connections (site_id, host, port, username, mode, use_tls, last_remote_dir)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (site_id, host, port, username, mode, use_tls, last_remote_dir),
                    )
                conn.commit()
            except Exception:
                # Không bao giờ để lỗi database làm gián đoạn luồng kết nối
                pass
            finally:
                conn.close()

    def record_recent_connection(
        self,
        site_id: Optional[int] = None,
        host: str = "127.0.0.1",
        port: int = 2121,
        username: str = "admin",
        mode: str = "PASV",
        use_tls: int = 0,
        last_remote_dir: str = "/",
    ) -> None:
        """Alias cho record_connection tương thích nhiều dạng tham số."""
        self.record_connection(
            host=host,
            port=port,
            username=username,
            last_remote_dir=last_remote_dir,
            site_id=site_id,
            mode=mode,
            use_tls=use_tls,
        )

    def update_last_remote_dir(self, host: str, port: int, username: str, remote_dir: str) -> None:
        """Cập nhật thư mục remote cuối cùng khi người dùng duyệt cây thư mục."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE recent_connections
                    SET last_remote_dir = ?
                    WHERE host = ? AND port = ? AND username = ?
                    """,
                    (remote_dir, host, port, username),
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

    def get_recent_connections(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Lấy danh sách các kết nối gần đây nhất."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM recent_connections 
                    ORDER BY connected_at DESC LIMIT ?
                    """,
                    (limit,),
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
