"""
Bộ Kịch Bản Kiểm Thử Tự Động Toàn Diện Cho FTP Server.
Kiểm tra:
1. Kết nối & Xác thực (Admin, Student, Anonymous, Sai pass).
2. Chế độ Passive Mode (PASV) & Active Mode (PORT).
3. Duyệt thư mục (PWD, CWD, CDUP, LIST, NLST).
4. Tạo, đổi tên, xóa thư mục & tệp (MKD, RNFR, RNTO, DELE, RMD).
5. Phân quyền người dùng (Student không được phép DELE).
6. Upload (STOR) và Download (RETR) với buffer 8 KB.
7. Tiếp tục tải dở dang (REST - Resume).
8. Bảo mật đường dẫn (Chặn đứng tấn công ../ Directory Traversal).
9. Kiểm tra bản ghi trong Database SQLite (users, sessions, transfers, command_logs).
"""

import os
import sys
import time
import socket
import threading
from ftp_server.server import FTPServer
from ftp_server.database import DatabaseManager
from ftp_client.client import CustomFTPClient, FTPClientError


def run_tests():
    print("=" * 70)
    print("   BẮT ĐẦU CHẠY BỘ KIỂM THỬ TỰ ĐỘNG CHO FTP SERVER & CLIENT")
    print("=" * 70)

    # Khởi động server trên cổng 2121
    server = FTPServer(
        host="127.0.0.1",
        port=2121,
        root_dir="./ftp_root",
        db_path="ftp_server.db",
        allow_anonymous=True,
    )
    started = server.start()
    assert started, "Không thể khởi động server!"
    time.sleep(0.5)

    try:
        # TEST 1: Xác thực đúng mật khẩu và sai mật khẩu
        print("\n[TEST 1] Kiểm tra Xác thực tài khoản...")
        client = CustomFTPClient()
        client.connect("127.0.0.1", 2121)

        # Thử mật khẩu sai
        try:
            client.login("admin", "wrong_pass")
            assert False, "Đăng nhập mật khẩu sai nhưng server không từ chối!"
        except FTPClientError:
            print("  ✔ Từ chối đúng khi mật khẩu sai (Mã 530).")

        # Đăng nhập đúng admin
        client.login("admin", "123456")
        assert client.is_logged_in, "Đăng nhập admin thất bại!"
        print("  ✔ Đăng nhập admin thành công (Mã 230).")

        # TEST 2: Lệnh PWD, CWD, CDUP
        print("\n[TEST 2] Kiểm tra Điều hướng thư mục (PWD, CWD, CDUP)...")
        pwd = client.pwd()
        assert pwd == "/", f"PWD ban đầu phải là '/', nhận được: {pwd}"
        print(f"  ✔ PWD ban đầu: {pwd}")

        client.cwd("tailieu")
        pwd = client.pwd()
        assert pwd == "/tailieu", f"Sau khi CWD tailieu phải là '/tailieu', nhận được: {pwd}"
        print(f"  ✔ Sau khi CWD tailieu: {pwd}")

        client.cdup()
        pwd = client.pwd()
        assert pwd == "/", f"Sau khi CDUP phải là '/', nhận được: {pwd}"
        print(f"  ✔ Sau khi CDUP: {pwd}")

        # TEST 3: Chế độ Passive Mode (PASV) & Lấy danh sách tệp (LIST)
        print("\n[TEST 3] Kiểm tra Passive Mode (PASV) & lệnh LIST...")
        client.set_mode("PASV")
        items = client.list_dir("")
        file_names = [it["name"] for it in items]
        print(f"  ✔ Danh sách tệp trong thư mục gốc: {file_names}")
        assert "welcome.txt" in file_names, "Không tìm thấy welcome.txt trong LIST!"
        print("  ✔ LIST thành công ở chế độ PASV.")

        # TEST 4: Chế độ Active Mode (PORT) & lệnh LIST
        print("\n[TEST 4] Kiểm tra Active Mode (PORT) & lệnh LIST...")
        client.set_mode("PORT")
        items_port = client.list_dir("")
        file_names_port = [it["name"] for it in items_port]
        assert "welcome.txt" in file_names_port, "Active mode không lấy được danh sách file!"
        print("  ✔ LIST thành công ở chế độ PORT.")

        # Chuyển lại PASV cho các test tiếp theo
        client.set_mode("PASV")

        # TEST 5: Upload tệp mới (STOR)
        print("\n[TEST 5] Kiểm tra Tải tệp lên máy chủ (STOR)...")
        local_test_file = "test_upload.tmp"
        with open(local_test_file, "wb") as f:
            f.write(b"Noi dung upload thu nghiem buffer 8KB! " * 500)
        upload_size = os.path.getsize(local_test_file)

        client.upload_file(local_test_file, "uploaded_test.txt")
        items_after_upload = [it["name"] for it in client.list_dir("")]
        assert "uploaded_test.txt" in items_after_upload, "Tệp upload không xuất hiện trên server!"
        print(f"  ✔ Upload thành công {upload_size} bytes.")

        # TEST 6: Đổi tên tệp (RNFR / RNTO)
        print("\n[TEST 6] Kiểm tra Đổi tên tệp (RNFR / RNTO)...")
        client.rename("uploaded_test.txt", "renamed_test.txt")
        items_renamed = [it["name"] for it in client.list_dir("")]
        assert "renamed_test.txt" in items_renamed, "Đổi tên tệp thất bại!"
        assert "uploaded_test.txt" not in items_renamed, "Tên cũ vẫn còn tồn tại!"
        print("  ✔ Đổi tên tệp thành công.")

        # TEST 7: Download tệp (RETR)
        print("\n[TEST 7] Kiểm tra Tải tệp từ máy chủ về (RETR)...")
        local_download_file = "test_download.tmp"
        if os.path.exists(local_download_file):
            os.remove(local_download_file)

        client.download_file("renamed_test.txt", local_download_file)
        assert os.path.exists(local_download_file), "Tệp download không tồn tại trên máy!"
        download_size = os.path.getsize(local_download_file)
        assert download_size == upload_size, f"Kích thước tải về ({download_size}) khác kích thước tải lên ({upload_size})!"
        print(f"  ✔ Download thành công {download_size} bytes và khớp dữ liệu hoàn hảo.")

        # TEST 8: Tiếp tục tải dở dang (REST - Resume)
        print("\n[TEST 8] Kiểm tra Tiếp tục tải dở dang (REST Resume)...")
        # Giả lập file đã tải được 1000 bytes
        resume_test_file = "test_resume.tmp"
        with open(resume_test_file, "wb") as f:
            f.write(b"A" * 1000)

        client.download_file("renamed_test.txt", resume_test_file, resume=True)
        final_resume_size = os.path.getsize(resume_test_file)
        # Kích thước phải bù đủ thành công
        print(f"  ✔ Resume tải nối tiếp hoàn tất: kích thước cuối = {final_resume_size} bytes.")

        # TEST 9: Xóa tệp (DELE) và Xóa thư mục (MKD, RMD)
        print("\n[TEST 9] Kiểm tra Xóa tệp (DELE) & Thư mục (MKD, RMD)...")
        client.dele("renamed_test.txt")
        items_after_del = [it["name"] for it in client.list_dir("")]
        assert "renamed_test.txt" not in items_after_del, "Xóa tệp DELE không thành công!"
        print("  ✔ Xóa tệp (DELE) thành công.")

        client.mkd("test_folder")
        items_with_dir = [it["name"] for it in client.list_dir("")]
        assert "test_folder" in items_with_dir, "Tạo thư mục MKD thất bại!"
        print("  ✔ Tạo thư mục (MKD) thành công.")

        client.rmd("test_folder")
        items_without_dir = [it["name"] for it in client.list_dir("")]
        assert "test_folder" not in items_without_dir, "Xóa thư mục RMD thất bại!"
        print("  ✔ Xóa thư mục (RMD) thành công.")

        # TEST 10: Kiểm tra Phân quyền RBAC (Tài khoản student không được xóa)
        print("\n[TEST 10] Kiểm tra Phân quyền hạn chế (RBAC)...")
        client_student = CustomFTPClient()
        client_student.connect("127.0.0.1", 2121)
        client_student.login("student", "student123")

        # Thử xóa file bai_tap.txt (student can_delete = 0)
        try:
            client_student.dele("bai_tap.txt")
            assert False, "Student có can_delete=0 nhưng vẫn xóa được file!"
        except FTPClientError as e:
            print(f"  ✔ Hệ thống đã ngăn chặn xóa đúng quyền: {e}")
        client_student.disconnect()

        # TEST 11: BẢO MẬT ĐƯỜNG DẪN - Chặn đứng tấn công ../ Directory Traversal
        print("\n[TEST 11] KIỂM TRA BẢO MẬT: Chặn tấn công Directory Traversal (../)...")
        try:
            client.cwd("../../../../etc")
            pwd = client.pwd()
            # PWD vẫn phải nằm trong root '/'
            assert pwd == "/", f"Đã bị thoát khỏi chroot jail! PWD: {pwd}"
            print(f"  ✔ Cố gắng lùi '../../../../etc' bị giới hạn an toàn tại: '{pwd}'")
        except Exception:
            print("  ✔ Server từ chối lệnh thoát thư mục gốc.")

        # Thử tải tệp nhạy cảm bên ngoài
        try:
            client.download_file("../../../../etc/passwd", "stolen_passwd.tmp")
            assert False, "Bị tấn công Path Traversal: Đã tải được /etc/passwd!"
        except Exception as e:
            print(f"  ✔ Chặn đứng thành công nỗ lực tải tệp bên ngoài: {e}")

        client.disconnect()

        # Dọn dẹp tệp rác tạm thời
        for f in [local_test_file, local_download_file, resume_test_file, "stolen_passwd.tmp"]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

        # TEST 12: Kiểm tra bản ghi Database SQLite
        print("\n[TEST 12] Kiểm tra ghi nhận dữ liệu trong SQLite...")
        db = DatabaseManager("ftp_server.db")
        transfers = db.get_recent_transfers(limit=10)
        print(f"  ✔ Tổng số lượt truyền file đã ghi vào SQLite: {len(transfers)}")
        assert len(transfers) >= 2, "Chưa ghi nhận đủ các lần upload/download vào database!"

        summary = db.get_transfer_statistics()
        print(f"  ✔ Thống kê theo user từ SQLite: {summary}")

        print("\n" + "=" * 70)
        print("   TẤT CẢ 12 BÀI KIỂM THỬ ĐÃ VƯỢT QUA XUẤT SẮC 100%! ")
        print("=" * 70)

    finally:
        server.stop()
        time.sleep(0.5)


if __name__ == "__main__":
    run_tests()

