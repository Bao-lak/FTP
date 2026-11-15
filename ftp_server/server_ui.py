"""
Giao diện Quản trị FTP Server (Tkinter GUI).
Bao gồm:
- Điều khiển Server (Start/Stop, đổi cổng, thư mục gốc, FTPS, Anonymous).
- Tab Quản lý người dùng (CRUD, phân quyền, đổi mật khẩu, quota, speed limit).
- Tab Đang kết nối (theo dõi client online, ngắt kết nối cưỡng bức).
- Tab Nhật ký & Lịch sử truyền file (Real-time logs + SQLite transfer records).
- Tab Thống kê (Biểu đồ số lượt truyền 7 ngày gần nhất, Top 5 tệp tải nhiều, Top 5 user truyền nhiều).
Cập nhật giao diện an toàn đa luồng qua queue.Queue và root.after().
Tự động tương thích với màn hình độ phân giải cao (HiDPI / 2K / 4K).
"""

import os
import queue
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from typing import Optional, Dict, Any

from .server import FTPServer
from .database import DatabaseManager


class ServerUI:
    """Cửa sổ đồ họa quản lý máy chủ FTP."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FTP Server Manager - RFC 959 (Lab 2 Mạng Máy Tính)")

        # 1. Tự động tính toán tỷ lệ theo độ phân giải màn hình (Hỗ trợ 2K/2.8K/4K HiDPI)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        self.scale = max(1.0, min(2.5, screen_w / 1920.0))

        win_w = max(1000, int(screen_w * 0.65))
        win_h = max(700, int(screen_h * 0.74))
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(0, (screen_h - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.root.minsize(int(850 * self.scale * 0.75), int(580 * self.scale * 0.75))

        # Hàng đợi nhận sự kiện từ luồng Server
        self.event_queue = queue.Queue()

        # Database Manager
        self.db = DatabaseManager("ftp_server.db")

        # Đối tượng FTP Server
        self.server: Optional[FTPServer] = None

        # Thiết lập style giao diện
        self._setup_styles()

        # Khởi tạo các thành phần giao diện
        self._build_ui()

        # Bắt đầu vòng lặp đọc sự kiện từ Server
        self.root.after(100, self._process_events)

    def _setup_styles(self) -> None:
        """Cấu hình màu sắc và font chữ cho ttk widgets sắc nét, chuẩn giao diện."""
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        import tkinter.font as tkfont
        available_fonts = tkfont.families()
        if "Ubuntu Sans" in available_fonts:
            font_family = "Ubuntu Sans"
        elif "Ubuntu" in available_fonts:
            font_family = "Ubuntu"
        elif "DejaVu Sans" in available_fonts:
            font_family = "DejaVu Sans"
        else:
            font_family = "sans-serif"

        self.font_base = (font_family, 10)
        self.font_bold = (font_family, 10, "bold")
        self.font_header = (font_family, 12, "bold")
        self.font_status = (font_family, 10, "bold")
        self.font_log = ("DejaVu Sans Mono" if "DejaVu Sans Mono" in available_fonts else "monospace", 9)
        self.row_height = 28

        self.style.configure(".", font=self.font_base)
        self.style.configure("Header.TLabel", font=self.font_header)
        self.style.configure("Status.TLabel", font=self.font_status)
        self.style.configure("Treeview.Heading", font=self.font_bold, padding=(6, 6))
        self.style.configure("Treeview", rowheight=self.row_height, font=self.font_base)

    def _build_ui(self) -> None:
        """Xây dựng bố cục tổng thể của ứng dụng."""
        # 1. Thanh trạng thái trên cùng (Top Banner)
        top_frame = ttk.Frame(self.root, padding="10")
        top_frame.pack(fill=tk.X, side=tk.TOP)

        self.lbl_title = ttk.Label(
            top_frame, text="MÁY CHỦ FTP (RFC 959)", style="Header.TLabel", foreground="#1a5276"
        )
        self.lbl_title.pack(side=tk.LEFT)

        self.lbl_server_status = ttk.Label(
            top_frame,
            text="TRẠNG THÁI: ĐANG DỪNG",
            style="Status.TLabel",
            foreground="#c0392b",
            padding=(15, 0),
        )
        self.lbl_server_status.pack(side=tk.RIGHT)

        # 2. Hệ thống Tabs (Notebook)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Khởi tạo các Tab
        self.tab_control = ttk.Frame(self.notebook, padding="10")
        self.tab_users = ttk.Frame(self.notebook, padding="10")
        self.tab_clients = ttk.Frame(self.notebook, padding="10")
        self.tab_logs = ttk.Frame(self.notebook, padding="10")
        self.tab_stats = ttk.Frame(self.notebook, padding="10")

        self.notebook.add(self.tab_control, text=" Điều khiển & Cấu hình ")
        self.notebook.add(self.tab_users, text=" Quản lý người dùng ")
        self.notebook.add(self.tab_clients, text=" Đang kết nối ")
        self.notebook.add(self.tab_logs, text=" Nhật ký & Lịch sử ")
        self.notebook.add(self.tab_stats, text=" Thống kê & Báo cáo ")

        self._build_control_tab()
        self._build_users_tab()
        self._build_clients_tab()
        self._build_logs_tab()
        self._build_stats_tab()

    # ==================== TAB 1: ĐIỀU KHIỂN & CẤU HÌNH ====================

    def _build_control_tab(self) -> None:
        """Xây dựng Tab điều khiển Start/Stop và cấu hình tham số."""
        container = ttk.Frame(self.tab_control)
        container.pack(fill=tk.BOTH, expand=True)

        cfg_frame = ttk.LabelFrame(container, text="Cấu hình Mạng & Giao thức", padding="15")
        cfg_frame.pack(fill=tk.X, pady=10)

        # Host IP
        ttk.Label(cfg_frame, text="Địa chỉ IP lắng nghe:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.ent_host = ttk.Entry(cfg_frame, width=20)
        self.ent_host.insert(0, "0.0.0.0")
        self.ent_host.grid(row=0, column=1, sticky=tk.W, padx=10, pady=5)
        ttk.Label(cfg_frame, text="(0.0.0.0 lắng nghe mọi card mạng hoặc 127.0.0.1)").grid(
            row=0, column=2, sticky=tk.W
        )

        # Cổng Control (mặc định 2121)
        ttk.Label(cfg_frame, text="Cổng kết nối (Control Port):").grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        self.ent_port = ttk.Entry(cfg_frame, width=20)
        self.ent_port.insert(0, "2121")
        self.ent_port.grid(row=1, column=1, sticky=tk.W, padx=10, pady=5)
        ttk.Label(cfg_frame, text="(Cổng 2121 không cần root; cổng 21 yêu cầu sudo)").grid(
            row=1, column=2, sticky=tk.W
        )

        # Thư mục gốc mặc định
        ttk.Label(cfg_frame, text="Thư mục gốc lưu trữ (Root Dir):").grid(
            row=2, column=0, sticky=tk.W, pady=5
        )
        dir_subframe = ttk.Frame(cfg_frame)
        dir_subframe.grid(row=2, column=1, columnspan=2, sticky=tk.W, padx=10, pady=5)

        self.ent_root_dir = ttk.Entry(dir_subframe, width=35)
        self.ent_root_dir.insert(0, os.path.abspath("./ftp_root"))
        self.ent_root_dir.pack(side=tk.LEFT)

        btn_browse = ttk.Button(dir_subframe, text="Duyệt...", command=self._browse_root_dir)
        btn_browse.pack(side=tk.LEFT, padx=5)

        # Dải cổng PASV
        ttk.Label(cfg_frame, text="Dải cổng Passive Mode:").grid(row=3, column=0, sticky=tk.W, pady=5)
        pasv_subframe = ttk.Frame(cfg_frame)
        pasv_subframe.grid(row=3, column=1, sticky=tk.W, padx=10, pady=5)
        self.ent_pasv_min = ttk.Entry(pasv_subframe, width=8)
        self.ent_pasv_min.insert(0, "50000")
        self.ent_pasv_min.pack(side=tk.LEFT)
        ttk.Label(pasv_subframe, text=" đến ").pack(side=tk.LEFT)
        self.ent_pasv_max = ttk.Entry(pasv_subframe, width=8)
        self.ent_pasv_max.insert(0, "50100")
        self.ent_pasv_max.pack(side=tk.LEFT)

        # Tùy chọn bảo mật & Anonymous
        opts_frame = ttk.Frame(cfg_frame)
        opts_frame.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=10)

        self.var_anon = tk.BooleanVar(value=True)
        self.chk_anon = ttk.Checkbutton(
            opts_frame, text="Cho phép truy cập Anonymous (Đọc công khai)", variable=self.var_anon
        )
        self.chk_anon.pack(side=tk.LEFT, padx=(0, 20))

        self.var_tls = tk.BooleanVar(value=False)
        self.chk_tls = ttk.Checkbutton(
            opts_frame,
            text="Bật mã hóa FTPS (TLS/SSL qua lệnh AUTH TLS)",
            variable=self.var_tls,
        )
        self.chk_tls.pack(side=tk.LEFT)

        # Khung nút thao tác Start / Stop
        btn_action_frame = ttk.Frame(container, padding="15")
        btn_action_frame.pack(fill=tk.X)

        self.btn_start = tk.Button(
            btn_action_frame,
            text="▶ KHỞI ĐỘNG SERVER",
            bg="#27ae60",
            fg="white",
            font=self.font_bold,
            padx=int(18 * self.scale),
            pady=int(6 * self.scale),
            command=self.start_server,
        )
        self.btn_start.pack(side=tk.LEFT, padx=10)

        self.btn_stop = tk.Button(
            btn_action_frame,
            text="⏹ DỪNG SERVER",
            bg="#c0392b",
            fg="white",
            font=self.font_bold,
            padx=int(18 * self.scale),
            pady=int(6 * self.scale),
            state=tk.DISABLED,
            command=self.stop_server,
        )
        self.btn_stop.pack(side=tk.LEFT, padx=10)

        # Thống kê nhanh
        quick_stats = ttk.LabelFrame(container, text="Thông tin phiên hoạt động", padding="15")
        quick_stats.pack(fill=tk.BOTH, expand=True, pady=10)

        self.lbl_stat_clients = ttk.Label(
            quick_stats, text="Số client đang online: 0", font=self.font_base
        )
        self.lbl_stat_clients.pack(anchor=tk.W, pady=5)

        self.lbl_stat_uptime = ttk.Label(
            quick_stats, text="Địa chỉ phục vụ: Chưa khởi động", font=self.font_base
        )
        self.lbl_stat_uptime.pack(anchor=tk.W, pady=5)

    def _browse_root_dir(self) -> None:
        """Mở hộp thoại chọn thư mục gốc."""
        path = filedialog.askdirectory(initialdir=self.ent_root_dir.get())
        if path:
            self.ent_root_dir.delete(0, tk.END)
            self.ent_root_dir.insert(0, path)

    def start_server(self) -> None:
        """Bắt đầu chạy server."""
        try:
            host = self.ent_host.get().strip() or "0.0.0.0"
            port = int(self.ent_port.get().strip())
            root_dir = self.ent_root_dir.get().strip()
            pasv_min = int(self.ent_pasv_min.get().strip())
            pasv_max = int(self.ent_pasv_max.get().strip())
            allow_anon = self.var_anon.get()
            enable_tls = self.var_tls.get()

            os.makedirs(root_dir, exist_ok=True)

            self.server = FTPServer(
                host=host,
                port=port,
                root_dir=root_dir,
                db_path="ftp_server.db",
                pasv_ports=(pasv_min, pasv_max),
                allow_anonymous=allow_anon,
                enable_tls=enable_tls,
                event_queue=self.event_queue,
            )

            if self.server.start():
                self.btn_start.config(state=tk.DISABLED)
                self.btn_stop.config(state=tk.NORMAL)
                self.lbl_server_status.config(
                    text=f"TRẠNG THÁI: ĐANG CHẠY (PORT {port})", foreground="#27ae60"
                )
                self.lbl_stat_uptime.config(text=f"Địa chỉ phục vụ: {host}:{port}")
                self.ent_host.config(state=tk.DISABLED)
                self.ent_port.config(state=tk.DISABLED)
                self.chk_tls.config(state=tk.DISABLED)
        except ValueError:
            messagebox.showerror("Lỗi nhập liệu", "Cổng và dải port PASV phải là số nguyên!")
        except Exception as e:
            messagebox.showerror("Lỗi khởi động", f"Không thể bật server: {e}")

    def stop_server(self) -> None:
        """Dừng server."""
        if self.server:
            self.server.stop()
            self.server = None

        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_server_status.config(text="TRẠNG THÁI: ĐANG DỪNG", foreground="#c0392b")
        self.lbl_stat_uptime.config(text="Địa chỉ phục vụ: Chưa khởi động")
        self.lbl_stat_clients.config(text="Số client đang online: 0")
        self.ent_host.config(state=tk.NORMAL)
        self.ent_port.config(state=tk.NORMAL)
        self.chk_tls.config(state=tk.NORMAL)

    # ==================== TAB 2: QUẢN LÝ NGƯỜI DÙNG ====================

    def _build_users_tab(self) -> None:
        """Xây dựng Tab quản lý người dùng và phân quyền từ Database."""
        toolbar = ttk.Frame(self.tab_users)
        toolbar.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(toolbar, text="➕ Thêm User", command=self._dialog_add_user).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(toolbar, text="✏ Chỉnh sửa", command=self._dialog_edit_user).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(toolbar, text="🔑 Đổi mật khẩu", command=self._dialog_change_password).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(toolbar, text="🗑 Xóa User", command=self._delete_user).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(toolbar, text="🔄 Làm mới", command=self.load_users_table).pack(
            side=tk.RIGHT, padx=3
        )

        cols = (
            "id",
            "username",
            "home_dir",
            "read",
            "write",
            "delete",
            "active",
            "quota",
            "max_conn",
            "speed",
        )
        self.tree_users = ttk.Treeview(self.tab_users, columns=cols, show="headings", selectmode="browse")

        self.tree_users.heading("id", text="ID")
        self.tree_users.heading("username", text="Username")
        self.tree_users.heading("home_dir", text="Thư mục gốc (Home)")
        self.tree_users.heading("read", text="Đọc (R)")
        self.tree_users.heading("write", text="Ghi (W)")
        self.tree_users.heading("delete", text="Xóa (D)")
        self.tree_users.heading("active", text="Kích hoạt")
        self.tree_users.heading("quota", text="Quota (MB)")
        self.tree_users.heading("max_conn", text="Max Kết nối")
        self.tree_users.heading("speed", text="Giới hạn (KB/s)")

        self.tree_users.column("id", width=int(50 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("username", width=int(120 * self.scale), anchor=tk.W)
        self.tree_users.column("home_dir", width=int(240 * self.scale), anchor=tk.W)
        self.tree_users.column("read", width=int(75 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("write", width=int(75 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("delete", width=int(75 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("active", width=int(85 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("quota", width=int(95 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("max_conn", width=int(95 * self.scale), anchor=tk.CENTER)
        self.tree_users.column("speed", width=int(110 * self.scale), anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(self.tab_users, orient=tk.VERTICAL, command=self.tree_users.yview)
        self.tree_users.configure(yscrollcommand=scrollbar.set)

        self.tree_users.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)

        self.load_users_table()

    def load_users_table(self) -> None:
        """Đọc và hiển thị danh sách người dùng từ SQLite database."""
        for item in self.tree_users.get_children():
            self.tree_users.delete(item)

        users = self.db.list_users()
        for u in users:
            quota_mb = (
                f"{u['quota_bytes'] // (1024*1024)}" if u["quota_bytes"] > 0 else "Vô hạn"
            )
            speed_str = f"{u['speed_limit_kb']} KB/s" if u["speed_limit_kb"] > 0 else "Không"
            self.tree_users.insert(
                "",
                tk.END,
                values=(
                    u["id"],
                    u["username"],
                    u["home_dir"],
                    "✔" if u["can_read"] else "✖",
                    "✔" if u["can_write"] else "✖",
                    "✔" if u["can_delete"] else "✖",
                    "Bật" if u["is_active"] else "Khóa",
                    quota_mb,
                    u["max_connections"],
                    speed_str,
                ),
            )

    def _dialog_add_user(self) -> None:
        """Mở cửa sổ thêm user mới."""
        win = tk.Toplevel(self.root)
        win.title("Thêm Người Dùng Mới")
        win.geometry(f"{int(540 * self.scale)}x{int(450 * self.scale)}")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frm = ttk.Frame(win, padding="20")
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Tên tài khoản:").grid(row=0, column=0, sticky=tk.W, pady=6)
        ent_user = ttk.Entry(frm, width=30)
        ent_user.grid(row=0, column=1, pady=6, sticky=tk.W)

        ttk.Label(frm, text="Mật khẩu:").grid(row=1, column=0, sticky=tk.W, pady=6)
        ent_pass = ttk.Entry(frm, width=30, show="*")
        ent_pass.grid(row=1, column=1, pady=6, sticky=tk.W)

        ttk.Label(frm, text="Thư mục gốc (Home):").grid(row=2, column=0, sticky=tk.W, pady=6)
        home_sub = ttk.Frame(frm)
        home_sub.grid(row=2, column=1, pady=6, sticky=tk.W)
        ent_home = ttk.Entry(home_sub, width=22)
        ent_home.insert(0, os.path.abspath("./ftp_root"))
        ent_home.pack(side=tk.LEFT)

        def pick_home():
            p = filedialog.askdirectory(initialdir=ent_home.get())
            if p:
                ent_home.delete(0, tk.END)
                ent_home.insert(0, p)

        ttk.Button(home_sub, text="...", width=3, command=pick_home).pack(side=tk.LEFT, padx=3)

        # Phân quyền
        ttk.Label(frm, text="Quyền hạn:").grid(row=3, column=0, sticky=tk.W, pady=6)
        perm_sub = ttk.Frame(frm)
        perm_sub.grid(row=3, column=1, pady=6, sticky=tk.W)
        v_read = tk.BooleanVar(value=True)
        v_write = tk.BooleanVar(value=True)
        v_del = tk.BooleanVar(value=True)
        ttk.Checkbutton(perm_sub, text="Đọc", variable=v_read).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(perm_sub, text="Ghi", variable=v_write).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(perm_sub, text="Xóa", variable=v_del).pack(side=tk.LEFT, padx=5)

        # Quota MB
        ttk.Label(frm, text="Hạn mức Quota (MB):").grid(row=4, column=0, sticky=tk.W, pady=6)
        ent_quota = ttk.Entry(frm, width=15)
        ent_quota.insert(0, "0")
        ent_quota.grid(row=4, column=1, sticky=tk.W, pady=6)
        ttk.Label(frm, text="(0 = Không giới hạn)").grid(row=4, column=1, sticky=tk.E)

        # Max connections
        ttk.Label(frm, text="Số kết nối tối đa:").grid(row=5, column=0, sticky=tk.W, pady=6)
        ent_maxconn = ttk.Entry(frm, width=15)
        ent_maxconn.insert(0, "5")
        ent_maxconn.grid(row=5, column=1, sticky=tk.W, pady=6)

        # Giới hạn tốc độ KB/s
        ttk.Label(frm, text="Tốc độ tối đa (KB/s):").grid(row=6, column=0, sticky=tk.W, pady=6)
        ent_speed = ttk.Entry(frm, width=15)
        ent_speed.insert(0, "0")
        ent_speed.grid(row=6, column=1, sticky=tk.W, pady=6)

        def save():
            uname = ent_user.get().strip()
            pwd = ent_pass.get()
            home = ent_home.get().strip()
            if not uname:
                messagebox.showwarning("Thiếu dữ liệu", "Username không được để trống!", parent=win)
                return
            if uname.lower() != "anonymous" and not pwd:
                messagebox.showwarning("Thiếu dữ liệu", "Vui lòng nhập mật khẩu!", parent=win)
                return

            try:
                quota_b = int(ent_quota.get().strip()) * 1024 * 1024
                max_c = int(ent_maxconn.get().strip())
                spd = int(ent_speed.get().strip())
            except ValueError:
                messagebox.showwarning("Lỗi số liệu", "Quota, Max Conn và Tốc độ phải là số!", parent=win)
                return

            if os.path.basename(home) != uname:
                home = os.path.join(home, uname)

            ok = self.db.add_user(
                username=uname,
                password=pwd,
                home_dir=home,
                can_read=1 if v_read.get() else 0,
                can_write=1 if v_write.get() else 0,
                can_delete=1 if v_del.get() else 0,
                is_active=1,
                quota_bytes=quota_b,
                max_connections=max_c,
                speed_limit_kb=spd,
            )

            if ok:
                messagebox.showinfo("Thành công", f"Đã thêm người dùng {uname} thành công!", parent=win)
                win.destroy()
                self.load_users_table()
            else:
                messagebox.showerror("Thất bại", f"Tài khoản '{uname}' đã tồn tại trong database!", parent=win)

        ttk.Button(frm, text="Lưu người dùng", command=save).grid(
            row=7, column=1, sticky=tk.E, pady=15
        )

    def _dialog_edit_user(self) -> None:
        """Chỉnh sửa thông tin và quyền hạn của user được chọn."""
        sel = self.tree_users.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn một người dùng để chỉnh sửa!")
            return

        values = self.tree_users.item(sel[0], "values")
        user_id = int(values[0])
        user = self.db.get_user(values[1])
        if not user:
            return

        win = tk.Toplevel(self.root)
        win.title(f"Chỉnh Sửa User: {user['username']}")
        win.geometry(f"{int(540 * self.scale)}x{int(430 * self.scale)}")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frm = ttk.Frame(win, padding="20")
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Tài khoản: {user['username']}", font=self.font_header).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=5
        )

        ttk.Label(frm, text="Thư mục gốc (Home):").grid(row=1, column=0, sticky=tk.W, pady=6)
        home_sub = ttk.Frame(frm)
        home_sub.grid(row=1, column=1, pady=6, sticky=tk.W)
        ent_home = ttk.Entry(home_sub, width=22)
        ent_home.insert(0, user["home_dir"])
        ent_home.pack(side=tk.LEFT)

        def pick_home():
            p = filedialog.askdirectory(initialdir=ent_home.get())
            if p:
                ent_home.delete(0, tk.END)
                ent_home.insert(0, p)

        ttk.Button(home_sub, text="...", width=3, command=pick_home).pack(side=tk.LEFT, padx=3)

        # Quyền hạn
        ttk.Label(frm, text="Quyền hạn:").grid(row=2, column=0, sticky=tk.W, pady=6)
        perm_sub = ttk.Frame(frm)
        perm_sub.grid(row=2, column=1, pady=6, sticky=tk.W)
        v_read = tk.BooleanVar(value=bool(user["can_read"]))
        v_write = tk.BooleanVar(value=bool(user["can_write"]))
        v_del = tk.BooleanVar(value=bool(user["can_delete"]))
        ttk.Checkbutton(perm_sub, text="Đọc", variable=v_read).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(perm_sub, text="Ghi", variable=v_write).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(perm_sub, text="Xóa", variable=v_del).pack(side=tk.LEFT, padx=5)

        # Trạng thái active
        ttk.Label(frm, text="Trạng thái tài khoản:").grid(row=3, column=0, sticky=tk.W, pady=6)
        v_act = tk.BooleanVar(value=bool(user["is_active"]))
        ttk.Checkbutton(frm, text="Đang hoạt động (Active)", variable=v_act).grid(
            row=3, column=1, sticky=tk.W, pady=6
        )

        # Quota
        ttk.Label(frm, text="Quota (MB):").grid(row=4, column=0, sticky=tk.W, pady=6)
        ent_quota = ttk.Entry(frm, width=15)
        ent_quota.insert(0, str(user["quota_bytes"] // (1024 * 1024)))
        ent_quota.grid(row=4, column=1, sticky=tk.W, pady=6)

        # Max conn
        ttk.Label(frm, text="Max Kết nối:").grid(row=5, column=0, sticky=tk.W, pady=6)
        ent_maxconn = ttk.Entry(frm, width=15)
        ent_maxconn.insert(0, str(user["max_connections"]))
        ent_maxconn.grid(row=5, column=1, sticky=tk.W, pady=6)

        # Speed limit
        ttk.Label(frm, text="Tốc độ tối đa (KB/s):").grid(row=6, column=0, sticky=tk.W, pady=6)
        ent_speed = ttk.Entry(frm, width=15)
        ent_speed.insert(0, str(user["speed_limit_kb"]))
        ent_speed.grid(row=6, column=1, sticky=tk.W, pady=6)

        def save():
            try:
                quota_b = int(ent_quota.get().strip()) * 1024 * 1024
                max_c = int(ent_maxconn.get().strip())
                spd = int(ent_speed.get().strip())
            except ValueError:
                messagebox.showwarning("Lỗi", "Số liệu không hợp lệ!", parent=win)
                return

            ok = self.db.update_user(
                user_id=user_id,
                home_dir=ent_home.get().strip(),
                can_read=1 if v_read.get() else 0,
                can_write=1 if v_write.get() else 0,
                can_delete=1 if v_del.get() else 0,
                is_active=1 if v_act.get() else 0,
                quota_bytes=quota_b,
                max_connections=max_c,
                speed_limit_kb=spd,
            )
            if ok:
                messagebox.showinfo("Thành công", "Cập nhật thành công!", parent=win)
                win.destroy()
                self.load_users_table()
            else:
                messagebox.showerror("Lỗi", "Không thể cập nhật người dùng!", parent=win)

        ttk.Button(frm, text="Lưu Thay Đổi", command=save).grid(
            row=7, column=1, sticky=tk.E, pady=15
        )

    def _dialog_change_password(self) -> None:
        """Đổi mật khẩu cho user."""
        sel = self.tree_users.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn user cần đổi mật khẩu!")
            return

        values = self.tree_users.item(sel[0], "values")
        user_id = int(values[0])
        uname = values[1]

        win = tk.Toplevel(self.root)
        win.title(f"Đổi mật khẩu: {uname}")
        win.geometry(f"{int(400 * self.scale)}x{int(200 * self.scale)}")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frm = ttk.Frame(win, padding="20")
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Mật khẩu mới:").grid(row=0, column=0, sticky=tk.W, pady=10)
        ent_p1 = ttk.Entry(frm, width=22, show="*")
        ent_p1.grid(row=0, column=1, pady=10)

        ttk.Label(frm, text="Xác nhận lại:").grid(row=1, column=0, sticky=tk.W, pady=10)
        ent_p2 = ttk.Entry(frm, width=22, show="*")
        ent_p2.grid(row=1, column=1, pady=10)

        def save():
            p1 = ent_p1.get()
            p2 = ent_p2.get()
            if not p1:
                messagebox.showwarning("Lỗi", "Mật khẩu không được rỗng!", parent=win)
                return
            if p1 != p2:
                messagebox.showwarning("Lỗi", "Mật khẩu xác nhận không khớp!", parent=win)
                return

            if self.db.update_password(user_id, p1):
                messagebox.showinfo("Thành công", f"Đã đổi mật khẩu cho {uname}!", parent=win)
                win.destroy()
            else:
                messagebox.showerror("Lỗi", "Không thể đổi mật khẩu!", parent=win)

        ttk.Button(frm, text="Đổi Mật Khẩu", command=save).grid(row=2, column=1, sticky=tk.E, pady=10)

    def _delete_user(self) -> None:
        """Xóa user được chọn khỏi SQLite."""
        sel = self.tree_users.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn user cần xóa!")
            return

        values = self.tree_users.item(sel[0], "values")
        user_id = int(values[0])
        uname = values[1]

        if uname.lower() == "admin":
            messagebox.showerror("Cấm xóa", "Không thể xóa tài khoản quản trị mặc định 'admin'!")
            return

        if messagebox.askyesno("Xác nhận xóa", f"Bạn có chắc muốn xóa tài khoản '{uname}'?"):
            if self.db.delete_user(user_id):
                messagebox.showinfo("Đã xóa", f"Tài khoản '{uname}' đã được xóa!")
                self.load_users_table()
            else:
                messagebox.showerror("Lỗi", "Không thể xóa user khỏi database!")

    # ==================== TAB 3: ĐANG KẾT NỐI ====================

    def _build_clients_tab(self) -> None:
        """Xây dựng Tab danh sách client online và nút ngắt kết nối."""
        toolbar = ttk.Frame(self.tab_clients)
        toolbar.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(
            toolbar, text="❌ Ngắt kết nối Client", command=self._disconnect_selected_client
        ).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="🔄 Làm mới", command=self._refresh_clients_table).pack(
            side=tk.RIGHT, padx=3
        )

        cols = ("id", "ip", "port", "user", "dir", "status")
        self.tree_clients = ttk.Treeview(
            self.tab_clients, columns=cols, show="headings", selectmode="browse"
        )

        self.tree_clients.heading("id", text="Client ID")
        self.tree_clients.heading("ip", text="Địa chỉ IP")
        self.tree_clients.heading("port", text="Cổng")
        self.tree_clients.heading("user", text="Tài khoản")
        self.tree_clients.heading("dir", text="Thư mục hiện tại")
        self.tree_clients.heading("status", text="Trạng thái")

        self.tree_clients.column("id", width=int(80 * self.scale), anchor=tk.CENTER)
        self.tree_clients.column("ip", width=int(150 * self.scale), anchor=tk.CENTER)
        self.tree_clients.column("port", width=int(90 * self.scale), anchor=tk.CENTER)
        self.tree_clients.column("user", width=int(150 * self.scale), anchor=tk.W)
        self.tree_clients.column("dir", width=int(280 * self.scale), stretch=True, anchor=tk.W)
        self.tree_clients.column("status", width=int(130 * self.scale), anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(
            self.tab_clients, orient=tk.VERTICAL, command=self.tree_clients.yview
        )
        self.tree_clients.configure(yscrollcommand=scrollbar.set)

        self.tree_clients.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)

    def _refresh_clients_table(self) -> None:
        """Cập nhật bảng client online từ server."""
        for item in self.tree_clients.get_children():
            self.tree_clients.delete(item)

        if not self.server:
            return

        with self.server.sessions_lock:
            for cid, s in self.server.active_sessions.items():
                status_str = "Đã xác thực" if s.is_authenticated else "Đang xác thực"
                self.tree_clients.insert(
                    "",
                    tk.END,
                    values=(
                        cid,
                        s.client_address[0],
                        s.client_address[1],
                        s.username,
                        s.current_directory,
                        status_str,
                    ),
                )
        self.lbl_stat_clients.config(
            text=f"Số client đang online: {len(self.server.active_sessions)}"
        )

    def _disconnect_selected_client(self) -> None:
        """Buộc ngắt kết nối của client được chọn."""
        sel = self.tree_clients.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn một client để ngắt kết nối!")
            return

        values = self.tree_clients.item(sel[0], "values")
        client_id = int(values[0])
        ip = values[1]

        if messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn ngắt kết nối client ID {client_id} ({ip})?"):
            if self.server and self.server.disconnect_client(client_id):
                messagebox.showinfo("Đã ngắt", f"Đã ngắt kết nối client {client_id}!")
                self._refresh_clients_table()
            else:
                messagebox.showerror("Lỗi", "Không thể ngắt kết nối client này!")

    # ==================== TAB 4: NHẬT KÝ & LỊCH SỬ ====================

    def _build_logs_tab(self) -> None:
        """Xây dựng Tab hiển thị log thời gian thực và lịch sử truyền file từ database."""
        paned = ttk.PanedWindow(self.tab_logs, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Nửa trên: Real-time Command Log
        top_group = ttk.LabelFrame(paned, text="Nhật ký Lệnh Thời Gian Thực (Live FTP Logs)", padding="5")
        paned.add(top_group, weight=1)

        log_toolbar = ttk.Frame(top_group)
        log_toolbar.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(log_toolbar, text="Xóa nhật ký", command=self._clear_logs).pack(
            side=tk.LEFT, padx=3
        )
        self.var_autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_toolbar, text="Tự động cuộn xuống", variable=self.var_autoscroll).pack(
            side=tk.LEFT, padx=10
        )

        self.txt_log = scrolledtext.ScrolledText(
            top_group, height=8, font=self.font_log, wrap=tk.WORD, bg="#2c3e50", fg="#ecf0f1"
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        self.txt_log.tag_config("INFO", foreground="#3498db")
        self.txt_log.tag_config("SUCCESS", foreground="#2ecc71")
        self.txt_log.tag_config("WARN", foreground="#f39c12")
        self.txt_log.tag_config("ERROR", foreground="#e74c3c")
        self.txt_log.tag_config("CMD", foreground="#f1c40f")

        # Nửa dưới: Transfer History từ Database
        bot_group = ttk.LabelFrame(
            paned, text="Lịch Sử Truyền File (Lấy từ SQLite Table: transfers)", padding="5"
        )
        paned.add(bot_group, weight=1)

        trans_toolbar = ttk.Frame(bot_group)
        trans_toolbar.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(trans_toolbar, text="🔄 Tải lại Lịch sử", command=self.load_transfer_history).pack(
            side=tk.RIGHT, padx=3
        )

        cols = ("id", "user", "file", "dir", "size", "bytes", "status", "start", "end")
        self.tree_transfers = ttk.Treeview(
            bot_group, columns=cols, show="headings", selectmode="browse"
        )

        self.tree_transfers.heading("id", text="ID")
        self.tree_transfers.heading("user", text="Tài khoản")
        self.tree_transfers.heading("file", text="Tên File")
        self.tree_transfers.heading("dir", text="Chiều")
        self.tree_transfers.heading("size", text="Kích thước")
        self.tree_transfers.heading("bytes", text="Đã truyền")
        self.tree_transfers.heading("status", text="Trạng thái")
        self.tree_transfers.heading("start", text="Thời gian bắt đầu")
        self.tree_transfers.heading("end", text="Thời gian xong")

        self.tree_transfers.column("id", width=int(50 * self.scale), anchor=tk.CENTER)
        self.tree_transfers.column("user", width=int(100 * self.scale), anchor=tk.W)
        self.tree_transfers.column("file", width=int(180 * self.scale), stretch=True, anchor=tk.W)
        self.tree_transfers.column("dir", width=int(85 * self.scale), anchor=tk.CENTER)
        self.tree_transfers.column("size", width=int(95 * self.scale), anchor=tk.E)
        self.tree_transfers.column("bytes", width=int(95 * self.scale), anchor=tk.E)
        self.tree_transfers.column("status", width=int(85 * self.scale), anchor=tk.CENTER)
        self.tree_transfers.column("start", width=int(140 * self.scale), anchor=tk.CENTER)
        self.tree_transfers.column("end", width=int(140 * self.scale), anchor=tk.CENTER)

        scrollbar_tr = ttk.Scrollbar(
            bot_group, orient=tk.VERTICAL, command=self.tree_transfers.yview
        )
        self.tree_transfers.configure(yscrollcommand=scrollbar_tr.set)

        self.tree_transfers.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        scrollbar_tr.pack(fill=tk.Y, side=tk.RIGHT)

        self.load_transfer_history()

    def _clear_logs(self) -> None:
        """Xóa màn hình log."""
        self.txt_log.delete("1.0", tk.END)

    def append_log(self, text: str, tag: str = "INFO") -> None:
        """Thêm dòng log mới vào khung ScrolledText với màu sắc tương ứng."""
        self.txt_log.insert(tk.END, text + "\n", tag)
        if self.var_autoscroll.get():
            self.txt_log.see(tk.END)

    def load_transfer_history(self) -> None:
        """Đọc danh sách lịch sử truyền file từ database."""
        for item in self.tree_transfers.get_children():
            self.tree_transfers.delete(item)

        rows = self.db.get_recent_transfers(limit=100)
        for r in rows:
            self.tree_transfers.insert(
                "",
                tk.END,
                values=(
                    r["id"],
                    r["username"],
                    r["filename"],
                    r["direction"],
                    f"{r['file_size']:,} B",
                    f"{r['bytes_transferred']:,} B",
                    r["status"],
                    r["start_time"],
                    r["end_time"],
                ),
            )

    # ==================== TAB 5: THỐNG KÊ (BIỂU ĐỒ 7 NGÀY & TOP 5) ====================

    def _build_stats_tab(self) -> None:
        """
        Xây dựng Tab thống kê quản trị:
        - Nửa trên: Biểu đồ cột số lượt Upload & Download theo ngày (7 ngày gần nhất) vẽ bằng Canvas.
        - Nửa dưới: Top 5 tệp được tải nhiều nhất và Top 5 người dùng truyền nhiều nhất.
        """
        toolbar = ttk.Frame(self.tab_stats)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Button(toolbar, text="🔄 Cập nhật thống kê", command=self.draw_statistics).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Label(
            toolbar,
            text="Báo cáo số lượt truyền 7 ngày gần nhất và Xếp hạng Top 5 (Tệp & Người dùng)",
            font=("Segoe UI", 10, "italic"),
        ).pack(side=tk.LEFT, padx=15)

        main_paned = ttk.PanedWindow(self.tab_stats, orient=tk.VERTICAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # 1. Khung biểu đồ 7 ngày
        chart_frame = ttk.LabelFrame(main_paned, text="Biểu Đồ Số Lượt Truyền 7 Ngày Gần Nhất", padding="5")
        main_paned.add(chart_frame, weight=3)

        self.canvas_stats = tk.Canvas(chart_frame, bg="#ffffff", highlightthickness=1)
        self.canvas_stats.pack(fill=tk.BOTH, expand=True)

        # 2. Khung Top 5 (chia đôi ngang)
        tables_frame = ttk.Frame(main_paned)
        main_paned.add(tables_frame, weight=2)

        tables_paned = ttk.PanedWindow(tables_frame, orient=tk.HORIZONTAL)
        tables_paned.pack(fill=tk.BOTH, expand=True)

        # Bảng Top 5 tệp tải nhiều nhất
        f_top_files = ttk.LabelFrame(tables_paned, text="Top 5 Tệp Tải Về Nhiều Nhất", padding="5")
        tables_paned.add(f_top_files, weight=1)

        cols_f = ("rank", "name", "count", "bytes")
        self.tree_top_files = ttk.Treeview(f_top_files, columns=cols_f, show="headings", height=5)
        self.tree_top_files.heading("rank", text="#")
        self.tree_top_files.heading("name", text="Tên Tệp")
        self.tree_top_files.heading("count", text="Số Lượt")
        self.tree_top_files.heading("bytes", text="Tổng Tải")

        self.tree_top_files.column("rank", width=int(35 * self.scale), anchor=tk.CENTER)
        self.tree_top_files.column("name", width=int(180 * self.scale), stretch=True, anchor=tk.W)
        self.tree_top_files.column("count", width=int(75 * self.scale), anchor=tk.CENTER)
        self.tree_top_files.column("bytes", width=int(95 * self.scale), anchor=tk.E)
        self.tree_top_files.pack(fill=tk.BOTH, expand=True)

        # Bảng Top 5 người dùng truyền nhiều nhất
        f_top_users = ttk.LabelFrame(tables_paned, text="Top 5 Người Dùng Truyền Nhiều Nhất", padding="5")
        tables_paned.add(f_top_users, weight=1)

        cols_u = ("rank", "user", "bytes", "count")
        self.tree_top_users = ttk.Treeview(f_top_users, columns=cols_u, show="headings", height=5)
        self.tree_top_users.heading("rank", text="#")
        self.tree_top_users.heading("user", text="Tài Khoản")
        self.tree_top_users.heading("bytes", text="Tổng Lưu Lượng")
        self.tree_top_users.heading("count", text="Số Lần")

        self.tree_top_users.column("rank", width=int(35 * self.scale), anchor=tk.CENTER)
        self.tree_top_users.column("user", width=int(150 * self.scale), stretch=True, anchor=tk.W)
        self.tree_top_users.column("bytes", width=int(110 * self.scale), anchor=tk.E)
        self.tree_top_users.column("count", width=int(75 * self.scale), anchor=tk.CENTER)
        self.tree_top_users.pack(fill=tk.BOTH, expand=True)

        self.draw_statistics()

    @staticmethod
    def _format_bytes(b: int) -> str:
        """Định dạng byte sang B, KB, MB, GB."""
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB"
        else:
            return f"{b / (1024 * 1024 * 1024):.2f} GB"

    def draw_statistics(self) -> None:
        """Vẽ biểu đồ cột 7 ngày và nạp dữ liệu vào bảng Top 5."""
        self.canvas_stats.delete("all")

        # 1. Nạp bảng Top 5 Tệp
        for item in self.tree_top_files.get_children():
            self.tree_top_files.delete(item)
        top_files = self.db.get_top_5_downloaded_files()
        for idx, row in enumerate(top_files, 1):
            self.tree_top_files.insert(
                "",
                tk.END,
                values=(
                    idx,
                    row["filename"],
                    row["download_count"],
                    self._format_bytes(row["total_bytes"] or 0),
                ),
            )

        # 2. Nạp bảng Top 5 Users
        for item in self.tree_top_users.get_children():
            self.tree_top_users.delete(item)
        top_users = self.db.get_top_5_active_users()
        for idx, row in enumerate(top_users, 1):
            self.tree_top_users.insert(
                "",
                tk.END,
                values=(
                    idx,
                    row["username"],
                    self._format_bytes(row["total_bytes"] or 0),
                    row["total_transfers"],
                ),
            )

        # 3. Vẽ biểu đồ số lượt truyền 7 ngày gần nhất
        days_data = self.db.get_transfers_last_7_days()
        w = self.canvas_stats.winfo_width() or 800
        h = self.canvas_stats.winfo_height() or 260

        title_font = ("Segoe UI", max(10, int(11 * self.scale)), "bold")
        legend_font = ("Segoe UI", max(9, int(9.5 * self.scale)))
        val_font = ("Segoe UI", max(8, int(8.5 * self.scale)))

        self.canvas_stats.create_text(
            w // 2, int(16 * self.scale), text="SỐ LƯỢT UPLOAD & DOWNLOAD THEO NGÀY (7 NGÀY GẦN NHẤT)", font=title_font, fill="#2c3e50"
        )

        # Chú thích
        lw = int(220 * self.scale)
        self.canvas_stats.create_rectangle(w - lw, int(8 * self.scale), w - lw + int(16 * self.scale), int(22 * self.scale), fill="#2980b9", outline="")
        self.canvas_stats.create_text(w - lw + int(55 * self.scale), int(15 * self.scale), text="Upload", font=legend_font)
        self.canvas_stats.create_rectangle(w - int(100 * self.scale), int(8 * self.scale), w - int(84 * self.scale), int(22 * self.scale), fill="#27ae60", outline="")
        self.canvas_stats.create_text(w - int(45 * self.scale), int(15 * self.scale), text="Download", font=legend_font)

        # Tìm giá trị cao nhất
        max_val = 1
        for d in days_data:
            max_val = max(max_val, d["upload_count"], d["download_count"])

        margin_left = int(60 * self.scale)
        margin_bottom = int(45 * self.scale)
        margin_top = int(35 * self.scale)
        chart_h = h - margin_bottom - margin_top
        chart_w = w - margin_left - int(30 * self.scale)

        if chart_h <= 10 or chart_w <= 10:
            return

        # Trục tọa độ
        self.canvas_stats.create_line(
            margin_left, h - margin_bottom, w - int(30 * self.scale), h - margin_bottom, width=2, fill="#7f8c8d"
        )
        self.canvas_stats.create_line(
            margin_left, margin_top, margin_left, h - margin_bottom, width=2, fill="#7f8c8d"
        )

        # Vạch trục Y
        y_steps = 4
        for i in range(y_steps + 1):
            val = int(round((max_val / y_steps) * i))
            y = (h - margin_bottom) - (i / y_steps) * chart_h
            self.canvas_stats.create_line(margin_left - 4, y, margin_left, y, fill="#7f8c8d")
            self.canvas_stats.create_text(
                margin_left - 8, y, text=str(val), anchor=tk.E, font=val_font
            )
            if i > 0:
                self.canvas_stats.create_line(
                    margin_left, y, w - int(30 * self.scale), y, dash=(2, 4), fill="#ecf0f1"
                )

        # Vẽ từng ngày
        num_days = len(days_data)
        slot_w = chart_w / max(1, num_days)
        bar_w = min(int(28 * self.scale), slot_w / 3.0)

        for i, d in enumerate(days_data):
            date_label = d["date"][5:]  # lấy MM-DD
            up_cnt = d["upload_count"]
            down_cnt = d["download_count"]

            center_x = margin_left + i * slot_w + slot_w / 2

            # Cột Upload (Xanh dương)
            up_h = (up_cnt / max_val) * chart_h
            x1 = center_x - bar_w - 2
            y1 = (h - margin_bottom) - up_h
            x2 = center_x - 2
            y2 = h - margin_bottom
            self.canvas_stats.create_rectangle(x1, y1, x2, y2, fill="#2980b9", outline="")
            if up_cnt > 0:
                self.canvas_stats.create_text(
                    (x1 + x2) / 2, y1 - 8, text=str(up_cnt), font=val_font
                )

            # Cột Download (Xanh lá)
            down_h = (down_cnt / max_val) * chart_h
            x3 = center_x + 2
            y3 = (h - margin_bottom) - down_h
            x4 = center_x + bar_w + 2
            y4 = h - margin_bottom
            self.canvas_stats.create_rectangle(x3, y3, x4, y4, fill="#27ae60", outline="")
            if down_cnt > 0:
                self.canvas_stats.create_text(
                    (x3 + x4) / 2, y3 - 8, text=str(down_cnt), font=val_font
                )

            # Nhãn ngày tháng
            self.canvas_stats.create_text(
                center_x,
                h - margin_bottom + int(14 * self.scale),
                text=date_label,
                font=val_font,
                fill="#2c3e50",
            )

    # ==================== VÒNG LẶP XỬ LÝ SỰ KIỆN TỪ SERVER (EVENT QUEUE) ====================

    def _process_events(self) -> None:
        """Đọc và xử lý các thông báo từ Server đưa vào queue mà không làm treo GUI."""
        try:
            while not self.event_queue.empty():
                evt = self.event_queue.get_nowait()
                etype = evt.get("type")
                data = evt.get("data", {})

                if etype == "SERVER_STARTED":
                    self.append_log(
                        f"▶ Máy chủ FTP đã khởi động tại {data['host']}:{data['port']}", "SUCCESS"
                    )
                elif etype == "SERVER_STOPPED":
                    self.append_log("⏹ Máy chủ FTP đã dừng.", "WARN")
                elif etype == "SERVER_ERROR":
                    self.append_log(f"✖ LỖI MÁY CHỦ: {data.get('error')}", "ERROR")
                elif etype == "CLIENT_CONNECTED":
                    self.append_log(
                        f"[+] Client ID {data['client_id']} kết nối từ {data['ip']}:{data['port']}",
                        "INFO",
                    )
                    self._refresh_clients_table()
                elif etype == "CLIENT_LOGGED_IN":
                    self.append_log(
                        f"[✔] Client ID {data['client_id']} đã đăng nhập: user='{data['user']}', root='{data['home_dir']}'",
                        "SUCCESS",
                    )
                    self._refresh_clients_table()
                elif etype == "CLIENT_DISCONNECTED":
                    self.append_log(
                        f"[-] Client ID {data['client_id']} ({data['ip']}) đã ngắt kết nối.", "WARN"
                    )
                    self._refresh_clients_table()
                    self.load_transfer_history()
                elif etype == "COMMAND_RECEIVED":
                    c_id = data.get("client_id", "?")
                    u = data.get("user", "?")
                    cmd = data.get("command", "")
                    arg = data.get("arg", "")
                    arg_str = f" {arg}" if arg else ""
                    self.append_log(f"[{c_id}][{u}] -> {cmd}{arg_str}", "CMD")

        except Exception:
            pass
        finally:
            self.root.after(100, self._process_events)
