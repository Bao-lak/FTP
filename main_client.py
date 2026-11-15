"""
Điểm khởi chạy chương trình Máy khách FTP (FTP Client).
Khởi chạy giao diện đồ họa Tkinter tương tự FileZilla (ClientUI).
Hỗ trợ cờ --cli để kiểm thử dòng lệnh nếu môi trường không có DISPLAY.
"""

import sys
import os
import argparse
from ftp_client.client import CustomFTPClient, FTPClientError


def run_gui():
    """Khởi chạy giao diện đồ họa Tkinter."""
    import tkinter as tk
    from ftp_client.client_ui import ClientUI

    root = tk.Tk()
    app = ClientUI(root)
    root.mainloop()


def run_cli_test(host: str, port: int, user: str, password: str):
    """Kiểm thử nhanh kết nối bằng socket client dạng dòng lệnh."""
    print("=" * 60)
    print(f"▶ Bắt đầu kiểm thử FTP Client dạng CLI tới {host}:{port}")
    print("=" * 60)

    client = CustomFTPClient(log_callback=lambda msg, tag: print(f"[{tag}] {msg}"))
    try:
        client.connect(host, port)
        client.login(user, password)
        pwd = client.pwd()
        print(f"✔ Thư mục hiện tại: {pwd}")

        print("✔ Lấy danh sách thư mục (LIST):")
        items = client.list_dir("")
        for it in items:
            t = "DIR " if it["is_dir"] else "FILE"
            print(f"   [{t}] {it['name']:<20} {it['size']:>10} bytes  {it['mtime']}")

        client.disconnect()
        print("✔ Kiểm thử thành công!")
    except Exception as e:
        print(f"✖ Lỗi trong quá trình kiểm thử: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Chương trình Máy khách FTP Client (FileZilla Style)")
    parser.add_argument("--cli", action="store_true", help="Chạy kiểm thử kết nối nhanh dạng CLI")
    parser.add_argument("--host", default="127.0.0.1", help="Địa chỉ Host của FTP Server")
    parser.add_argument("--port", type=int, default=2121, help="Cổng của FTP Server")
    parser.add_argument("--user", default="admin", help="Tài khoản đăng nhập")
    parser.add_argument("--password", default="123456", help="Mật khẩu đăng nhập")

    args = parser.parse_args()

    has_display = bool(os.environ.get("DISPLAY"))

    if args.cli or not has_display:
        if not has_display and not args.cli:
            print("Không tìm thấy biến môi trường DISPLAY, chuyển sang chế độ kiểm thử CLI.")
        run_cli_test(args.host, args.port, args.user, args.password)
    else:
        run_gui()


if __name__ == "__main__":
    main()

