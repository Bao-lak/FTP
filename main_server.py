"""
Điểm khởi chạy chương trình Máy chủ FTP (FTP Server).
Mặc định khởi chạy giao diện đồ họa Tkinter (ServerUI).
Hỗ trợ cờ --headless hoặc --cli để chạy trực tiếp trên dòng lệnh không cần màn hình X11.
"""

import sys
import os
import time
import argparse
import signal
from ftp_server.database import DatabaseManager
from ftp_server.server import FTPServer


def run_gui():
    """Khởi chạy giao diện quản trị đồ họa Tkinter."""
    import tkinter as tk
    from ftp_server.server_ui import ServerUI

    root = tk.Tk()
    app = ServerUI(root)
    root.mainloop()


def run_headless(host: str, port: int, root_dir: str):
    """Khởi chạy máy chủ FTP dạng Console / Service không cần màn hình X11."""
    print("=" * 60)
    print(f"▶ Khởi động FTP Server ở chế độ Console (Headless Mode)")
    print(f"  - Lắng nghe: {host}:{port}")
    print(f"  - Thư mục gốc: {os.path.abspath(root_dir)}")
    print("  - Nhấn Ctrl+C để dừng server.")
    print("=" * 60)

    server = FTPServer(
        host=host,
        port=port,
        root_dir=root_dir,
        db_path="ftp_server.db",
        allow_anonymous=True,
    )

    if not server.start():
        print("✖ Không thể khởi động server! Thoát.")
        sys.exit(1)

    def signal_handler(sig, frame):
        print("\n⏹ Đang nhận tín hiệu dừng server...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        while server.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


def main():
    parser = argparse.ArgumentParser(description="Chương trình Máy chủ FTP (RFC 959)")
    parser.add_argument(
        "--headless",
        "--cli",
        action="store_true",
        help="Chạy server dạng console không dùng giao diện Tkinter",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Địa chỉ IP lắng nghe (mặc định 0.0.0.0)")
    parser.add_argument("--port", type=int, default=2121, help="Cổng kết nối (mặc định 2121)")
    parser.add_argument(
        "--root", default="./ftp_root", help="Thư mục gốc lưu trữ (mặc định ./ftp_root)"
    )

    args = parser.parse_args()

    # Tự động chuyển sang headless nếu không tìm thấy biến môi trường DISPLAY trên Linux
    has_display = bool(os.environ.get("DISPLAY"))

    if args.headless or not has_display:
        if not has_display and not args.headless:
            print("Không tìm thấy màn hình đồ họa (DISPLAY trống), tự động chuyển sang chế độ Headless.")
        run_headless(args.host, args.port, args.root)
    else:
        run_gui()


if __name__ == "__main__":
    main()

