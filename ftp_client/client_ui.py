"""
Giao diện FTP Client (Tkinter GUI - Phong cách FileZilla).
Bao gồm:
- Thanh kết nối nhanh: Host, Port, Username, Password, Mode (PASV/PORT), TLS, nút Connect / Disconnect.
- Khung log thời gian thực hiển thị lệnh gửi và phản hồi server.
- Hai khung duyệt file:
  + Bên trái: Máy cục bộ (Local Site).
  + Bên phải: Máy chủ từ xa (Remote Site).
- Thao tác: Kéo thả / Double click, tạo thư mục, xóa, đổi tên, làm mới.
- Hàng đợi truyền file và thanh tiến trình (ttk.Progressbar) chạy đa luồng không đơ giao diện.
"""

import os
import sys
import time
import datetime
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, scrolledtext
from typing import Optional, List, Dict, Any

from .client import CustomFTPClient, FTPClientError


class ClientUI:
    """Cửa sổ ứng dụng FTP Client phong cách FileZilla."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FTP Client - Giao diện FileZilla (Lab 2 Mạng Máy Tính)")

        # 1. Tự động tính toán tỷ lệ theo độ phân giải màn hình (Hỗ trợ 2K/2.8K/4K HiDPI)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        # Tỷ lệ scale chuẩn dựa trên 1920x1080 (Màn 2880x1800 -> scale = 1.5)
        self.scale = max(1.0, min(2.5, screen_w / 1920.0))

        # Cài đặt kích thước cửa sổ tương thích độ phân giải màn hình
        win_w = max(1050, int(screen_w * 0.65))
        win_h = max(720, int(screen_h * 0.74))
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(0, (screen_h - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.root.minsize(int(950 * self.scale * 0.75), int(620 * self.scale * 0.75))

        # Hàng đợi giao tiếp luồng với GUI
        self.gui_queue = queue.Queue()

        # Đối tượng FTP Client
        self.client = CustomFTPClient(log_callback=self._on_client_log)

        # Trạng thái thư mục
        self.local_current_dir = os.path.abspath(os.path.expanduser("~"))
        self.remote_current_dir = "/"

        # Khởi tạo style và dựng giao diện
        self._setup_styles()
        self._build_ui()

        # Nạp danh sách file cục bộ ban đầu
        self.refresh_local_files()

        # Vòng lặp cập nhật GUI từ Queue
        self.root.after(100, self._process_gui_queue)

    def _setup_styles(self) -> None:
        """Cấu hình style giao diện ttk co giãn linh hoạt theo tỷ lệ màn hình."""
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Định cỡ font chữ và khoảng cách theo tỷ lệ màn hình (HiDPI)
        self.font_base = ("Segoe UI", max(10, int(10 * self.scale)))
        self.font_bold = ("Segoe UI", max(10, int(10 * self.scale)), "bold")
        self.font_header = ("Segoe UI", max(11, int(11.5 * self.scale)), "bold")
        self.font_log = ("Consolas", max(9, int(10 * self.scale)))
        self.row_height = max(26, int(28 * self.scale))

        self.style.configure(".", font=self.font_base)
        self.style.configure("Header.TLabel", font=self.font_header)
        self.style.configure("Treeview.Heading", font=self.font_bold)
        self.style.configure("Treeview", rowheight=self.row_height)

    def _build_ui(self) -> None:
        """Xây dựng toàn bộ bố cục giao diện."""
        # 1. Thanh kết nối nhanh (Quick Connect Bar)
        self._build_quick_connect_bar()

        # 2. Khung Log thông điệp FTP (Server Response Console)
        self._build_log_frame()

        # 3. Khung chia đôi duyệt file (Dual Panes: Local vs Remote)
        self._build_dual_file_panes()

        # 4. Khung hàng đợi & Thanh tiến trình truyền file (Transfer Queue & Progress)
        self._build_transfer_queue_frame()

    # ==================== 1. THANH KẾT NỐI NHANH ====================

    def _build_quick_connect_bar(self) -> None:
        """Thanh nhập thông tin kết nối nhanh trên cùng."""
        bar = ttk.LabelFrame(self.root, text="Kết Nối Nhanh", padding="6")
        bar.pack(fill=tk.X, padx=8, pady=(5, 3))

        # Host
        ttk.Label(bar, text="Host:").pack(side=tk.LEFT, padx=(5, 2))
        self.ent_host = ttk.Entry(bar, width=15)
        self.ent_host.insert(0, "127.0.0.1")
        self.ent_host.pack(side=tk.LEFT, padx=2)

        # Port
        ttk.Label(bar, text="Port:").pack(side=tk.LEFT, padx=(5, 2))
        self.ent_port = ttk.Entry(bar, width=6)
        self.ent_port.insert(0, "2121")
        self.ent_port.pack(side=tk.LEFT, padx=2)

        # Username
        ttk.Label(bar, text="User:").pack(side=tk.LEFT, padx=(5, 2))
        self.ent_user = ttk.Entry(bar, width=12)
        self.ent_user.insert(0, "admin")
        self.ent_user.pack(side=tk.LEFT, padx=2)

        # Password
        ttk.Label(bar, text="Pass:").pack(side=tk.LEFT, padx=(5, 2))
        self.ent_pass = ttk.Entry(bar, width=12, show="*")
        self.ent_pass.insert(0, "123456")
        self.ent_pass.pack(side=tk.LEFT, padx=2)

        # Chế độ PASV / PORT
        ttk.Label(bar, text="Mode:").pack(side=tk.LEFT, padx=(8, 2))
        self.cbo_mode = ttk.Combobox(
            bar, values=["PASV (Passive)", "PORT (Active)"], width=13, state="readonly"
        )
        self.cbo_mode.current(0)
        self.cbo_mode.pack(side=tk.LEFT, padx=2)

        # Checkbox FTPS
        self.var_tls = tk.BooleanVar(value=False)
        self.chk_tls = ttk.Checkbutton(bar, text="TLS", variable=self.var_tls)
        self.chk_tls.pack(side=tk.LEFT, padx=4)

        # Nút Kết nối / Ngắt kết nối
        self.btn_connect = tk.Button(
            bar,
            text="Kết Nối",
            bg="#2980b9",
            fg="white",
            font=self.font_bold,
            padx=12,
            pady=3,
            command=self.action_connect,
        )
        self.btn_connect.pack(side=tk.LEFT, padx=6)

        self.btn_disconnect = tk.Button(
            bar,
            text="Ngắt Kết Nối",
            bg="#7f8c8d",
            fg="white",
            font=self.font_bold,
            state=tk.DISABLED,
            padx=10,
            pady=3,
            command=self.action_disconnect,
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=2)

    # ==================== 2. KHUNG LOG THÔNG ĐIỆP ====================

    def _build_log_frame(self) -> None:
        """Khung hiển thị lệnh gửi và phản hồi chuẩn FTP."""
        log_frame = ttk.LabelFrame(self.root, text="Nhật Ký Thông Điệp FTP", padding="4")
        log_frame.pack(fill=tk.X, padx=8, pady=2)

        self.txt_log = scrolledtext.ScrolledText(
            log_frame, height=5, font=self.font_log, bg="#1e272e", fg="#d2dae2", wrap=tk.WORD
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        self.txt_log.tag_config("CMD", foreground="#00d2d3")
        self.txt_log.tag_config("SUCCESS", foreground="#1dd1a1")
        self.txt_log.tag_config("INFO", foreground="#54a0ff")
        self.txt_log.tag_config("WARN", foreground="#ff9f43")
        self.txt_log.tag_config("ERROR", foreground="#ee5253")

    def _on_client_log(self, message: str, tag: str) -> None:
        """Callback từ FTP Client để ghi nhận dòng log lên UI thread an toàn."""
        self.gui_queue.put({"action": "LOG", "text": message, "tag": tag})

    # ==================== 3. KHUNG DUAL-PANE FILE BROWSER ====================

    def _build_dual_file_panes(self) -> None:
        """Khung chia đôi chứa Local Site và Remote Site."""
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=3)

        # Bên trái: Khung máy cục bộ (Local)
        local_frame = ttk.LabelFrame(paned, text="Máy Cục Bộ (Local Site)", padding="5")
        paned.add(local_frame, weight=1)
        self._setup_local_browser(local_frame)

        # Bên phải: Khung máy chủ (Remote)
        remote_frame = ttk.LabelFrame(paned, text="Máy Chủ FTP (Remote Site)", padding="5")
        paned.add(remote_frame, weight=1)
        self._setup_remote_browser(remote_frame)

    def _setup_local_browser(self, parent: ttk.Frame) -> None:
        """Thiết lập cây thư mục máy cục bộ."""
        # Thanh điều hướng cục bộ
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(nav, text="⬆ Lên", width=5, command=self._local_go_up).pack(side=tk.LEFT, padx=2)
        self.ent_local_path = ttk.Entry(nav)
        self.ent_local_path.insert(0, self.local_current_dir)
        self.ent_local_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.ent_local_path.bind("<Return>", lambda e: self._on_local_path_enter())
        ttk.Button(nav, text="🔄", width=3, command=self.refresh_local_files).pack(
            side=tk.RIGHT, padx=2
        )

        # Bảng file cục bộ
        cols = ("name", "size", "mtime", "type")
        self.tree_local = ttk.Treeview(parent, columns=cols, show="headings", selectmode="extended")
        self.tree_local.heading("name", text="Tên")
        self.tree_local.heading("size", text="Kích thước")
        self.tree_local.heading("mtime", text="Ngày sửa")
        self.tree_local.heading("type", text="Loại")

        self.tree_local.column("name", width=int(220 * self.scale), stretch=True, anchor=tk.W)
        self.tree_local.column("size", width=int(95 * self.scale), stretch=False, anchor=tk.E)
        self.tree_local.column("mtime", width=int(145 * self.scale), stretch=False, anchor=tk.CENTER)
        self.tree_local.column("type", width=int(85 * self.scale), stretch=False, anchor=tk.CENTER)

        sb_local = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.tree_local.yview)
        self.tree_local.configure(yscrollcommand=sb_local.set)

        self.tree_local.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb_local.pack(fill=tk.Y, side=tk.RIGHT)

        # Sự kiện double click và chuột phải
        self.tree_local.bind("<Double-1>", self._on_local_double_click)
        self.tree_local.bind("<Button-3>", self._show_local_context_menu)

        # Menu ngữ cảnh chuột phải cục bộ
        self.menu_local = tk.Menu(self.root, tearoff=0)
        self.menu_local.add_command(
            label="⬆ Tải lên Server (Upload)", command=self.action_upload_selected
        )
        self.menu_local.add_separator()
        self.menu_local.add_command(label="📁 Tạo thư mục mới", command=self._local_mkdir)
        self.menu_local.add_command(label="✏ Đổi tên", command=self._local_rename)
        self.menu_local.add_command(label="🗑 Xóa", command=self._local_delete)
        self.menu_local.add_separator()
        self.menu_local.add_command(label="🔄 Làm mới", command=self.refresh_local_files)

    def _setup_remote_browser(self, parent: ttk.Frame) -> None:
        """Thiết lập cây thư mục máy chủ từ xa."""
        # Thanh điều hướng từ xa
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 4))

        ttk.Button(nav, text="⬆ Lên", width=5, command=self._remote_go_up).pack(side=tk.LEFT, padx=2)
        self.ent_remote_path = ttk.Entry(nav)
        self.ent_remote_path.insert(0, "/")
        self.ent_remote_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.ent_remote_path.bind("<Return>", lambda e: self._on_remote_path_enter())
        ttk.Button(nav, text="🔄", width=3, command=self.refresh_remote_files).pack(
            side=tk.RIGHT, padx=2
        )

        # Bảng file từ xa
        cols = ("name", "size", "mtime", "type")
        self.tree_remote = ttk.Treeview(parent, columns=cols, show="headings", selectmode="extended")
        self.tree_remote.heading("name", text="Tên")
        self.tree_remote.heading("size", text="Kích thước")
        self.tree_remote.heading("mtime", text="Ngày sửa")
        self.tree_remote.heading("type", text="Loại")

        self.tree_remote.column("name", width=int(220 * self.scale), stretch=True, anchor=tk.W)
        self.tree_remote.column("size", width=int(95 * self.scale), stretch=False, anchor=tk.E)
        self.tree_remote.column("mtime", width=int(145 * self.scale), stretch=False, anchor=tk.CENTER)
        self.tree_remote.column("type", width=int(85 * self.scale), stretch=False, anchor=tk.CENTER)

        sb_remote = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.tree_remote.yview)
        self.tree_remote.configure(yscrollcommand=sb_remote.set)

        self.tree_remote.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb_remote.pack(fill=tk.Y, side=tk.RIGHT)

        # Sự kiện double click và chuột phải
        self.tree_remote.bind("<Double-1>", self._on_remote_double_click)
        self.tree_remote.bind("<Button-3>", self._show_remote_context_menu)

        # Menu ngữ cảnh chuột phải từ xa
        self.menu_remote = tk.Menu(self.root, tearoff=0)
        self.menu_remote.add_command(
            label="⬇ Tải về máy (Download)", command=self.action_download_selected
        )
        self.menu_remote.add_separator()
        self.menu_remote.add_command(label="📁 Tạo thư mục mới", command=self._remote_mkdir)
        self.menu_remote.add_command(label="✏ Đổi tên", command=self._remote_rename)
        self.menu_remote.add_command(label="🗑 Xóa", command=self._remote_delete)
        self.menu_remote.add_separator()
        self.menu_remote.add_command(label="🔄 Làm mới", command=self.refresh_remote_files)

    # ==================== 4. KHUNG HÀNG ĐỢI & TIẾN ĐỘ TRUYỀN FILE ====================

    def _build_transfer_queue_frame(self) -> None:
        """Khung tiến trình truyền file và bảng hàng đợi dưới cùng."""
        bot_frame = ttk.LabelFrame(self.root, text="Hàng Đợi & Tiến Độ Truyền File", padding="4")
        bot_frame.pack(fill=tk.X, padx=8, pady=(2, 6))

        # Thanh tiến trình
        prog_sub = ttk.Frame(bot_frame)
        prog_sub.pack(fill=tk.X, pady=(0, 4))

        self.lbl_transfer_status = ttk.Label(prog_sub, text="Trạng thái: Sẵn sàng")
        self.lbl_transfer_status.pack(side=tk.LEFT, padx=5)

        self.progressbar = ttk.Progressbar(prog_sub, orient=tk.HORIZONTAL, mode="determinate")
        self.progressbar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)

        # Bảng hàng đợi
        cols = ("file", "dir", "size", "progress", "speed", "status")
        self.tree_queue = ttk.Treeview(
            bot_frame, columns=cols, show="headings", height=3, selectmode="browse"
        )
        self.tree_queue.heading("file", text="Tên File")
        self.tree_queue.heading("dir", text="Chiều")
        self.tree_queue.heading("size", text="Kích thước")
        self.tree_queue.heading("progress", text="Tiến độ")
        self.tree_queue.heading("speed", text="Tốc độ")
        self.tree_queue.heading("status", text="Trạng thái")

        self.tree_queue.column("file", width=int(280 * self.scale), stretch=True, anchor=tk.W)
        self.tree_queue.column("dir", width=int(100 * self.scale), stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("size", width=int(110 * self.scale), stretch=False, anchor=tk.E)
        self.tree_queue.column("progress", width=int(110 * self.scale), stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("speed", width=int(125 * self.scale), stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("status", width=int(125 * self.scale), stretch=False, anchor=tk.CENTER)

        self.tree_queue.pack(fill=tk.X, expand=True)

    # ==================== XỬ LÝ SỰ KIỆN KẾT NỐI / NGẮT KẾT NỐI ====================

    def action_connect(self) -> None:
        """Bắt đầu kết nối tới server trên một luồng riêng."""
        host = self.ent_host.get().strip()
        port_str = self.ent_port.get().strip()
        user = self.ent_user.get().strip()
        pwd = self.ent_pass.get()
        mode_val = "PASV" if "PASV" in self.cbo_mode.get() else "PORT"
        use_tls = self.var_tls.get()

        if not host:
            messagebox.showwarning("Lỗi", "Vui lòng nhập địa chỉ Host!")
            return
        try:
            port = int(port_str)
        except ValueError:
            messagebox.showwarning("Lỗi", "Cổng phải là số nguyên!")
            return

        self.btn_connect.config(state=tk.DISABLED)

        def connect_worker():
            try:
                self.client.connect(host, port)
                if use_tls:
                    self.client.enable_ftps()
                self.client.set_mode(mode_val)
                self.client.login(user, pwd)

                # Đọc thư mục hiện tại
                pwd_dir = self.client.pwd()
                self.remote_current_dir = pwd_dir

                self.gui_queue.put({"action": "CONNECTED"})
                self.refresh_remote_files()
            except FTPClientError as e:
                self.gui_queue.put({"action": "CONNECT_FAILED", "error": str(e)})
            except Exception as e:
                self.gui_queue.put({"action": "CONNECT_FAILED", "error": f"Lỗi không xác định: {e}"})

        threading.Thread(target=connect_worker, daemon=True).start()

    def action_disconnect(self) -> None:
        """Ngắt kết nối tới server."""
        self.client.disconnect()
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_disconnect.config(state=tk.DISABLED)
        for item in self.tree_remote.get_children():
            self.tree_remote.delete(item)
        self.lbl_transfer_status.config(text="Trạng thái: Đã ngắt kết nối")

    # ==================== ĐIỀU HƯỚNG VÀ DUYỆT FILE CỤC BỘ ====================

    def refresh_local_files(self) -> None:
        """Đọc danh sách tệp tại thư mục cục bộ."""
        for item in self.tree_local.get_children():
            self.tree_local.delete(item)

        self.ent_local_path.delete(0, tk.END)
        self.ent_local_path.insert(0, self.local_current_dir)

        try:
            entries = sorted(os.listdir(self.local_current_dir))
            # Phân loại thư mục lên trước, tệp sau
            dirs = []
            files = []

            for name in entries:
                fp = os.path.join(self.local_current_dir, name)
                try:
                    st = os.stat(fp)
                    is_dir = os.path.isdir(fp)
                    size_str = f"{st.st_size:,} B" if not is_dir else ""
                    mtime_str = datetime.datetime.fromtimestamp(st.st_mtime).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    t_str = "Thư mục" if is_dir else "Tệp"
                    item = (name, size_str, mtime_str, t_str, is_dir)
                    if is_dir:
                        dirs.append(item)
                    else:
                        files.append(item)
                except OSError:
                    continue

            for name, s_str, m_str, t_str, is_dir in dirs + files:
                tag = "DIR" if is_dir else "FILE"
                self.tree_local.insert("", tk.END, values=(name, s_str, m_str, t_str), tags=(tag,))

        except Exception as e:
            messagebox.showerror("Lỗi duyệt file cục bộ", str(e))

    def _local_go_up(self) -> None:
        """Lên thư mục cha trên máy cục bộ."""
        parent = os.path.dirname(self.local_current_dir)
        if parent and parent != self.local_current_dir:
            self.local_current_dir = parent
            self.refresh_local_files()

    def _on_local_path_enter(self) -> None:
        """Người dùng ấn Enter tại ô đường dẫn cục bộ."""
        target = self.ent_local_path.get().strip()
        if os.path.exists(target) and os.path.isdir(target):
            self.local_current_dir = os.path.abspath(target)
            self.refresh_local_files()
        else:
            messagebox.showwarning("Lỗi", "Thư mục không tồn tại!")

    def _on_local_double_click(self, event) -> None:
        """Xử lý double click cục bộ: nếu thư mục thì vào, nếu tệp thì tải lên server."""
        sel = self.tree_local.selection()
        if not sel:
            return
        values = self.tree_local.item(sel[0], "values")
        name = values[0]
        is_dir = values[3] == "Thư mục"

        if is_dir:
            self.local_current_dir = os.path.join(self.local_current_dir, name)
            self.refresh_local_files()
        else:
            # Tải tệp lên máy chủ
            if self.client.is_connected and self.client.is_logged_in:
                self.action_upload_selected()
            else:
                messagebox.showinfo("Chưa kết nối", "Vui lòng kết nối tới FTP Server trước khi tải lên!")

    def _show_local_context_menu(self, event) -> None:
        """Hiện menu chuột phải tại vị trí con trỏ trên bảng cục bộ."""
        item = self.tree_local.identify_row(event.y)
        if item:
            self.tree_local.selection_set(item)
        self.menu_local.post(event.x_root, event.y_root)

    def _local_mkdir(self) -> None:
        """Tạo thư mục cục bộ mới."""
        name = simpledialog.askstring("Thư mục mới", "Nhập tên thư mục mới cần tạo:")
        if name:
            p = os.path.join(self.local_current_dir, name)
            try:
                os.makedirs(p, exist_ok=False)
                self.refresh_local_files()
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể tạo thư mục: {e}")

    def _local_rename(self) -> None:
        """Đổi tên tệp hoặc thư mục cục bộ."""
        sel = self.tree_local.selection()
        if not sel:
            return
        old_name = self.tree_local.item(sel[0], "values")[0]
        new_name = simpledialog.askstring("Đổi tên", f"Nhập tên mới cho '{old_name}':")
        if new_name and new_name != old_name:
            src = os.path.join(self.local_current_dir, old_name)
            dst = os.path.join(self.local_current_dir, new_name)
            try:
                os.rename(src, dst)
                self.refresh_local_files()
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể đổi tên: {e}")

    def _local_delete(self) -> None:
        """Xóa tệp hoặc thư mục cục bộ."""
        sel = self.tree_local.selection()
        if not sel:
            return
        values = self.tree_local.item(sel[0], "values")
        name = values[0]
        is_dir = values[3] == "Thư mục"
        p = os.path.join(self.local_current_dir, name)

        if messagebox.askyesno("Xác nhận xóa", f"Bạn có chắc muốn xóa '{name}' trên máy cục bộ?"):
            try:
                if is_dir:
                    os.rmdir(p)
                else:
                    os.remove(p)
                self.refresh_local_files()
            except Exception as e:
                messagebox.showerror("Lỗi xóa", str(e))

    # ==================== ĐIỀU HƯỚNG VÀ DUYỆT FILE TỪ XA (REMOTE) ====================

    def refresh_remote_files(self) -> None:
        """Gửi lệnh LIST để cập nhật danh sách file trên server qua luồng phụ."""
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def list_worker():
            try:
                items = self.client.list_dir("")
                pwd = self.client.pwd()
                self.remote_current_dir = pwd
                self.gui_queue.put({"action": "UPDATE_REMOTE_LIST", "items": items, "pwd": pwd})
            except Exception as e:
                self.gui_queue.put({"action": "REMOTE_ERROR", "error": f"Lỗi lấy danh sách file: {e}"})

        threading.Thread(target=list_worker, daemon=True).start()

    def _remote_go_up(self) -> None:
        """Lên thư mục cha trên server."""
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def up_worker():
            try:
                self.client.cdup()
                self.refresh_remote_files()
            except Exception as e:
                self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

        threading.Thread(target=up_worker, daemon=True).start()

    def _on_remote_path_enter(self) -> None:
        """Người dùng gõ đường dẫn server và nhấn Enter."""
        target = self.ent_remote_path.get().strip()
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def cwd_worker():
            try:
                self.client.cwd(target)
                self.refresh_remote_files()
            except Exception as e:
                self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

        threading.Thread(target=cwd_worker, daemon=True).start()

    def _on_remote_double_click(self, event) -> None:
        """Xử lý double click từ xa: nếu thư mục thì CWD vào, nếu tệp thì tải về máy."""
        sel = self.tree_remote.selection()
        if not sel:
            return
        values = self.tree_remote.item(sel[0], "values")
        name = values[0]
        is_dir = values[3] == "Thư mục"

        if is_dir:
            def cd_worker():
                try:
                    self.client.cwd(name)
                    self.refresh_remote_files()
                except Exception as e:
                    self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

            threading.Thread(target=cd_worker, daemon=True).start()
        else:
            # Tải tệp về máy
            self.action_download_selected()

    def _show_remote_context_menu(self, event) -> None:
        """Hiện menu chuột phải tại vị trí con trỏ trên bảng từ xa."""
        item = self.tree_remote.identify_row(event.y)
        if item:
            self.tree_remote.selection_set(item)
        self.menu_remote.post(event.x_root, event.y_root)

    def _remote_mkdir(self) -> None:
        """Tạo thư mục trên server."""
        if not (self.client.is_connected and self.client.is_logged_in):
            return
        name = simpledialog.askstring("Thư mục mới", "Nhập tên thư mục cần tạo trên server:")
        if name:
            def mkdir_worker():
                try:
                    self.client.mkd(name)
                    self.refresh_remote_files()
                except Exception as e:
                    self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

            threading.Thread(target=mkdir_worker, daemon=True).start()

    def _remote_rename(self) -> None:
        """Đổi tên file/thư mục trên server."""
        sel = self.tree_remote.selection()
        if not sel:
            return
        old_name = self.tree_remote.item(sel[0], "values")[0]
        new_name = simpledialog.askstring("Đổi tên", f"Nhập tên mới cho '{old_name}' trên server:")
        if new_name and new_name != old_name:
            def ren_worker():
                try:
                    self.client.rename(old_name, new_name)
                    self.refresh_remote_files()
                except Exception as e:
                    self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

            threading.Thread(target=ren_worker, daemon=True).start()

    def _remote_delete(self) -> None:
        """Xóa file hoặc thư mục trên server."""
        sel = self.tree_remote.selection()
        if not sel:
            return
        values = self.tree_remote.item(sel[0], "values")
        name = values[0]
        is_dir = values[3] == "Thư mục"

        if messagebox.askyesno("Xác nhận xóa", f"Bạn có chắc muốn xóa '{name}' trên máy chủ FTP?"):
            def del_worker():
                try:
                    if is_dir:
                        self.client.rmd(name)
                    else:
                        self.client.dele(name)
                    self.refresh_remote_files()
                except Exception as e:
                    self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

            threading.Thread(target=del_worker, daemon=True).start()

    # ==================== TRUYỀN FILE ĐA LUỒNG (UPLOAD / DOWNLOAD) ====================

    def action_upload_selected(self) -> None:
        """Tải các file đã chọn từ máy cục bộ lên server."""
        if not (self.client.is_connected and self.client.is_logged_in):
            messagebox.showwarning("Chưa kết nối", "Vui lòng kết nối tới server trước!")
            return

        selected_items = self.tree_local.selection()
        if not selected_items:
            messagebox.showinfo("Chưa chọn file", "Vui lòng chọn ít nhất một tệp để tải lên!")
            return

        for item in selected_items:
            values = self.tree_local.item(item, "values")
            filename = values[0]
            is_dir = values[3] == "Thư mục"
            if is_dir:
                messagebox.showinfo("Thông báo", f"Bỏ qua thư mục '{filename}' (Chỉ hỗ trợ upload file trực tiếp).")
                continue

            local_file_path = os.path.join(self.local_current_dir, filename)
            remote_file_path = filename
            file_size = os.path.getsize(local_file_path)

            # Thêm vào bảng hàng đợi UI
            q_id = self.tree_queue.insert(
                "",
                tk.END,
                values=(filename, "UPLOAD ⬆", f"{file_size:,} B", "0%", "0 KB/s", "Đang chờ"),
            )

            # Khởi động luồng upload
            threading.Thread(
                target=self._upload_thread,
                args=(local_file_path, remote_file_path, q_id, file_size),
                daemon=True,
            ).start()

    def _upload_thread(self, local_path: str, remote_path: str, q_id: str, total_size: int) -> None:
        """Luồng thực thi việc upload file và cập nhật tiến độ."""
        self.gui_queue.put(
            {"action": "QUEUE_UPDATE", "id": q_id, "status": "Đang truyền...", "speed": "0 KB/s"}
        )

        def on_progress(transferred: int, total: int, speed: float):
            pct = int((transferred / total) * 100) if total > 0 else 0
            spd_str = f"{speed / 1024:.1f} KB/s" if speed < 1024 * 1024 else f"{speed / (1024*1024):.2f} MB/s"
            self.gui_queue.put(
                {
                    "action": "PROGRESS",
                    "id": q_id,
                    "percent": pct,
                    "speed": spd_str,
                    "file": os.path.basename(local_path),
                }
            )

        try:
            ok = self.client.upload_file(local_path, remote_path, progress_callback=on_progress)
            if ok:
                self.gui_queue.put(
                    {
                        "action": "QUEUE_UPDATE",
                        "id": q_id,
                        "status": "Hoàn tất",
                        "percent": 100,
                        "speed": "--",
                    }
                )
                self.refresh_remote_files()
            else:
                self.gui_queue.put(
                    {"action": "QUEUE_UPDATE", "id": q_id, "status": "Đã hủy", "speed": "--"}
                )
        except Exception as e:
            self.gui_queue.put(
                {"action": "QUEUE_UPDATE", "id": q_id, "status": f"Lỗi: {e}", "speed": "--"}
            )

    def action_download_selected(self) -> None:
        """Tải các file đã chọn từ server về máy cục bộ."""
        if not (self.client.is_connected and self.client.is_logged_in):
            messagebox.showwarning("Chưa kết nối", "Vui lòng kết nối tới server trước!")
            return

        selected_items = self.tree_remote.selection()
        if not selected_items:
            messagebox.showinfo("Chưa chọn file", "Vui lòng chọn ít nhất một tệp để tải về!")
            return

        for item in selected_items:
            values = self.tree_remote.item(item, "values")
            filename = values[0]
            is_dir = values[3] == "Thư mục"
            if is_dir:
                messagebox.showinfo("Thông báo", f"Bỏ qua thư mục '{filename}' (Chỉ hỗ trợ download file trực tiếp).")
                continue

            local_file_path = os.path.join(self.local_current_dir, filename)
            remote_file_path = filename
            size_str = values[1].replace(" B", "").replace(",", "")
            file_size = int(size_str) if size_str.isdigit() else 0

            # Kiểm tra xem file đã có một phần hay chưa để hỏi resume
            resume = False
            if os.path.exists(local_file_path):
                cur_sz = os.path.getsize(local_file_path)
                if 0 < cur_sz < file_size:
                    resume = messagebox.askyesno(
                        "Tiếp tục tải (Resume)",
                        f"Tệp '{filename}' đã có sẵn {cur_sz:,} B trên máy. Bạn có muốn tiếp tục tải (REST) không?",
                    )

            q_id = self.tree_queue.insert(
                "",
                tk.END,
                values=(filename, "DOWNLOAD ⬇", f"{file_size:,} B", "0%", "0 KB/s", "Đang chờ"),
            )

            threading.Thread(
                target=self._download_thread,
                args=(remote_file_path, local_file_path, q_id, file_size, resume),
                daemon=True,
            ).start()

    def _download_thread(
        self, remote_path: str, local_path: str, q_id: str, total_size: int, resume: bool
    ) -> None:
        """Luồng thực thi việc download file và cập nhật tiến độ."""
        self.gui_queue.put(
            {"action": "QUEUE_UPDATE", "id": q_id, "status": "Đang tải...", "speed": "0 KB/s"}
        )

        def on_progress(transferred: int, total: int, speed: float):
            final_total = total if total > 0 else total_size
            pct = int((transferred / final_total) * 100) if final_total > 0 else 0
            spd_str = f"{speed / 1024:.1f} KB/s" if speed < 1024 * 1024 else f"{speed / (1024*1024):.2f} MB/s"
            self.gui_queue.put(
                {
                    "action": "PROGRESS",
                    "id": q_id,
                    "percent": pct,
                    "speed": spd_str,
                    "file": os.path.basename(remote_path),
                }
            )

        try:
            ok = self.client.download_file(
                remote_path, local_path, resume=resume, progress_callback=on_progress
            )
            if ok:
                self.gui_queue.put(
                    {
                        "action": "QUEUE_UPDATE",
                        "id": q_id,
                        "status": "Hoàn tất",
                        "percent": 100,
                        "speed": "--",
                    }
                )
                self.refresh_local_files()
            else:
                self.gui_queue.put(
                    {"action": "QUEUE_UPDATE", "id": q_id, "status": "Đã hủy", "speed": "--"}
                )
        except Exception as e:
            self.gui_queue.put(
                {"action": "QUEUE_UPDATE", "id": q_id, "status": f"Lỗi: {e}", "speed": "--"}
            )

    # ==================== VÒNG LẶP CẬP NHẬT GUI (GUI QUEUE) ====================

    def _process_gui_queue(self) -> None:
        """Đọc và thực thi các yêu cầu cập nhật giao diện từ các luồng nền."""
        try:
            while not self.gui_queue.empty():
                msg = self.gui_queue.get_nowait()
                act = msg.get("action")

                if act == "LOG":
                    self.txt_log.insert(tk.END, msg["text"] + "\n", msg.get("tag", "INFO"))
                    self.txt_log.see(tk.END)

                elif act == "CONNECTED":
                    self.btn_connect.config(state=tk.DISABLED)
                    self.btn_disconnect.config(state=tk.NORMAL)
                    self.lbl_transfer_status.config(text="Trạng thái: Đã kết nối và đăng nhập")

                elif act == "CONNECT_FAILED":
                    self.btn_connect.config(state=tk.NORMAL)
                    self.btn_disconnect.config(state=tk.DISABLED)
                    messagebox.showerror("Lỗi kết nối", msg.get("error", "Không thể kết nối!"))

                elif act == "UPDATE_REMOTE_LIST":
                    for item in self.tree_remote.get_children():
                        self.tree_remote.delete(item)

                    pwd = msg.get("pwd", "/")
                    self.ent_remote_path.delete(0, tk.END)
                    self.ent_remote_path.insert(0, pwd)

                    items = msg.get("items", [])
                    # Sắp xếp thư mục trước, file sau
                    dirs = [i for i in items if i["is_dir"]]
                    files = [i for i in items if not i["is_dir"]]

                    for it in dirs + files:
                        sz_str = f"{it['size']:,} B" if not it["is_dir"] else ""
                        self.tree_remote.insert(
                            "",
                            tk.END,
                            values=(it["name"], sz_str, it["mtime"], it["type"]),
                            tags=("DIR" if it["is_dir"] else "FILE",),
                        )

                elif act == "REMOTE_ERROR":
                    messagebox.showerror("Lỗi máy chủ", msg.get("error"))

                elif act == "PROGRESS":
                    pct = msg.get("percent", 0)
                    spd = msg.get("speed", "")
                    fname = msg.get("file", "")
                    self.progressbar["value"] = pct
                    self.lbl_transfer_status.config(
                        text=f"Đang truyền: {fname} ({pct}%) - Tốc độ: {spd}"
                    )
                    # Cập nhật dòng tương ứng trên queue table
                    qid = msg.get("id")
                    if qid and self.tree_queue.exists(qid):
                        cur_vals = list(self.tree_queue.item(qid, "values"))
                        cur_vals[3] = f"{pct}%"
                        cur_vals[4] = spd
                        cur_vals[5] = "Đang truyền"
                        self.tree_queue.item(qid, values=cur_vals)

                elif act == "QUEUE_UPDATE":
                    qid = msg.get("id")
                    if qid and self.tree_queue.exists(qid):
                        cur_vals = list(self.tree_queue.item(qid, "values"))
                        if "status" in msg:
                            cur_vals[5] = msg["status"]
                        if "percent" in msg:
                            cur_vals[3] = f"{msg['percent']}%"
                        if "speed" in msg:
                            cur_vals[4] = msg["speed"]
                        self.tree_queue.item(qid, values=cur_vals)

                    if msg.get("status") in ("Hoàn tất", "Đã hủy"):
                        self.progressbar["value"] = 100 if msg.get("status") == "Hoàn tất" else 0
                        self.lbl_transfer_status.config(
                            text=f"Truyền tệp: {msg.get('status')}"
                        )

        except Exception:
            pass
        finally:
            self.root.after(100, self._process_gui_queue)

