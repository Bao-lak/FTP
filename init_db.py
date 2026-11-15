"""
Script Khởi tạo Cơ sở dữ liệu và Thư mục dữ liệu mẫu cho FTP Server.
Tạo các bảng SQLite, cấu hình tài khoản mặc định và sinh file mẫu để kiểm thử.
"""

import os
import sys
from ftp_server.database import DatabaseManager


def init_system():
    print("=" * 60)
    print("   KHỞI TẠO HỆ THỐNG FTP SERVER (RFC 959)")
    print("=" * 60)

    db_path = "ftp_server.db"
    print(f"[1] Khởi tạo Database SQLite: {os.path.abspath(db_path)}...")
    db = DatabaseManager(db_path)

    base_root = os.path.abspath("./ftp_root")
    admin_home = os.path.join(base_root, "admin")
    anon_home = os.path.join(base_root, "anonymous")
    student_home = os.path.join(base_root, "student")

    for d in [admin_home, anon_home, student_home]:
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(admin_home, "tailieu"), exist_ok=True)

    print("[2] Tạo các tệp dữ liệu mẫu để kiểm thử...")
    # File cho admin
    with open(os.path.join(admin_home, "welcome.txt"), "w", encoding="utf-8") as f:
        f.write("Chào mừng bạn đến với FTP Server - Lab 2 Mạng Máy Tính!\nĐây là tệp kiểm thử tải về.\n")

    with open(os.path.join(admin_home, "tailieu", "huongdan.txt"), "w", encoding="utf-8") as f:
        f.write("Thư mục con 'tailieu' kiểm thử duyệt cây thư mục CWD / CDUP.\n")

    # File 500 KB để test progress bar
    big_file_path = os.path.join(admin_home, "data_500kb.bin")
    if not os.path.exists(big_file_path):
        with open(big_file_path, "wb") as f:
            f.write(b"FTP_DATA_CHUNK_1234567890\n" * 20000)

    # File cho anonymous
    with open(os.path.join(anon_home, "readme.txt"), "w", encoding="utf-8") as f:
        f.write("Tài khoản Anonymous: Chỉ có quyền ĐỌC (can_read=1, can_write=0, can_delete=0).\n")

    # File cho student
    with open(os.path.join(student_home, "bai_tap.txt"), "w", encoding="utf-8") as f:
        f.write("Tài khoản Student: Có quyền Đọc + Ghi, nhưng KHÔNG có quyền Xóa (can_delete=0).\n")

    print("[3] Thiết lập tài khoản mẫu trong Database:")

    # 1. Admin
    db.add_user(
        username="admin",
        password="123456",
        home_dir=admin_home,
        can_read=1,
        can_write=1,
        can_delete=1,
        is_active=1,
        quota_bytes=0,
        max_connections=10,
        speed_limit_kb=0,
    )
    print("   ✔ User: 'admin'     | Pass: '123456'     | Full Quyền (Read, Write, Delete)")

    # 2. Anonymous
    db.add_user(
        username="anonymous",
        password="",
        home_dir=anon_home,
        can_read=1,
        can_write=0,
        can_delete=0,
        is_active=1,
        quota_bytes=0,
        max_connections=5,
        speed_limit_kb=0,
    )
    print("   ✔ User: 'anonymous' | Pass: (Bất kỳ)     | Quyền: Chỉ Đọc (Read-only)")

    # 3. Student
    db.add_user(
        username="student",
        password="student123",
        home_dir=student_home,
        can_read=1,
        can_write=1,
        can_delete=0,
        is_active=1,
        quota_bytes=50 * 1024 * 1024,
        max_connections=3,
        speed_limit_kb=0,
    )
    print("   ✔ User: 'student'   | Pass: 'student123' | Quyền: Đọc & Ghi (Không được Xóa)")

    print("=" * 60)
    print("   KHỞI TẠO HOÀN TẤT! HỆ THỐNG ĐÃ SẴN SÀNG.")
    print("=" * 60)


if __name__ == "__main__":
    init_system()

