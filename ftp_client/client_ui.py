"""
Giao diện FTP Client Nâng Cao Hiện Đại (Tkinter GUI - Phong cách FileZilla Chuyên Nghiệp).
Khắc phục triệt để:
- Không bị mất chữ / tràn chữ ở các cột Kích thước, Ngày sửa, Loại.
- Cột Ngày sửa (225px) hiển thị trọn vẹn YYYY-MM-DD HH:MM.
- Cột Kích thước (175px) căn phải chuẩn, hiển thị đủ tiêu đề "Kích thước ▲" và dung lượng.
- Cột Loại (115px) hiển thị trọn vẹn "Thư mục" / "Tệp".
- Hỗ trợ cuộn ngang (Horizontal Scrollbar) chống co cắt chữ khi thu nhỏ cửa sổ.
- Biểu tượng trực quan 📁 Thư mục và 📄 Tệp tin.
- Giao diện hiện đại, phối màu phẳng cao cấp, zebra-striping (sọc xen kẽ dễ nhìn).
- Hàng đợi truyền đa luồng với thanh tiến độ, đo tốc độ trượt 1.5-2s, ETA, tạm dừng, tiếp tục.
- Site Manager và Tab Lịch sử & Dung lượng Quota.
"""

import os
import sys
import time
import datetime
import threading
import queue
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, simpledialog, scrolledtext
from typing import Optional, List, Dict, Any, Tuple

from .client import CustomFTPClient, FTPClientError
from .client_db import ClientDatabaseManager


def format_size(size_bytes: int) -> str:
    """Định dạng dung lượng byte thành B, KB, MB, GB dễ đọc."""
    if size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_eta(seconds: int) -> str:
    """Định dạng số giây còn lại thành MM:SS hoặc HH:MM:SS."""
    if seconds <= 0:
        return "--:--"
    if seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m:02d}:{s:02d}"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_item_name(name_str: str) -> str:
    """Loại bỏ biểu tượng icon 📁 hoặc 📄 ở đầu tên tệp/thư mục."""
    for prefix in ("📁 ", "📄 ", "📁", "📄"):
        if name_str.startswith(prefix):
            return name_str[len(prefix):]
    return name_str


class ResumeChoiceDialog(tk.Toplevel):
    """Hộp thoại hỏi người dùng khi tệp đã tồn tại một phần: Tiếp tục / Ghi đè / Bỏ qua."""

    def __init__(self, parent: tk.Tk, filename: str, existing_sz: int, total_sz: int):
        super().__init__(parent)
        self.title("Xung Đột Tệp Truyền")
        self.result: Optional[str] = None  # "RESUME", "OVERWRITE", "SKIP"
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        msg = (
            f"Tệp đích '{filename}' đã tồn tại một phần:\n\n"
            f"• Dung lượng hiện có: {format_size(existing_sz)} ({existing_sz:,} B)\n"
            f"• Kích thước tệp nguồn: {format_size(total_sz)} ({total_sz:,} B)\n\n"
            f"Bạn muốn xử lý tệp này như thế nào?"
        )

        ttk.Label(self, text=msg, padding=14, justify=tk.LEFT).pack(fill=tk.BOTH, expand=True)

        btn_box = ttk.Frame(self, padding=10)
        btn_box.pack(fill=tk.X)

        ttk.Button(btn_box, text="▶ Tiếp tục (REST)", command=self._on_resume).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_box, text="🔄 Ghi đè", command=self._on_overwrite).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(btn_box, text="✕ Bỏ qua", command=self._on_skip).pack(
            side=tk.RIGHT, padx=4
        )

        self.protocol("WM_DELETE_WINDOW", self._on_skip)
        self.wait_window(self)

    def _on_resume(self):
        self.result = "RESUME"
        self.destroy()

    def _on_overwrite(self):
        self.result = "OVERWRITE"
        self.destroy()

    def _on_skip(self):
        self.result = "SKIP"
        self.destroy()


