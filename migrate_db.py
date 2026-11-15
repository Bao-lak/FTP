"""
Kịch bản nâng cấp và đồng bộ cơ sở dữ liệu (Database Migration Script).
Chức năng:
1. Nâng cấp `ftp_server.db`:
   - Đảm bảo đầy đủ các bảng: users, sessions, command_logs, transfers.
   - Bổ sung cột `quota_bytes` vào bảng users nếu chưa có.
   - Thêm tài khoản mẫu (admin, bao, anonymous) nếu bảng trống.
2. Nâng cấp `ftp_client.db`:
   - Đảm bảo đầy đủ các bảng: sites, recent_connections.
   - Thêm cấu hình site mặc định "Localhost Test Server" nếu bảng trống.
"""

import os
import sqlite3
import hashlib
from typing import Optional


def hash_password(password: str) -> str:
    """Tạo chuỗi băm SHA-256 kèm salt cho mật khẩu."""
    salt = "ftp_secure_salt_2024"
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def migrate_server_db(db_path: str = "ftp_server.db") -> None:
    """Đồng bộ schema cho ftp_server.db."""
    print(f"\n[SERVER DB] Kiểm tra và đồng bộ: {os.path.abspath(db_path)}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Bảng users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            home_dir TEXT NOT NULL,
            can_read INTEGER DEFAULT 1,
            can_write INTEGER DEFAULT 1,
            can_delete INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            quota_bytes INTEGER DEFAULT 104857600
        );
    """)

    # Kiểm tra cột quota_bytes
    cur.execute("PRAGMA table_info(users);")
    columns = [row[1] for row in cur.fetchall()]
    if "quota_bytes" not in columns:
        print(" -> Thêm cột 'quota_bytes' (100 MB mặc định) vào bảng users...")
        cur.execute("ALTER TABLE users ADD COLUMN quota_bytes INTEGER DEFAULT 104857600;")
    else:
        print(" -> Cột 'quota_bytes' đã tồn tại trong bảng users.")

    # 2. Bảng sessions
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            logout_time TIMESTAMP,
            status TEXT DEFAULT 'ACTIVE'
        );
    """)

    # 3. Bảng command_logs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS command_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            command TEXT NOT NULL,
            arguments TEXT,
            response_code INTEGER,
            response_msg TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 4. Bảng transfers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            filename TEXT NOT NULL,
            direction TEXT NOT NULL,
            size_bytes INTEGER DEFAULT 0,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT DEFAULT 'IN_PROGRESS'
        );
    """)

    # Nạp người dùng mặc định nếu chưa có
    cur.execute("SELECT COUNT(*) FROM users;")
    count = cur.fetchone()[0]
    if count == 0:
        print(" -> Khởi tạo người dùng mặc định: admin, bao, anonymous...")
        home_root = os.path.abspath("ftp_root")
        os.makedirs(home_root, exist_ok=True)

        users_init = [
            ("admin", hash_password("123456"), home_root, 1, 1, 1, 1, 524288000),  # 500 MB
            ("bao", hash_password("123456"), home_root, 1, 1, 1, 1, 104857600),   # 100 MB
            ("anonymous", hash_password(""), home_root, 1, 0, 0, 1, 52428800),     # 50 MB
        ]
        cur.executemany("""
            INSERT INTO users (username, password_hash, home_dir, can_read, can_write, can_delete, is_active, quota_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, users_init)

    conn.commit()
    conn.close()
    print(" -> Hoàn tất nâng cấp ftp_server.db thành công!")


def migrate_client_db(db_path: str = "ftp_client.db") -> None:
    """Đồng bộ schema cho ftp_client.db."""
    print(f"\n[CLIENT DB] Kiểm tra và đồng bộ: {os.path.abspath(db_path)}...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Bảng sites
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 2121,
            username TEXT DEFAULT 'anonymous',
            password TEXT DEFAULT '',
            mode TEXT DEFAULT 'PASV',
            use_tls INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. Bảng recent_connections
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recent_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 2121,
            username TEXT NOT NULL,
            mode TEXT DEFAULT 'PASV',
            use_tls INTEGER DEFAULT 0,
            last_remote_dir TEXT DEFAULT '/',
            connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
        );
    """)

    # Thêm site mẫu nếu rỗng
    cur.execute("SELECT COUNT(*) FROM sites;")
    count = cur.fetchone()[0]
    if count == 0:
        print(" -> Thêm Site mẫu mặc định: 'Localhost Test Server'...")
        cur.execute("""
            INSERT INTO sites (name, host, port, username, password, mode, use_tls, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, ("Localhost Test Server", "127.0.0.1", 2121, "admin", "123456", "PASV", 0, "Server thử nghiệm cục bộ"))

    conn.commit()
    conn.close()
    print(" -> Hoàn tất nâng cấp ftp_client.db thành công!")


def main():
    print("=" * 60)
    print(" BẮT ĐẦU NÂNG CẤP HỆ THỐNG CƠ SỞ DỮ LIỆU FTP (SQLite)")
    print("=" * 60)
    migrate_server_db("ftp_server.db")
    migrate_client_db("ftp_client.db")
    print("\n" + "=" * 60)
    print(" ✔ TẤT CẢ CƠ SỞ DỮ LIỆU ĐÃ ĐƯỢC ĐỒNG BỘ VÀ SẴN SÀNG HOẠT ĐỘNG!")
    print("=" * 60)


if __name__ == "__main__":
    main()