class ClientUI:
    """Cửa sổ ứng dụng FTP Client phong cách FileZilla hiện đại, đẹp mắt và sắc nét."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FTP Client - Giao diện FileZilla Hiện Đại (Lab 2 Mạng Máy Tính)")

        # Kích thước cửa sổ chuẩn, rộng rãi trên mọi độ phân giải (hỗ trợ 1080p, 2K, 4K)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        win_w = min(1560, max(1360, int(screen_w * 0.72)))
        win_h = min(960, max(800, int(screen_h * 0.78)))
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(0, (screen_h - win_h) // 2)
        self.root.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.root.minsize(1180, 700)

        # Database quản lý Site & Lịch sử
        self.db = ClientDatabaseManager()

        # Hàng đợi giao tiếp luồng với GUI
        self.gui_queue = queue.Queue()

        # FTP Client chính dùng cho duyệt file và thao tác lệnh
        self.client = CustomFTPClient(log_callback=self._on_client_log)

        # Trạng thái thư mục
        self.local_current_dir = os.path.abspath(os.path.expanduser("~"))
        self.remote_current_dir = "/"

        # Bộ nhớ đệm danh sách tệp phục vụ lọc nhanh và sắp xếp
        self.local_items_cache: List[Dict[str, Any]] = []
        self.remote_items_cache: List[Dict[str, Any]] = []

        # Trạng thái sắp xếp cột
        self.local_sort_col = "name"
        self.local_sort_rev = False
        self.remote_sort_col = "name"
        self.remote_sort_rev = False

        # Quản lý Hàng đợi truyền file đa luồng
        self.transfer_tasks: Dict[str, Dict[str, Any]] = {}
        self.transfer_queue: queue.Queue = queue.Queue()
        self.max_workers = 2
        self.worker_threads: List[threading.Thread] = []
        self._start_transfer_workers()

        # Biến hỗ trợ Kéo - Thả (Drag & Drop)
        self._drag_data: Optional[Dict[str, Any]] = None

        # Khởi tạo style và giao diện
        self._setup_styles()
        self._build_ui()

        # Nạp danh sách site đã lưu vào combobox
        self._refresh_saved_sites_dropdown()

        # Nạp danh sách file cục bộ ban đầu
        self.refresh_local_files()

        # Vòng lặp cập nhật GUI định kỳ
        self.root.after(100, self._process_gui_queue)

    def _setup_styles(self) -> None:
        """Cấu hình phong cách đồ họa hiện đại, sang trọng, tương phản cao."""
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Lựa chọn font chữ đẹp và chuẩn nhất trên Linux/Ubuntu
        available_fonts = tkfont.families()
        if "Ubuntu Sans" in available_fonts:
            font_family = "Ubuntu Sans"
        elif "Ubuntu" in available_fonts:
            font_family = "Ubuntu"
        elif "DejaVu Sans" in available_fonts:
            font_family = "DejaVu Sans"
        else:
            font_family = "sans-serif"

        # Định cỡ font chuẩn không bị phóng to gấp đôi
        self.font_base = (font_family, 10)
        self.font_bold = (font_family, 10, "bold")
        self.font_header = (font_family, 11, "bold")
        self.font_title = (font_family, 12, "bold")
        self.font_log = ("DejaVu Sans Mono" if "DejaVu Sans Mono" in available_fonts else "monospace", 9)
        self.row_height = 28

        # Cấu hình chung cho ttk
        self.style.configure(".", font=self.font_base)
        self.style.configure("Header.TLabel", font=self.font_header, foreground="#1e293b")
        self.style.configure("Title.TLabel", font=self.font_title, foreground="#0f172a")

        # Cấu hình Treeview hiện đại
        self.style.configure(
            "Treeview",
            background="#ffffff",
            foreground="#1e293b",
            fieldbackground="#ffffff",
            rowheight=self.row_height,
            font=self.font_base,
            borderwidth=1,
            relief="solid",
        )
        self.style.configure(
            "Treeview.Heading",
            background="#f1f5f9",
            foreground="#0f172a",
            font=self.font_bold,
            relief="flat",
            padding=(6, 6),
        )
        self.style.map(
            "Treeview.Heading",
            background=[("active", "#e2e8f0"), ("pressed", "#cbd5e1")],
        )
        self.style.map(
            "Treeview",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )

        # Style cho Notebook (Tabs)
        self.style.configure("TNotebook", background="#f8fafc", borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            font=self.font_bold,
            padding=(14, 6),
            background="#e2e8f0",
            foreground="#334155",
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", "#ffffff"), ("active", "#f1f5f9")],
            foreground=[("selected", "#1d4ed8"), ("active", "#0f172a")],
        )

        # Style cho Progressbar
        self.style.configure(
            "Horizontal.TProgressbar",
            troughcolor="#e2e8f0",
            background="#2563eb",
            thickness=16,
        )

    def _build_ui(self) -> None:
        """Xây dựng bố cục giao diện tổng thể."""
        # 1. Thanh kết nối nhanh & Site Manager
        self._build_quick_connect_bar()

        # 2. Khung Log thông điệp FTP
        self._build_log_frame()

        # 3. Khung chia đôi duyệt file (Dual Panes: Local vs Remote)
        self._build_dual_file_panes()

        # 4. Tab dưới cùng: Hàng đợi truyền file & Lịch sử/Dung lượng
        self._build_bottom_tabs()

    # ==================== 1. THANH KẾT NỐI NHANH ====================

    def _build_quick_connect_bar(self) -> None:
        """Thanh kết nối nhanh trên cùng tích hợp chọn Site đã lưu."""
        bar = ttk.LabelFrame(self.root, text=" ⚡ Kết Nối Nhanh & Quản Lý Site ", padding="8")
        bar.pack(fill=tk.X, padx=10, pady=(6, 3))

        # Hàng trên: Thông tin kết nối
        top_row = ttk.Frame(bar)
        top_row.pack(fill=tk.X, pady=(0, 4))

        # Chọn site đã lưu
        ttk.Label(top_row, text="📁 Site:").pack(side=tk.LEFT, padx=(2, 2))
        self.cbo_saved_sites = ttk.Combobox(top_row, width=15, state="readonly")
        self.cbo_saved_sites.pack(side=tk.LEFT, padx=2)
        self.cbo_saved_sites.bind("<<ComboboxSelected>>", self._on_saved_site_selected)

        ttk.Button(top_row, text="⚙ Quản Lý Sites...", command=self._open_site_manager).pack(
            side=tk.LEFT, padx=(3, 10)
        )

        # Host
        ttk.Label(top_row, text="Host:").pack(side=tk.LEFT, padx=(4, 2))
        self.ent_host = ttk.Entry(top_row, width=14)
        self.ent_host.insert(0, "127.0.0.1")
        self.ent_host.pack(side=tk.LEFT, padx=2)

        # Port
        ttk.Label(top_row, text="Port:").pack(side=tk.LEFT, padx=(4, 2))
        self.ent_port = ttk.Entry(top_row, width=6)
        self.ent_port.insert(0, "2121")
        self.ent_port.pack(side=tk.LEFT, padx=2)

        # Username
        ttk.Label(top_row, text="User:").pack(side=tk.LEFT, padx=(4, 2))
        self.ent_user = ttk.Entry(top_row, width=12)
        self.ent_user.insert(0, "admin")
        self.ent_user.pack(side=tk.LEFT, padx=2)

        # Password
        ttk.Label(top_row, text="Pass:").pack(side=tk.LEFT, padx=(4, 2))
        self.ent_pass = ttk.Entry(top_row, width=12, show="*")
        self.ent_pass.insert(0, "123456")
        self.ent_pass.pack(side=tk.LEFT, padx=2)

        # Chế độ PASV / PORT
        ttk.Label(top_row, text="Mode:").pack(side=tk.LEFT, padx=(6, 2))
        self.cbo_mode = ttk.Combobox(
            top_row, values=["PASV (Passive)", "PORT (Active)"], width=14, state="readonly"
        )
        self.cbo_mode.current(0)
        self.cbo_mode.pack(side=tk.LEFT, padx=2)

        # TLS Checkbox
        self.var_tls = tk.BooleanVar(value=False)
        self.chk_tls = ttk.Checkbutton(top_row, text="TLS", variable=self.var_tls)
        self.chk_tls.pack(side=tk.LEFT, padx=6)

        # Nút Kết nối / Ngắt kết nối thiết kế hiện đại
        self.btn_connect = tk.Button(
            top_row,
            text="⚡ Kết Nối",
            bg="#2563eb",
            activebackground="#1d4ed8",
            fg="white",
            activeforeground="white",
            font=self.font_bold,
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2",
            command=self.action_connect,
        )
        self.btn_connect.pack(side=tk.LEFT, padx=4)

        self.btn_disconnect = tk.Button(
            top_row,
            text="⏹ Ngắt Kết Nối",
            bg="#dc2626",
            activebackground="#b91c1c",
            fg="white",
            activeforeground="white",
            font=self.font_bold,
            relief="flat",
            state=tk.DISABLED,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self.action_disconnect,
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=2)

        # Hàng dưới: Trạng thái kết nối dạng huy hiệu (Pill Badge)
        bot_row = ttk.Frame(bar)
        bot_row.pack(fill=tk.X, pady=(2, 0))

        self.lbl_conn_status = tk.Label(
            bot_row,
            text="⚪ CHƯA KẾT NỐI",
            bg="#f1f5f9",
            fg="#64748b",
            font=self.font_bold,
            padx=8,
            pady=2,
            relief="solid",
            borderwidth=1,
        )
        self.lbl_conn_status.pack(side=tk.LEFT)

    def _refresh_saved_sites_dropdown(self) -> None:
        """Cập nhật danh sách các site trong Combobox."""
        sites = self.db.get_sites()
        names = [s["name"] for s in sites]
        self.cbo_saved_sites["values"] = names
        if names:
            self.cbo_saved_sites.current(0)

    def _on_saved_site_selected(self, event) -> None:
        """Người dùng chọn site từ Combobox -> Tự động điền form."""
        name = self.cbo_saved_sites.get()
        sites = self.db.get_sites()
        for s in sites:
            if s["name"] == name:
                self.ent_host.delete(0, tk.END)
                self.ent_host.insert(0, s["host"])
                self.ent_port.delete(0, tk.END)
                self.ent_port.insert(0, str(s["port"]))
                self.ent_user.delete(0, tk.END)
                self.ent_user.insert(0, s["username"])
                self.ent_pass.delete(0, tk.END)
                self.ent_pass.insert(0, s["password"])
                idx = 0 if s["mode"] == "PASV" else 1
                self.cbo_mode.current(idx)
                self.var_tls.set(bool(s["use_tls"]))
                break

    # ==================== 2. KHUNG LOG THÔNG ĐIỆP ====================

    def _build_log_frame(self) -> None:
        """Khung hiển thị thông điệp FTP thời gian thực dạng Dark Slate Terminal."""
        log_frame = ttk.LabelFrame(self.root, text=" 📜 Nhật Ký Thông Điệp Kênh Điều Khiển (RFC 959) ", padding="4")
        log_frame.pack(fill=tk.X, padx=10, pady=2)

        self.txt_log = scrolledtext.ScrolledText(
            log_frame,
            height=4,
            font=self.font_log,
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#ffffff",
            wrap=tk.WORD,
            relief="flat",
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        self.txt_log.tag_config("CMD", foreground="#38bdf8")       # Sky blue
        self.txt_log.tag_config("SUCCESS", foreground="#4ade80")   # Emerald green
        self.txt_log.tag_config("INFO", foreground="#60a5fa")      # Blue
        self.txt_log.tag_config("WARN", foreground="#fbbf24")      # Amber
        self.txt_log.tag_config("ERROR", foreground="#f87171")     # Rose red

    def _on_client_log(self, message: str, tag: str) -> None:
        """Callback ghi log an toàn vào hàng đợi GUI."""
        self.gui_queue.put({"action": "LOG", "text": message, "tag": tag})

    # ==================== 3. KHUNG DUAL-PANE FILE BROWSER ====================

    def _build_dual_file_panes(self) -> None:
        """Khung chia đôi chứa máy cục bộ (Local) và máy chủ (Remote)."""
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        # Khung Local
        local_frame = ttk.LabelFrame(paned, text=" 💻 Máy Cục Bộ (Local Site) ", padding="6")
        paned.add(local_frame, weight=1)
        self._setup_local_browser(local_frame)

        # Khung Remote
        remote_frame = ttk.LabelFrame(paned, text=" ☁ Máy Chủ FTP (Remote Site) ", padding="6")
        paned.add(remote_frame, weight=1)
        self._setup_remote_browser(remote_frame)

    def _setup_local_browser(self, parent: ttk.Frame) -> None:
        """Thiết lập cây thư mục máy cục bộ với ô lọc nhanh và cột co giãn."""
        # 1. Thanh điều hướng đường dẫn
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 3))

        ttk.Button(nav, text="⬆ Lên", width=5, command=self._local_go_up).pack(side=tk.LEFT, padx=1)
        ttk.Button(nav, text="🏠 Home", width=6, command=self._local_go_home).pack(side=tk.LEFT, padx=1)

        self.ent_local_path = ttk.Entry(nav)
        self.ent_local_path.insert(0, self.local_current_dir)
        self.ent_local_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.ent_local_path.bind("<Return>", lambda e: self._on_local_path_enter())

        ttk.Button(nav, text="🔄 Làm Mới", width=9, command=self.refresh_local_files).pack(
            side=tk.RIGHT, padx=1
        )

        # 2. Thanh lọc nhanh
        filter_bar = ttk.Frame(parent)
        filter_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_bar, text="🔍 Lọc nhanh:").pack(side=tk.LEFT, padx=(2, 3))
        self.ent_local_filter = ttk.Entry(filter_bar)
        self.ent_local_filter.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.ent_local_filter.bind("<KeyRelease>", lambda e: self._filter_local_tree())
        ttk.Button(filter_bar, text="✕", width=3, command=self._clear_local_filter).pack(
            side=tk.RIGHT, padx=1
        )

        # 3. Khung chứa bảng Treeview và thanh cuộn kép (Dọc + Ngang)
        tree_container = ttk.Frame(parent)
        tree_container.pack(fill=tk.BOTH, expand=True)

        cols = ("name", "size", "mtime", "type")
        self.tree_local = ttk.Treeview(tree_container, columns=cols, show="headings", selectmode="extended")

        self.tree_local.heading("name", text="Tên", command=lambda: self._sort_local_column("name"))
        self.tree_local.heading("size", text="Kích thước", command=lambda: self._sort_local_column("size"))
        self.tree_local.heading("mtime", text="Ngày sửa", command=lambda: self._sort_local_column("mtime"))
        self.tree_local.heading("type", text="Loại", command=lambda: self._sort_local_column("type"))

        # Độ rộng chuẩn: đảm bảo KHÔNG BAO GIỜ bị cắt chữ
        self.tree_local.column("name", width=240, minwidth=160, stretch=True, anchor=tk.W)
        self.tree_local.column("size", width=175, minwidth=130, stretch=False, anchor=tk.E)
        self.tree_local.column("mtime", width=225, minwidth=190, stretch=False, anchor=tk.CENTER)
        self.tree_local.column("type", width=115, minwidth=90, stretch=False, anchor=tk.CENTER)

        # Thanh cuộn dọc
        sb_y_local = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree_local.yview)
        # Thanh cuộn ngang chống tràn
        sb_x_local = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.tree_local.xview)

        self.tree_local.configure(yscrollcommand=sb_y_local.set, xscrollcommand=sb_x_local.set)

        self.tree_local.grid(row=0, column=0, sticky="nsew")
        sb_y_local.grid(row=0, column=1, sticky="ns")
        sb_x_local.grid(row=1, column=0, sticky="ew")

        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        # Cấu hình màu xen kẽ zebra
        self.tree_local.tag_configure("even", background="#ffffff")
        self.tree_local.tag_configure("odd", background="#f8fafc")

        # Sự kiện: Double-click, Chuột phải, Kéo thả
        self.tree_local.bind("<Double-1>", self._on_local_double_click)
        self.tree_local.bind("<Button-3>", self._show_local_context_menu)

        # Kéo thả (Drag & Drop)
        self.tree_local.bind("<ButtonPress-1>", self._on_local_drag_start)
        self.tree_local.bind("<B1-Motion>", self._on_drag_motion)
        self.tree_local.bind("<ButtonRelease-1>", self._on_local_drag_release)

        # Menu ngữ cảnh chuột phải Local
        self.menu_local = tk.Menu(self.root, tearoff=0)
        self.menu_local.add_command(label="⬆ Tải lên Server (Upload)", command=self.action_upload_selected)
        self.menu_local.add_command(label="➕ Thêm vào hàng đợi", command=self.action_queue_upload_selected)
        self.menu_local.add_separator()
        self.menu_local.add_command(label="📁 Tạo thư mục mới", command=self._local_mkdir)
        self.menu_local.add_command(label="✏ Đổi tên", command=self._local_rename)
        self.menu_local.add_command(label="🗑 Xóa", command=self._local_delete)
        self.menu_local.add_separator()
        self.menu_local.add_command(label="ℹ Xem thuộc tính", command=self._show_local_properties)
        self.menu_local.add_command(label="🔄 Làm mới", command=self.refresh_local_files)

    def _setup_remote_browser(self, parent: ttk.Frame) -> None:
        """Thiết lập cây thư mục máy chủ với ô lọc nhanh và cột co giãn."""
        # 1. Thanh điều hướng đường dẫn
        nav = ttk.Frame(parent)
        nav.pack(fill=tk.X, pady=(0, 3))

        ttk.Button(nav, text="⬆ Lên", width=5, command=self._remote_go_up).pack(side=tk.LEFT, padx=1)
        ttk.Button(nav, text="🏠 Root", width=6, command=self._remote_go_root).pack(side=tk.LEFT, padx=1)

        self.ent_remote_path = ttk.Entry(nav)
        self.ent_remote_path.insert(0, "/")
        self.ent_remote_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        self.ent_remote_path.bind("<Return>", lambda e: self._on_remote_path_enter())

        ttk.Button(nav, text="🔄 Làm Mới", width=9, command=self.refresh_remote_files).pack(
            side=tk.RIGHT, padx=1
        )

        # 2. Thanh lọc nhanh
        filter_bar = ttk.Frame(parent)
        filter_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_bar, text="🔍 Lọc nhanh:").pack(side=tk.LEFT, padx=(2, 3))
        self.ent_remote_filter = ttk.Entry(filter_bar)
        self.ent_remote_filter.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.ent_remote_filter.bind("<KeyRelease>", lambda e: self._filter_remote_tree())
        ttk.Button(filter_bar, text="✕", width=3, command=self._clear_remote_filter).pack(
            side=tk.RIGHT, padx=1
        )

        # 3. Khung chứa bảng Treeview và thanh cuộn kép (Dọc + Ngang)
        tree_container = ttk.Frame(parent)
        tree_container.pack(fill=tk.BOTH, expand=True)

        cols = ("name", "size", "mtime", "type")
        self.tree_remote = ttk.Treeview(tree_container, columns=cols, show="headings", selectmode="extended")

        self.tree_remote.heading("name", text="Tên", command=lambda: self._sort_remote_column("name"))
        self.tree_remote.heading("size", text="Kích thước", command=lambda: self._sort_remote_column("size"))
        self.tree_remote.heading("mtime", text="Ngày sửa", command=lambda: self._sort_remote_column("mtime"))
        self.tree_remote.heading("type", text="Loại", command=lambda: self._sort_remote_column("type"))

        # Độ rộng chuẩn: đảm bảo KHÔNG BAO GIỜ bị cắt chữ
        self.tree_remote.column("name", width=240, minwidth=160, stretch=True, anchor=tk.W)
        self.tree_remote.column("size", width=175, minwidth=130, stretch=False, anchor=tk.E)
        self.tree_remote.column("mtime", width=225, minwidth=190, stretch=False, anchor=tk.CENTER)
        self.tree_remote.column("type", width=115, minwidth=90, stretch=False, anchor=tk.CENTER)

        sb_y_remote = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.tree_remote.yview)
        sb_x_remote = ttk.Scrollbar(tree_container, orient=tk.HORIZONTAL, command=self.tree_remote.xview)

        self.tree_remote.configure(yscrollcommand=sb_y_remote.set, xscrollcommand=sb_x_remote.set)

        self.tree_remote.grid(row=0, column=0, sticky="nsew")
        sb_y_remote.grid(row=0, column=1, sticky="ns")
        sb_x_remote.grid(row=1, column=0, sticky="ew")

        tree_container.grid_rowconfigure(0, weight=1)
        tree_container.grid_columnconfigure(0, weight=1)

        self.tree_remote.tag_configure("even", background="#ffffff")
        self.tree_remote.tag_configure("odd", background="#f8fafc")

        # Sự kiện: Double-click, Chuột phải, Kéo thả
        self.tree_remote.bind("<Double-1>", self._on_remote_double_click)
        self.tree_remote.bind("<Button-3>", self._show_remote_context_menu)

        # Kéo thả (Drag & Drop)
        self.tree_remote.bind("<ButtonPress-1>", self._on_remote_drag_start)
        self.tree_remote.bind("<B1-Motion>", self._on_drag_motion)
        self.tree_remote.bind("<ButtonRelease-1>", self._on_remote_drag_release)

        # Menu ngữ cảnh chuột phải Remote
        self.menu_remote = tk.Menu(self.root, tearoff=0)
        self.menu_remote.add_command(label="⬇ Tải về máy (Download)", command=self.action_download_selected)
        self.menu_remote.add_command(label="➕ Thêm vào hàng đợi", command=self.action_queue_download_selected)
        self.menu_remote.add_separator()
        self.menu_remote.add_command(label="📁 Tạo thư mục mới", command=self._remote_mkdir)
        self.menu_remote.add_command(label="✏ Đổi tên (RNFR/RNTO)", command=self._remote_rename)
        self.menu_remote.add_command(label="🗑 Xóa (DELE/RMD)", command=self._remote_delete)
        self.menu_remote.add_separator()
        self.menu_remote.add_command(label="ℹ Xem thuộc tính & SHA-256", command=self._show_remote_properties)
        self.menu_remote.add_command(label="🔄 Làm mới", command=self.refresh_remote_files)

    # ==================== 4. TAB DƯỚI CÙNG (HÀNG ĐỢI & LỊCH SỬ / DUNG LƯỢNG) ====================

    def _build_bottom_tabs(self) -> None:
        """Tab dưới cùng gồm Hàng đợi truyền file (Tab 1) và Lịch sử & Dung lượng (Tab 2)."""
        self.nb_bottom = ttk.Notebook(self.root)
        self.nb_bottom.pack(fill=tk.BOTH, expand=False, padx=10, pady=(2, 6))

        # Tab 1: Hàng đợi truyền file
        tab_queue = ttk.Frame(self.nb_bottom, padding="6")
        self.nb_bottom.add(tab_queue, text=" 📥 Hàng Đợi Truyền File ")
        self._build_transfer_queue_content(tab_queue)

        # Tab 2: Lịch sử & Dung lượng
        tab_history = ttk.Frame(self.nb_bottom, padding="6")
        self.nb_bottom.add(tab_history, text=" 📊 Lịch Sử & Dung Lượng ")
        self._build_history_quota_content(tab_history)

    def _build_transfer_queue_content(self, parent: ttk.Frame) -> None:
        """Nội dung Tab Hàng đợi truyền file kèm nút điều khiển và thông số chi tiết."""
        # Thanh điều khiển hàng đợi
        ctrl_bar = ttk.Frame(parent)
        ctrl_bar.pack(fill=tk.X, pady=(0, 4))

        self.lbl_transfer_status = tk.Label(
            ctrl_bar,
            text="Trạng thái: Sẵn sàng",
            font=self.font_bold,
            fg="#1e293b",
        )
        self.lbl_transfer_status.pack(side=tk.LEFT, padx=4)

        ttk.Button(ctrl_bar, text="⏸ Tạm dừng", command=self._queue_pause_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl_bar, text="▶ Tiếp tục", command=self._queue_resume_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl_bar, text="⏹ Hủy", command=self._queue_cancel_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl_bar, text="🔄 Thử lại", command=self._queue_retry_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(ctrl_bar, text="🧹 Dọn mục xong", command=self._queue_clear_completed).pack(side=tk.LEFT, padx=4)

        self.progressbar = ttk.Progressbar(
            ctrl_bar, orient=tk.HORIZONTAL, mode="determinate", style="Horizontal.TProgressbar"
        )
        self.progressbar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=8)

        # Bảng hàng đợi
        q_container = ttk.Frame(parent)
        q_container.pack(fill=tk.BOTH, expand=True)

        cols = ("file", "dir", "size", "progress", "speed", "eta", "status")
        self.tree_queue = ttk.Treeview(q_container, columns=cols, show="headings", height=4, selectmode="browse")
        self.tree_queue.heading("file", text="Tên File")
        self.tree_queue.heading("dir", text="Chiều")
        self.tree_queue.heading("size", text="Kích thước")
        self.tree_queue.heading("progress", text="Tiến độ")
        self.tree_queue.heading("speed", text="Tốc độ (1-2s)")
        self.tree_queue.heading("eta", text="Còn lại (ETA)")
        self.tree_queue.heading("status", text="Trạng thái")

        self.tree_queue.column("file", width=280, stretch=True, anchor=tk.W)
        self.tree_queue.column("dir", width=110, stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("size", width=120, stretch=False, anchor=tk.E)
        self.tree_queue.column("progress", width=100, stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("speed", width=130, stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("eta", width=100, stretch=False, anchor=tk.CENTER)
        self.tree_queue.column("status", width=130, stretch=False, anchor=tk.CENTER)

        sb_y_q = ttk.Scrollbar(q_container, orient=tk.VERTICAL, command=self.tree_queue.yview)
        sb_x_q = ttk.Scrollbar(q_container, orient=tk.HORIZONTAL, command=self.tree_queue.xview)

        self.tree_queue.configure(yscrollcommand=sb_y_q.set, xscrollcommand=sb_x_q.set)

        self.tree_queue.grid(row=0, column=0, sticky="nsew")
        sb_y_q.grid(row=0, column=1, sticky="ns")
        sb_x_q.grid(row=1, column=0, sticky="ew")

        q_container.grid_rowconfigure(0, weight=1)
        q_container.grid_columnconfigure(0, weight=1)

    def _build_history_quota_content(self, parent: ttk.Frame) -> None:
        """Nội dung Tab Lịch sử & Dung lượng (Quota Progressbar & History Table)."""
        # Khung Quota trên cùng
        quota_box = ttk.LabelFrame(parent, text=" 💾 Dung Lượng Tài Khoản (Server Quota) ", padding="8")
        quota_box.pack(fill=tk.X, pady=(0, 4))

        self.lbl_quota_text = ttk.Label(
            quota_box, text="Đã dùng: -- MB / -- MB (0% đã sử dụng)", font=self.font_bold
        )
        self.lbl_quota_text.pack(side=tk.LEFT, padx=6)

        ttk.Button(quota_box, text="🔄 Cập Nhật Quota", command=self.refresh_quota_info).pack(
            side=tk.RIGHT, padx=4
        )

        self.pbar_quota = ttk.Progressbar(
            quota_box, orient=tk.HORIZONTAL, mode="determinate", style="Horizontal.TProgressbar"
        )
        self.pbar_quota.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=10)

        # Khung Lịch sử truyền file
        hist_box = ttk.LabelFrame(parent, text=" 📜 Lịch Sử Truyền File Gần Đây (Database SQLite) ", padding="6")
        hist_box.pack(fill=tk.BOTH, expand=True)

        hist_nav = ttk.Frame(hist_box)
        hist_nav.pack(fill=tk.X, pady=(0, 3))
        ttk.Button(hist_nav, text="🔄 Tải Lại Lịch Sử", command=self.refresh_history_info).pack(
            side=tk.LEFT, padx=2
        )

        h_container = ttk.Frame(hist_box)
        h_container.pack(fill=tk.BOTH, expand=True)

        cols = ("file", "direction", "size", "time", "status")
        self.tree_history = ttk.Treeview(h_container, columns=cols, show="headings", height=4)
        self.tree_history.heading("file", text="Tên Tệp")
        self.tree_history.heading("direction", text="Chiều")
        self.tree_history.heading("size", text="Kích Thước")
        self.tree_history.heading("time", text="Thời Gian Bắt Đầu")
        self.tree_history.heading("status", text="Trạng Thái")

        self.tree_history.column("file", width=280, stretch=True, anchor=tk.W)
        self.tree_history.column("direction", width=110, stretch=False, anchor=tk.CENTER)
        self.tree_history.column("size", width=120, stretch=False, anchor=tk.E)
        self.tree_history.column("time", width=190, stretch=False, anchor=tk.CENTER)
        self.tree_history.column("status", width=120, stretch=False, anchor=tk.CENTER)

        sb_y_h = ttk.Scrollbar(h_container, orient=tk.VERTICAL, command=self.tree_history.yview)
        sb_x_h = ttk.Scrollbar(h_container, orient=tk.HORIZONTAL, command=self.tree_history.xview)

        self.tree_history.configure(yscrollcommand=sb_y_h.set, xscrollcommand=sb_x_h.set)

        self.tree_history.grid(row=0, column=0, sticky="nsew")
        sb_y_h.grid(row=0, column=1, sticky="ns")
        sb_x_h.grid(row=1, column=0, sticky="ew")

        h_container.grid_rowconfigure(0, weight=1)
        h_container.grid_columnconfigure(0, weight=1)

    # ==================== SẮP XẾP CỘT & LỌC NHANH ====================

    def _render_local_tree(self, items: List[Dict[str, Any]]) -> None:
        """Hiển thị danh sách file vào treeview máy cục bộ kèm icon và zebra striping."""
        for row in self.tree_local.get_children():
            self.tree_local.delete(row)

        for idx, it in enumerate(items):
            sz_str = format_size(it["size"]) if not it["is_dir"] else ""
            display_name = f"📁 {it['name']}" if it["is_dir"] else f"📄 {it['name']}"
            stripe = "even" if idx % 2 == 0 else "odd"
            self.tree_local.insert(
                "",
                tk.END,
                values=(display_name, sz_str, it["mtime"], it["type"]),
                tags=(stripe,),
            )

    def _render_remote_tree(self, items: List[Dict[str, Any]]) -> None:
        """Hiển thị danh sách file vào treeview máy chủ kèm icon và zebra striping."""
        for row in self.tree_remote.get_children():
            self.tree_remote.delete(row)

        for idx, it in enumerate(items):
            sz_str = format_size(it["size"]) if not it["is_dir"] else ""
            display_name = f"📁 {it['name']}" if it["is_dir"] else f"📄 {it['name']}"
            stripe = "even" if idx % 2 == 0 else "odd"
            self.tree_remote.insert(
                "",
                tk.END,
                values=(display_name, sz_str, it["mtime"], it["type"]),
                tags=(stripe,),
            )

    def _sort_local_column(self, col: str) -> None:
        """Sắp xếp cột cục bộ: Thư mục luôn ở trên, có mũi tên ▲/▼."""
        if self.local_sort_col == col:
            self.local_sort_rev = not self.local_sort_rev
        else:
            self.local_sort_col = col
            self.local_sort_rev = False

        # Cập nhật mũi tên tiêu đề
        for c in ("name", "size", "mtime", "type"):
            title = {"name": "Tên", "size": "Kích thước", "mtime": "Ngày sửa", "type": "Loại"}[c]
            if c == col:
                title += " ▲" if not self.local_sort_rev else " ▼"
            self.tree_local.heading(c, text=title)

        dirs = [i for i in self.local_items_cache if i["is_dir"]]
        files = [i for i in self.local_items_cache if not i["is_dir"]]

        key_fn = lambda x: x[col] if col != "name" else x["name"].lower()
        dirs.sort(key=key_fn, reverse=self.local_sort_rev)
        files.sort(key=key_fn, reverse=self.local_sort_rev)

        self.local_items_cache = dirs + files
        self._filter_local_tree()

    def _sort_remote_column(self, col: str) -> None:
        """Sắp xếp cột máy chủ: Thư mục luôn ở trên, có mũi tên ▲/▼."""
        if self.remote_sort_col == col:
            self.remote_sort_rev = not self.remote_sort_rev
        else:
            self.remote_sort_col = col
            self.remote_sort_rev = False

        for c in ("name", "size", "mtime", "type"):
            title = {"name": "Tên", "size": "Kích thước", "mtime": "Ngày sửa", "type": "Loại"}[c]
            if c == col:
                title += " ▲" if not self.remote_sort_rev else " ▼"
            self.tree_remote.heading(c, text=title)

        dirs = [i for i in self.remote_items_cache if i["is_dir"]]
        files = [i for i in self.remote_items_cache if not i["is_dir"]]

        key_fn = lambda x: x[col] if col != "name" else x["name"].lower()
        dirs.sort(key=key_fn, reverse=self.remote_sort_rev)
        files.sort(key=key_fn, reverse=self.remote_sort_rev)

        self.remote_items_cache = dirs + files
        self._filter_remote_tree()

    def _filter_local_tree(self) -> None:
        """Lọc nhanh danh sách file cục bộ theo từ khóa."""
        kw = self.ent_local_filter.get().strip().lower()
        if not kw:
            self._render_local_tree(self.local_items_cache)
        else:
            filtered = [i for i in self.local_items_cache if kw in i["name"].lower()]
            self._render_local_tree(filtered)

    def _clear_local_filter(self) -> None:
        self.ent_local_filter.delete(0, tk.END)
        self._filter_local_tree()

    def _filter_remote_tree(self) -> None:
        """Lọc nhanh danh sách file máy chủ theo từ khóa."""
        kw = self.ent_remote_filter.get().strip().lower()
        if not kw:
            self._render_remote_tree(self.remote_items_cache)
        else:
            filtered = [i for i in self.remote_items_cache if kw in i["name"].lower()]
            self._render_remote_tree(filtered)

    def _clear_remote_filter(self) -> None:
        self.ent_remote_filter.delete(0, tk.END)
        self._filter_remote_tree()

    # ==================== KÉO THẢ GIỮA 2 KHUNG ====================

    def _on_local_drag_start(self, event) -> None:
        """Bắt đầu kéo chuột từ bảng Local."""
        row_id = self.tree_local.identify_row(event.y)
        if row_id:
            vals = self.tree_local.item(row_id, "values")
            raw_name = clean_item_name(vals[0])
            self._drag_data = {"source": "local", "row_id": row_id, "name": raw_name, "type": vals[3]}

    def _on_remote_drag_start(self, event) -> None:
        """Bắt đầu kéo chuột từ bảng Remote."""
        row_id = self.tree_remote.identify_row(event.y)
        if row_id:
            vals = self.tree_remote.item(row_id, "values")
            raw_name = clean_item_name(vals[0])
            self._drag_data = {"source": "remote", "row_id": row_id, "name": raw_name, "type": vals[3]}

    def _on_drag_motion(self, event) -> None:
        """Đang kéo chuột: đổi con trỏ sang dạng hand2."""
        if self._drag_data:
            self.root.config(cursor="hand2")

    def _on_local_drag_release(self, event) -> None:
        """Thả chuột khi kéo từ Local."""
        self.root.config(cursor="")
        if not self._drag_data or self._drag_data.get("source") != "local":
            self._drag_data = None
            return

        rx = self.tree_remote.winfo_rootx()
        ry = self.tree_remote.winfo_rooty()
        rw = self.tree_remote.winfo_width()
        rh = self.tree_remote.winfo_height()

        if rx <= event.x_root <= rx + rw and ry <= event.y_root <= ry + rh:
            fname = self._drag_data["name"]
            is_dir = self._drag_data["type"] == "Thư mục"
            src_path = os.path.join(self.local_current_dir, fname)
            dst_remote = f"{self.remote_current_dir.rstrip('/')}/{fname}"
            self._queue_item(src_path, dst_remote, "UPLOAD", is_dir)

        self._drag_data = None

    def _on_remote_drag_release(self, event) -> None:
        """Thả chuột khi kéo từ Remote."""
        self.root.config(cursor="")
        if not self._drag_data or self._drag_data.get("source") != "remote":
            self._drag_data = None
            return

        lx = self.tree_local.winfo_rootx()
        ly = self.tree_local.winfo_rooty()
        lw = self.tree_local.winfo_width()
        lh = self.tree_local.winfo_height()

        if lx <= event.x_root <= lx + lw and ly <= event.y_root <= ly + lh:
            fname = self._drag_data["name"]
            is_dir = self._drag_data["type"] == "Thư mục"
            src_remote = f"{self.remote_current_dir.rstrip('/')}/{fname}"
            dst_local = os.path.join(self.local_current_dir, fname)
            self._queue_item(src_remote, dst_local, "DOWNLOAD", is_dir)

        self._drag_data = None

    # ==================== ĐIỀU HƯỚNG VÀ DUYỆT FILE CỤC BỘ ====================

    def refresh_local_files(self) -> None:
        """Đọc và nạp danh sách tệp tại thư mục cục bộ."""
        self.ent_local_path.delete(0, tk.END)
        self.ent_local_path.insert(0, self.local_current_dir)

        try:
            entries = sorted(os.listdir(self.local_current_dir))
            items = []
            for name in entries:
                fp = os.path.join(self.local_current_dir, name)
                try:
                    st = os.stat(fp)
                    is_dir = os.path.isdir(fp)
                    mtime_str = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                    items.append({
                        "name": name,
                        "size": st.st_size if not is_dir else 0,
                        "is_dir": is_dir,
                        "mtime": mtime_str,
                        "type": "Thư mục" if is_dir else "Tệp",
                    })
                except OSError:
                    continue

            dirs = [i for i in items if i["is_dir"]]
            files = [i for i in items if not i["is_dir"]]
            self.local_items_cache = dirs + files
            self._filter_local_tree()

        except Exception as e:
            messagebox.showerror("Lỗi duyệt file cục bộ", str(e))

    def _local_go_up(self) -> None:
        parent = os.path.dirname(self.local_current_dir)
        if parent and parent != self.local_current_dir:
            self.local_current_dir = parent
            self.refresh_local_files()

    def _local_go_home(self) -> None:
        self.local_current_dir = os.path.abspath(os.path.expanduser("~"))
        self.refresh_local_files()

    def _on_local_path_enter(self) -> None:
        target = self.ent_local_path.get().strip()
        if os.path.exists(target) and os.path.isdir(target):
            self.local_current_dir = os.path.abspath(target)
            self.refresh_local_files()
        else:
            messagebox.showwarning("Lỗi", "Thư mục không tồn tại!")

    def _on_local_double_click(self, event) -> None:
        """Double click cục bộ: thư mục thì vào trong, tệp thì đưa vào hàng đợi truyền."""
        sel = self.tree_local.selection()
        if not sel:
            return
        vals = self.tree_local.item(sel[0], "values")
        name = clean_item_name(vals[0])
        is_dir = vals[3] == "Thư mục"

        if is_dir:
            self.local_current_dir = os.path.join(self.local_current_dir, name)
            self.refresh_local_files()
        else:
            if self.client.is_connected and self.client.is_logged_in:
                src_path = os.path.join(self.local_current_dir, name)
                dst_remote = f"{self.remote_current_dir.rstrip('/')}/{name}"
                self._queue_item(src_path, dst_remote, "UPLOAD", False)
            else:
                messagebox.showinfo("Chưa kết nối", "Vui lòng kết nối tới FTP Server trước khi truyền tệp!")

    def _show_local_context_menu(self, event) -> None:
        item = self.tree_local.identify_row(event.y)
        if item:
            self.tree_local.selection_set(item)
        self.menu_local.post(event.x_root, event.y_root)

    def _local_mkdir(self) -> None:
        name = simpledialog.askstring("Thư mục mới", "Nhập tên thư mục mới cần tạo trên máy:")
        if name:
            p = os.path.join(self.local_current_dir, name)
            try:
                os.makedirs(p, exist_ok=False)
                self.refresh_local_files()
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể tạo thư mục: {e}")

    def _local_rename(self) -> None:
        sel = self.tree_local.selection()
        if not sel:
            return
        vals = self.tree_local.item(sel[0], "values")
        old_name = clean_item_name(vals[0])
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
        sel = self.tree_local.selection()
        if not sel:
            return
        vals = self.tree_local.item(sel[0], "values")
        name = clean_item_name(vals[0])
        is_dir = vals[3] == "Thư mục"
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

    def _show_local_properties(self) -> None:
        """Xem chi tiết thuộc tính tệp cục bộ và mã băm SHA-256."""
        sel = self.tree_local.selection()
        if not sel:
            return
        vals = self.tree_local.item(sel[0], "values")
        name = clean_item_name(vals[0])
        fp = os.path.join(self.local_current_dir, name)

        top = tk.Toplevel(self.root)
        top.title(f"Thuộc tính: {name}")
        top.geometry("540x360")
        top.transient(self.root)

        frame = ttk.Frame(top, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"Tên tệp: {name}", font=self.font_bold).pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Đường dẫn: {fp}").pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Loại: {vals[3]}").pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Kích thước: {vals[1]}").pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Ngày sửa: {vals[2]}").pack(anchor=tk.W, pady=2)

        sha_box = ttk.LabelFrame(frame, text=" Mã Băm SHA-256 Cục Bộ ", padding=8)
        sha_box.pack(fill=tk.X, pady=10)

        lbl_sha = ttk.Label(sha_box, text="Đang tính toán mã băm...", wraplength=480)
        lbl_sha.pack(fill=tk.X)

        def calc_sha():
            val = CustomFTPClient.compute_local_sha256(fp)
            lbl_sha.config(text=val if val else "(Thư mục không có mã băm SHA-256)")

        threading.Thread(target=calc_sha, daemon=True).start()
        ttk.Button(frame, text="Đóng", command=top.destroy).pack(side=tk.BOTTOM, pady=6)

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
                if self.client.is_connected:
                    self.gui_queue.put({"action": "REMOTE_ERROR", "error": f"Lỗi lấy danh sách file: {e}"})

        threading.Thread(target=list_worker, daemon=True).start()

    def _remote_go_up(self) -> None:
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def up_worker():
            try:
                self.client.cdup()
                self.refresh_remote_files()
            except Exception as e:
                self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

        threading.Thread(target=up_worker, daemon=True).start()

    def _remote_go_root(self) -> None:
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def root_worker():
            try:
                self.client.cwd("/")
                self.refresh_remote_files()
            except Exception as e:
                self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

        threading.Thread(target=root_worker, daemon=True).start()

    def _on_remote_path_enter(self) -> None:
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
        """Double click từ xa: thư mục thì CWD vào trong, tệp thì đưa vào hàng đợi tải về."""
        sel = self.tree_remote.selection()
        if not sel:
            return
        vals = self.tree_remote.item(sel[0], "values")
        name = clean_item_name(vals[0])
        is_dir = vals[3] == "Thư mục"

        if is_dir:
            def cd_worker():
                try:
                    self.client.cwd(name)
                    self.refresh_remote_files()
                except Exception as e:
                    self.gui_queue.put({"action": "REMOTE_ERROR", "error": str(e)})

            threading.Thread(target=cd_worker, daemon=True).start()
        else:
            src_remote = f"{self.remote_current_dir.rstrip('/')}/{name}"
            dst_local = os.path.join(self.local_current_dir, name)
            self._queue_item(src_remote, dst_local, "DOWNLOAD", False)

    def _show_remote_context_menu(self, event) -> None:
        item = self.tree_remote.identify_row(event.y)
        if item:
            self.tree_remote.selection_set(item)
        self.menu_remote.post(event.x_root, event.y_root)

    def _remote_mkdir(self) -> None:
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
        sel = self.tree_remote.selection()
        if not sel:
            return
        vals = self.tree_remote.item(sel[0], "values")
        old_name = clean_item_name(vals[0])
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
        sel = self.tree_remote.selection()
        if not sel:
            return
        vals = self.tree_remote.item(sel[0], "values")
        name = clean_item_name(vals[0])
        is_dir = vals[3] == "Thư mục"

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

    def _show_remote_properties(self) -> None:
        """Xem thuộc tính tệp từ xa và kiểm tra toàn vẹn băm SHA-256 (XSHA256)."""
        sel = self.tree_remote.selection()
        if not sel:
            return
        vals = self.tree_remote.item(sel[0], "values")
        name = clean_item_name(vals[0])
        remote_path = f"{self.remote_current_dir.rstrip('/')}/{name}"
        is_dir = vals[3] == "Thư mục"

        top = tk.Toplevel(self.root)
        top.title(f"Thuộc tính Máy Chủ: {name}")
        top.geometry("580x410")
        top.transient(self.root)

        frame = ttk.Frame(top, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text=f"Tên: {name}", font=self.font_bold).pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Đường dẫn máy chủ: {remote_path}").pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Loại: {vals[3]}").pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Kích thước: {vals[1]}").pack(anchor=tk.W, pady=2)
        ttk.Label(frame, text=f"Ngày sửa: {vals[2]}").pack(anchor=tk.W, pady=2)

        sha_box = ttk.LabelFrame(frame, text=" Kiểm Tra Toàn Vẹn Băm SHA-256 (RFC & XSHA256) ", padding=8)
        sha_box.pack(fill=tk.X, pady=10)

        lbl_remote_sha = ttk.Label(sha_box, text="Server Hash: Đang truy vấn...", wraplength=520)
        lbl_remote_sha.pack(anchor=tk.W, pady=3)

        lbl_local_sha = ttk.Label(sha_box, text="Local Hash: --", wraplength=520)
        lbl_local_sha.pack(anchor=tk.W, pady=3)

        lbl_compare = ttk.Label(sha_box, text="Kết quả: --", font=self.font_bold)
        lbl_compare.pack(anchor=tk.W, pady=6)

        def query_sha():
            if is_dir:
                lbl_remote_sha.config(text="Server Hash: (Thư mục không có SHA-256)")
                return

            server_hash = self.client.get_remote_sha256(remote_path)
            lbl_remote_sha.config(text=f"Server Hash: {server_hash if server_hash else 'Không hỗ trợ'}")

            local_same_file = os.path.join(self.local_current_dir, name)
            if os.path.exists(local_same_file) and not os.path.isdir(local_same_file):
                local_hash = CustomFTPClient.compute_local_sha256(local_same_file)
                lbl_local_sha.config(text=f"Local Hash:  {local_hash}")
                if server_hash and local_hash:
                    if server_hash.lower() == local_hash.lower():
                        lbl_compare.config(text="✔ Kết quả: KHỚP 100% (Toàn vẹn hoàn hảo)", foreground="#16a34a")
                    else:
                        lbl_compare.config(text="✖ Kết quả: KHÔNG KHỚP (Dữ liệu bị sai lệch!)", foreground="#dc2626")
            else:
                lbl_local_sha.config(text="Local Hash:  (Chưa có tệp tương ứng trên máy để so sánh)")

        threading.Thread(target=query_sha, daemon=True).start()
        ttk.Button(frame, text="Đóng", command=top.destroy).pack(side=tk.BOTTOM, pady=6)

    # ==================== TRUYỀN FILE NÂNG CAO & HÀNG ĐỢI ĐA LUỒNG ====================

    def action_upload_selected(self) -> None:
        """Tải các mục được chọn từ Local lên Server."""
        if not (self.client.is_connected and self.client.is_logged_in):
            messagebox.showwarning("Chưa kết nối", "Vui lòng kết nối tới server trước!")
            return

        sel = self.tree_local.selection()
        if not sel:
            messagebox.showinfo("Chưa chọn", "Vui lòng chọn ít nhất một tệp hoặc thư mục!")
            return

        for item in sel:
            vals = self.tree_local.item(item, "values")
            name = clean_item_name(vals[0])
            is_dir = vals[3] == "Thư mục"
            src_path = os.path.join(self.local_current_dir, name)
            dst_remote = f"{self.remote_current_dir.rstrip('/')}/{name}"
            self._queue_item(src_path, dst_remote, "UPLOAD", is_dir)

    def action_queue_upload_selected(self) -> None:
        self.action_upload_selected()

    def action_download_selected(self) -> None:
        """Tải các mục được chọn từ Server về Local."""
        if not (self.client.is_connected and self.client.is_logged_in):
            messagebox.showwarning("Chưa kết nối", "Vui lòng kết nối tới server trước!")
            return

        sel = self.tree_remote.selection()
        if not sel:
            messagebox.showinfo("Chưa chọn", "Vui lòng chọn ít nhất một tệp hoặc thư mục!")
            return

        for item in sel:
            vals = self.tree_remote.item(item, "values")
            name = clean_item_name(vals[0])
            is_dir = vals[3] == "Thư mục"
            src_remote = f"{self.remote_current_dir.rstrip('/')}/{name}"
            dst_local = os.path.join(self.local_current_dir, name)
            self._queue_item(src_remote, dst_local, "DOWNLOAD", is_dir)

    def action_queue_download_selected(self) -> None:
        self.action_download_selected()

    def _queue_item(self, src: str, dst: str, direction: str, is_dir: bool) -> None:
        """Đưa tệp hoặc cây thư mục đệ quy vào hàng đợi truyền."""
        if is_dir:
            if direction == "UPLOAD":
                threading.Thread(
                    target=self._enqueue_local_dir_recursive, args=(src, dst), daemon=True
                ).start()
            else:
                threading.Thread(
                    target=self._enqueue_remote_dir_recursive, args=(src, dst), daemon=True
                ).start()
        else:
            self._create_and_push_task(src, dst, direction)

    def _enqueue_local_dir_recursive(self, local_dir: str, remote_dir: str) -> None:
        """Duyệt đệ quy cây thư mục cục bộ và thêm từng tệp vào hàng đợi."""
        base_len = len(local_dir)

        try:
            self.client.makedirs_remote(remote_dir)
        except Exception:
            pass

        for root, dirs, files in os.walk(local_dir):
            rel_path = root[base_len:].replace("\\", "/").strip("/")
            target_remote_dir = f"{remote_dir}/{rel_path}".rstrip("/") if rel_path else remote_dir

            for d in dirs:
                sub_r = f"{target_remote_dir}/{d}"
                try:
                    self.client.makedirs_remote(sub_r)
                except Exception:
                    pass

            for f in files:
                l_file = os.path.join(root, f)
                r_file = f"{target_remote_dir}/{f}"
                self._create_and_push_task(l_file, r_file, "UPLOAD")

        self.refresh_remote_files()

    def _enqueue_remote_dir_recursive(self, remote_dir: str, local_dir: str) -> None:
        """Duyệt đệ quy cây thư mục máy chủ và thêm từng tệp vào hàng đợi tải về."""
        os.makedirs(local_dir, exist_ok=True)
        try:
            items = self.client.list_dir(remote_dir)
        except Exception as e:
            self.gui_queue.put({"action": "LOG", "text": f"Lỗi đọc thư mục {remote_dir}: {e}", "tag": "ERROR"})
            return

        for it in items:
            sub_r = f"{remote_dir}/{it['name']}".rstrip("/")
            sub_l = os.path.join(local_dir, it["name"])
            if it["is_dir"]:
                self._enqueue_remote_dir_recursive(sub_r, sub_l)
            else:
                self._create_and_push_task(sub_r, sub_l, "DOWNLOAD")

    def _create_and_push_task(self, src: str, dst: str, direction: str) -> None:
        """Tạo một task truyền tệp và đẩy vào hàng đợi chờ xử lý."""
        filename = os.path.basename(src if direction == "UPLOAD" else dst)
        task_id = f"task_{time.time()}_{len(self.transfer_tasks)}"

        total_sz = 0
        if direction == "UPLOAD":
            total_sz = os.path.getsize(src) if os.path.exists(src) else 0
        else:
            total_sz = self.client.get_size(src)

        # Kiểm tra Resume (REST)
        offset = 0
        if direction == "DOWNLOAD":
            if os.path.exists(dst):
                cur_sz = os.path.getsize(dst)
                if 0 < cur_sz < total_sz:
                    dlg = ResumeChoiceDialog(self.root, filename, cur_sz, total_sz)
                    if dlg.result == "RESUME":
                        offset = cur_sz
                    elif dlg.result == "SKIP":
                        return
                    else:  # OVERWRITE
                        offset = 0
        elif direction == "UPLOAD":
            remote_sz = self.client.get_size(dst)
            if 0 < remote_sz < total_sz:
                dlg = ResumeChoiceDialog(self.root, filename, remote_sz, total_sz)
                if dlg.result == "RESUME":
                    offset = remote_sz
                elif dlg.result == "SKIP":
                    return
                else:
                    offset = 0

        task = {
            "id": task_id,
            "filename": filename,
            "src": src,
            "dst": dst,
            "direction": direction,
            "total_size": total_sz,
            "offset": offset,
            "status": "Chờ",
            "cancel_event": threading.Event(),
            "pause_event": threading.Event(),
            "speed_history": [],
        }

        self.transfer_tasks[task_id] = task

        self.gui_queue.put({
            "action": "QUEUE_INSERT",
            "id": task_id,
            "file": filename,
            "dir": "⬆ UPLOAD" if direction == "UPLOAD" else "⬇ DOWNLOAD",
            "size": format_size(total_sz),
            "progress": f"{int(offset / total_sz * 100)}%" if total_sz > 0 else "0%",
            "speed": "--",
            "eta": "--:--",
            "status": "Chờ",
        })

        self.transfer_queue.put(task_id)

    # ==================== ĐA LUỒNG TRUYỀN DỮ LIỆU (WORKER POOL) ====================

    def _start_transfer_workers(self) -> None:
        """Khởi động worker pool chạy ngầm lấy task từ transfer_queue."""
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            self.worker_threads.append(t)
            t.start()

    def _worker_loop(self, worker_id: int) -> None:
        """Vòng lặp của từng worker: kết nối session riêng và xử lý từng task."""
        while True:
            task_id = self.transfer_queue.get()
            if task_id not in self.transfer_tasks:
                self.transfer_queue.task_done()
                continue

            task = self.transfer_tasks[task_id]
            if task["status"] in ("Đã hủy", "Hoàn tất"):
                self.transfer_queue.task_done()
                continue

            task["status"] = "Đang truyền"
            self.gui_queue.put({"action": "QUEUE_UPDATE", "id": task_id, "status": "Đang truyền"})

            worker_client = CustomFTPClient()
            try:
                worker_client.connect(self.client.host, self.client.port)
                if self.client.tls_active:
                    worker_client.enable_ftps()
                worker_client.set_mode(self.client.mode)
                worker_client.login(self.client.current_user, self.ent_pass.get())

                def on_progress(transferred: int, total: int, raw_speed: float):
                    now = time.time()
                    task["speed_history"].append((now, transferred))
                    task["speed_history"] = [h for h in task["speed_history"] if now - h[0] <= 1.8]

                    if len(task["speed_history"]) >= 2:
                        db = task["speed_history"][-1][1] - task["speed_history"][0][1]
                        dt = task["speed_history"][-1][0] - task["speed_history"][0][0]
                        smooth_speed = db / dt if dt > 0 else raw_speed
                    else:
                        smooth_speed = raw_speed

                    pct = int(transferred / total * 100) if total > 0 else 0
                    rem_bytes = max(0, total - transferred)
                    eta_sec = int(rem_bytes / smooth_speed) if smooth_speed > 0 else 0

                    spd_str = f"{smooth_speed / 1024:.1f} KB/s" if smooth_speed < 1024 * 1024 else f"{smooth_speed / (1024*1024):.2f} MB/s"
                    eta_str = format_eta(eta_sec)

                    self.gui_queue.put({
                        "action": "PROGRESS",
                        "id": task_id,
                        "file": task["filename"],
                        "percent": pct,
                        "speed": spd_str,
                        "eta": eta_str,
                    })

                ok = False
                if task["direction"] == "UPLOAD":
                    ok = worker_client.upload_file(
                        task["src"],
                        task["dst"],
                        offset=task["offset"],
                        progress_callback=on_progress,
                        cancel_check=task["cancel_event"].is_set,
                        pause_check=task["pause_event"].is_set,
                    )
                else:
                    ok = worker_client.download_file(
                        task["src"],
                        task["dst"],
                        offset=task["offset"],
                        progress_callback=on_progress,
                        cancel_check=task["cancel_event"].is_set,
                        pause_check=task["pause_event"].is_set,
                    )

                worker_client.disconnect()

                if ok:
                    task["status"] = "Hoàn tất"
                    self.gui_queue.put({
                        "action": "QUEUE_UPDATE",
                        "id": task_id,
                        "status": "Hoàn tất",
                        "percent": 100,
                        "speed": "--",
                        "eta": "00:00",
                    })
                    if task["direction"] == "UPLOAD":
                        self.refresh_remote_files()
                    else:
                        self.refresh_local_files()
                else:
                    task["status"] = "Đã hủy"
                    self.gui_queue.put({
                        "action": "QUEUE_UPDATE",
                        "id": task_id,
                        "status": "Đã hủy",
                        "speed": "--",
                        "eta": "--:--",
                    })

            except Exception as e:
                task["status"] = "Lỗi"
                self.gui_queue.put({
                    "action": "QUEUE_UPDATE",
                    "id": task_id,
                    "status": f"Lỗi: {e}",
                    "speed": "--",
                    "eta": "--:--",
                })
            finally:
                self.transfer_queue.task_done()

    # ==================== ĐIỀU KHIỂN HÀNG ĐỢI ====================

    def _queue_pause_selected(self) -> None:
        sel = self.tree_queue.selection()
        if not sel:
            return
        task_id = sel[0]
        if task_id in self.transfer_tasks:
            t = self.transfer_tasks[task_id]
            t["pause_event"].set()
            t["status"] = "Tạm dừng"
            self.tree_queue.set(task_id, "status", "Tạm dừng")
            self.tree_queue.set(task_id, "speed", "--")

    def _queue_resume_selected(self) -> None:
        sel = self.tree_queue.selection()
        if not sel:
            return
        task_id = sel[0]
        if task_id in self.transfer_tasks:
            t = self.transfer_tasks[task_id]
            t["pause_event"].clear()
            if t["status"] == "Tạm dừng":
                t["status"] = "Đang truyền"
                self.tree_queue.set(task_id, "status", "Đang truyền")

    def _queue_cancel_selected(self) -> None:
        sel = self.tree_queue.selection()
        if not sel:
            return
        task_id = sel[0]
        if task_id in self.transfer_tasks:
            t = self.transfer_tasks[task_id]
            t["cancel_event"].set()
            t["pause_event"].clear()
            t["status"] = "Đã hủy"
            self.tree_queue.set(task_id, "status", "Đã hủy")
            self.tree_queue.set(task_id, "speed", "--")
            self.tree_queue.set(task_id, "eta", "--:--")

    def _queue_retry_selected(self) -> None:
        sel = self.tree_queue.selection()
        if not sel:
            return
        task_id = sel[0]
        if task_id in self.transfer_tasks:
            t = self.transfer_tasks[task_id]
            if t["status"] in ("Lỗi", "Đã hủy"):
                t["status"] = "Chờ"
                t["cancel_event"].clear()
                t["pause_event"].clear()
                self.tree_queue.set(task_id, "status", "Chờ")
                self.transfer_queue.put(task_id)

    def _queue_clear_completed(self) -> None:
        for task_id, t in list(self.transfer_tasks.items()):
            if t["status"] in ("Hoàn tất", "Đã hủy"):
                if self.tree_queue.exists(task_id):
                    self.tree_queue.delete(task_id)
                del self.transfer_tasks[task_id]

    # ==================== SITE MANAGER ====================

    def _open_site_manager(self) -> None:
        """Cửa sổ modal quản lý Site Manager."""
        top = tk.Toplevel(self.root)
        top.title("Quản Lý Sites (FTP Site Manager)")
        top.geometry("740x480")
        top.transient(self.root)

        main_box = ttk.Frame(top, padding=10)
        main_box.pack(fill=tk.BOTH, expand=True)

        left_f = ttk.LabelFrame(main_box, text=" Danh Sách Sites ", padding=6)
        left_f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        tree_sites = ttk.Treeview(left_f, columns=("name", "host"), show="headings", height=12)
        tree_sites.heading("name", text="Tên Site")
        tree_sites.heading("host", text="Host:Port")
        tree_sites.column("name", width=140)
        tree_sites.column("host", width=140)
        tree_sites.pack(fill=tk.BOTH, expand=True)

        right_f = ttk.LabelFrame(main_box, text=" Thông Tin Chi Tiết ", padding=10)
        right_f.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        ttk.Label(right_f, text="Tên Site:").grid(row=0, column=0, sticky=tk.W, pady=3)
        ent_sname = ttk.Entry(right_f, width=22)
        ent_sname.grid(row=0, column=1, sticky=tk.W, pady=3)

        ttk.Label(right_f, text="Host:").grid(row=1, column=0, sticky=tk.W, pady=3)
        ent_shost = ttk.Entry(right_f, width=22)
        ent_shost.grid(row=1, column=1, sticky=tk.W, pady=3)

        ttk.Label(right_f, text="Port:").grid(row=2, column=0, sticky=tk.W, pady=3)
        ent_sport = ttk.Entry(right_f, width=22)
        ent_sport.insert(0, "2121")
        ent_sport.grid(row=2, column=1, sticky=tk.W, pady=3)

        ttk.Label(right_f, text="Username:").grid(row=3, column=0, sticky=tk.W, pady=3)
        ent_suser = ttk.Entry(right_f, width=22)
        ent_suser.grid(row=3, column=1, sticky=tk.W, pady=3)

        ttk.Label(right_f, text="Password:").grid(row=4, column=0, sticky=tk.W, pady=3)
        ent_spass = ttk.Entry(right_f, width=22, show="*")
        ent_spass.grid(row=4, column=1, sticky=tk.W, pady=3)

        ttk.Label(right_f, text="Mode:").grid(row=5, column=0, sticky=tk.W, pady=3)
        cbo_smode = ttk.Combobox(right_f, values=["PASV", "PORT"], width=20, state="readonly")
        cbo_smode.current(0)
        cbo_smode.grid(row=5, column=1, sticky=tk.W, pady=3)

        var_stls = tk.BooleanVar(value=False)
        ttk.Checkbutton(right_f, text="Kích hoạt FTPS (TLS)", variable=var_stls).grid(
            row=6, column=0, columnspan=2, sticky=tk.W, pady=4
        )

        ttk.Label(right_f, text="Ghi chú:").grid(row=7, column=0, sticky=tk.W, pady=3)
        ent_snotes = ttk.Entry(right_f, width=22)
        ent_snotes.grid(row=7, column=1, sticky=tk.W, pady=3)

        selected_site_id = [None]

        def reload_site_list():
            for r in tree_sites.get_children():
                tree_sites.delete(r)
            for s in self.db.get_sites():
                tree_sites.insert("", tk.END, iid=str(s["id"]), values=(s["name"], f"{s['host']}:{s['port']}"))

        def on_select_site(event):
            sel = tree_sites.selection()
            if not sel:
                return
            sid = int(sel[0])
            selected_site_id[0] = sid
            site = self.db.get_site(sid)
            if site:
                ent_sname.delete(0, tk.END)
                ent_sname.insert(0, site["name"])
                ent_shost.delete(0, tk.END)
                ent_shost.insert(0, site["host"])
                ent_sport.delete(0, tk.END)
                ent_sport.insert(0, str(site["port"]))
                ent_suser.delete(0, tk.END)
                ent_suser.insert(0, site["username"])
                ent_spass.delete(0, tk.END)
                ent_spass.insert(0, site["password"])
                cbo_smode.current(0 if site["mode"] == "PASV" else 1)
                var_stls.set(bool(site["use_tls"]))
                ent_snotes.delete(0, tk.END)
                ent_snotes.insert(0, site["notes"] or "")

        tree_sites.bind("<<TreeviewSelect>>", on_select_site)

        btn_box = ttk.Frame(right_f)
        btn_box.grid(row=8, column=0, columnspan=2, pady=10)

        def save_site():
            name = ent_sname.get().strip()
            host = ent_shost.get().strip()
            port = int(ent_sport.get().strip() or "2121")
            user = ent_suser.get().strip()
            pwd = ent_spass.get()
            mode = cbo_smode.get()
            tls = int(var_stls.get())
            notes = ent_snotes.get().strip()

            if not name or not host:
                messagebox.showwarning("Lỗi", "Vui lòng nhập Tên site và Host!")
                return

            if selected_site_id[0]:
                self.db.update_site(selected_site_id[0], name, host, port, user, pwd, mode, tls, notes)
            else:
                self.db.add_site(name, host, port, user, pwd, mode, tls, notes)

            reload_site_list()
            self._refresh_saved_sites_dropdown()
            messagebox.showinfo("Thành công", f"Đã lưu site '{name}'!")

        def delete_site():
            if selected_site_id[0]:
                if messagebox.askyesno("Xác nhận", "Bạn có chắc muốn xóa site này?"):
                    self.db.delete_site(selected_site_id[0])
                    selected_site_id[0] = None
                    reload_site_list()
                    self._refresh_saved_sites_dropdown()

        def quick_connect_from_site():
            save_site()
            self._on_saved_site_selected(None)
            top.destroy()
            self.action_connect()

        ttk.Button(btn_box, text="💾 Lưu", command=save_site).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_box, text="🗑 Xóa", command=delete_site).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_box, text="⚡ Kết Nối Nhanh", command=quick_connect_from_site).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_box, text="Đóng", command=top.destroy).pack(side=tk.LEFT, padx=3)

        reload_site_list()

    # ==================== LỊCH SỬ & DUNG LƯỢNG (XQUOTA & XHISTORY) ====================

    def refresh_quota_info(self) -> None:
        """Truy vấn server qua lệnh mở rộng XQUOTA để cập nhật dung lượng đã dùng."""
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def quota_worker():
            try:
                res = self.client.get_quota_info()
                if res:
                    used_mb, max_mb, pct = res
                    self.gui_queue.put({"action": "UPDATE_QUOTA", "used": used_mb, "max": max_mb, "pct": pct})
            except Exception as e:
                if self.client.is_connected:
                    self.gui_queue.put({"action": "LOG", "text": f"Lỗi lấy thông tin quota: {e}", "tag": "WARN"})

        threading.Thread(target=quota_worker, daemon=True).start()

    def refresh_history_info(self) -> None:
        """Truy vấn server qua lệnh mở rộng XHISTORY 50 để lấy lịch sử truyền file."""
        if not (self.client.is_connected and self.client.is_logged_in):
            return

        def history_worker():
            try:
                hist = self.client.get_transfer_history(50)
                self.gui_queue.put({"action": "UPDATE_HISTORY", "items": hist})
            except Exception as e:
                if self.client.is_connected:
                    self.gui_queue.put({"action": "LOG", "text": f"Lỗi lấy lịch sử truyền: {e}", "tag": "WARN"})

        threading.Thread(target=history_worker, daemon=True).start()

    # ==================== KẾT NỐI / NGẮT KẾT NỐI ====================

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

                pwd_dir = self.client.pwd()
                self.remote_current_dir = pwd_dir

                try:
                    self.db.record_recent_connection(
                        site_id=None,
                        host=host,
                        port=port,
                        username=user,
                        mode=mode_val,
                        use_tls=int(use_tls),
                        last_remote_dir=pwd_dir,
                    )
                except Exception as db_err:
                    self.gui_queue.put({"action": "LOG", "text": f"Ghi nhận lịch sử kết nối: {db_err}", "tag": "WARN"})

                self.gui_queue.put({"action": "CONNECTED", "host": host, "port": port, "user": user})

                # Tải danh sách file từ xa ban đầu tuần tự
                try:
                    items = self.client.list_dir("")
                    self.gui_queue.put({"action": "UPDATE_REMOTE_LIST", "items": items, "pwd": pwd_dir})
                except Exception as list_err:
                    self.gui_queue.put({"action": "LOG", "text": f"Lỗi lấy danh sách file: {list_err}", "tag": "WARN"})

                # Tải thông tin Quota ban đầu
                try:
                    res = self.client.get_quota_info()
                    if res:
                        used_mb, max_mb, pct = res
                        self.gui_queue.put({"action": "UPDATE_QUOTA", "used": used_mb, "max": max_mb, "pct": pct})
                except Exception as q_err:
                    self.gui_queue.put({"action": "LOG", "text": f"Lỗi lấy thông tin quota: {q_err}", "tag": "WARN"})

                # Tải thông tin lịch sử truyền file ban đầu
                try:
                    hist = self.client.get_transfer_history(50)
                    self.gui_queue.put({"action": "UPDATE_HISTORY", "items": hist})
                except Exception as h_err:
                    self.gui_queue.put({"action": "LOG", "text": f"Lỗi lấy lịch sử truyền: {h_err}", "tag": "WARN"})

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
        self.lbl_conn_status.config(text="⚪ CHƯA KẾT NỐI", bg="#f1f5f9", fg="#64748b")
        for item in self.tree_remote.get_children():
            self.tree_remote.delete(item)
        self.lbl_transfer_status.config(text="Trạng thái: Đã ngắt kết nối")

    # ==================== VÒNG LẶP CẬP NHẬT GUI (GUI QUEUE) ====================

    def _process_gui_queue(self) -> None:
        """Đọc và xử lý các sự kiện từ các luồng gửi về giao diện an toàn."""
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
                    h = msg.get("host", "127.0.0.1")
                    p = msg.get("port", 2121)
                    u = msg.get("user", "admin")
                    self.lbl_conn_status.config(
                        text=f"🟢 ĐÃ KẾT NỐI ({u}@{h}:{p})", bg="#dcfce7", fg="#15803d"
                    )
                    self.lbl_transfer_status.config(text="Trạng thái: Sẵn sàng")

                elif act == "CONNECT_FAILED":
                    self.btn_connect.config(state=tk.NORMAL)
                    self.btn_disconnect.config(state=tk.DISABLED)
                    self.lbl_conn_status.config(text="🔴 KẾT NỐI THẤT BẠI", bg="#fee2e2", fg="#b91c1c")
                    messagebox.showerror("Lỗi kết nối", msg.get("error", "Không thể kết nối!"))

                elif act == "UPDATE_REMOTE_LIST":
                    pwd = msg.get("pwd", "/")
                    self.ent_remote_path.delete(0, tk.END)
                    self.ent_remote_path.insert(0, pwd)

                    items = msg.get("items", [])
                    dirs = [i for i in items if i["is_dir"]]
                    files = [i for i in items if not i["is_dir"]]
                    self.remote_items_cache = dirs + files
                    self._filter_remote_tree()

                elif act == "REMOTE_ERROR":
                    messagebox.showerror("Lỗi máy chủ", msg.get("error"))

                elif act == "QUEUE_INSERT":
                    qid = msg["id"]
                    self.tree_queue.insert(
                        "",
                        tk.END,
                        iid=qid,
                        values=(
                            msg["file"],
                            msg["dir"],
                            msg["size"],
                            msg["progress"],
                            msg["speed"],
                            msg["eta"],
                            msg["status"],
                        ),
                    )

                elif act == "PROGRESS":
                    qid = msg.get("id")
                    pct = msg.get("percent", 0)
                    spd = msg.get("speed", "--")
                    eta = msg.get("eta", "--:--")
                    fname = msg.get("file", "")

                    self.progressbar["value"] = pct
                    self.lbl_transfer_status.config(
                        text=f"Đang truyền: {fname} ({pct}%) | ⚡ {spd} | ⏱ Còn lại: {eta}"
                    )
                    if qid and self.tree_queue.exists(qid):
                        self.tree_queue.set(qid, "progress", f"{pct}%")
                        self.tree_queue.set(qid, "speed", spd)
                        self.tree_queue.set(qid, "eta", eta)
                        self.tree_queue.set(qid, "status", "Đang truyền")

                elif act == "QUEUE_UPDATE":
                    qid = msg.get("id")
                    if qid and self.tree_queue.exists(qid):
                        if "status" in msg:
                            self.tree_queue.set(qid, "status", msg["status"])
                        if "percent" in msg:
                            self.tree_queue.set(qid, "progress", f"{msg['percent']}%")
                        if "speed" in msg:
                            self.tree_queue.set(qid, "speed", msg["speed"])
                        if "eta" in msg:
                            self.tree_queue.set(qid, "eta", msg["eta"])

                    st = msg.get("status")
                    if st in ("Hoàn tất", "Đã hủy"):
                        self.progressbar["value"] = 100 if st == "Hoàn tất" else 0
                        self.lbl_transfer_status.config(text=f"Truyền tệp: {st}")
                        self.refresh_quota_info()
                        self.refresh_history_info()

                elif act == "UPDATE_QUOTA":
                    used = msg.get("used", 0.0)
                    mx = msg.get("max", 0.0)
                    pct = msg.get("pct", 0.0)
                    self.lbl_quota_text.config(
                        text=f"Đã dùng: {used:.1f} MB / {mx:.1f} MB ({pct:.1f}% đã sử dụng)"
                    )
                    self.pbar_quota["value"] = pct

                elif act == "UPDATE_HISTORY":
                    for r in self.tree_history.get_children():
                        self.tree_history.delete(r)
                    for h in msg.get("items", []):
                        sz = int(h["size"]) if h["size"].isdigit() else 0
                        self.tree_history.insert(
                            "",
                            tk.END,
                            values=(
                                h["file"],
                                h["direction"],
                                format_size(sz),
                                h["time"],
                                h["status"],
                            ),
                        )

        except Exception:
            pass
        finally:
            self.root.after(100, self._process_gui_queue)
