from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .config import ICON_PATH, ICON_PNG_PATH, AppConfig, load_config, save_config
from .market_data import QuoteError, StockQuote, fetch_quotes, normalize_symbol
from .tray import TrayController, TrayUnavailable


MODE_LABELS = {
    "table": "完整版",
    "mini": "极简置顶",
    "widget": "桌面组件",
    "summary": "市场摘要",
}
LABEL_TO_MODE = {label: mode for mode, label in MODE_LABELS.items()}


class AStockPanel(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.config_data = load_config()
        self.result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.refresh_job: str | None = None
        self.is_loading = False
        self.last_quotes: list[StockQuote] = []
        self.drag_start: tuple[int, int] | None = None
        self.tray_controller: TrayController | None = None

        self.title("Market Summary")
        self._apply_app_icon()
        self.configure(bg="#eef1ee")
        self.minsize(260, 64)

        self._build_variables()
        self._build_styles()
        self._build_context_menu()
        self._setup_tray(silent=True)
        self._switch_mode(self.config_data.mode, save=False)
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.after(100, self.refresh_quotes)
        self.after(150, self._drain_queue)

    def _apply_app_icon(self) -> None:
        if ICON_PATH.exists():
            try:
                self.iconbitmap(default=str(ICON_PATH))
            except tk.TclError:
                pass
        if ICON_PNG_PATH.exists():
            try:
                self._icon_photo = tk.PhotoImage(file=str(ICON_PNG_PATH))
                self.iconphoto(True, self._icon_photo)
            except tk.TclError:
                pass

    def _build_variables(self) -> None:
        self.symbol_var = tk.StringVar()
        self.status_var = tk.StringVar(value="准备刷新")
        self.interval_var = tk.IntVar(value=self.config_data.refresh_seconds)
        self.topmost_var = tk.BooleanVar(value=self.config_data.topmost)
        self.color_mode_var = tk.StringVar(value=self.config_data.color_mode)
        self.mode_label_var = tk.StringVar(value=MODE_LABELS.get(self.config_data.mode, "极简置顶"))
        self.opacity_var = tk.DoubleVar(value=self.config_data.opacity)
        self.minimize_to_taskbar_var = tk.BooleanVar(value=self.config_data.minimize_to_taskbar)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Panel.TFrame", background="#eef1ee")
        style.configure("Header.TFrame", background="#e2e7e2")
        style.configure("TLabel", background="#eef1ee", foreground="#202721")
        style.configure("Muted.TLabel", background="#eef1ee", foreground="#68736a")
        style.configure("Header.TLabel", background="#e2e7e2", foreground="#202721")
        style.configure("TButton", padding=(10, 5))
        style.configure("TCheckbutton", background="#eef1ee", foreground="#202721")
        style.configure("Treeview", rowheight=30, fieldbackground="#fbfcfa", background="#fbfcfa")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_context_menu(self) -> None:
        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="打开完整版", command=lambda: self._switch_mode("table"))
        self.context_menu.add_command(label="极简置顶", command=lambda: self._switch_mode("mini"))
        self.context_menu.add_command(label="桌面组件", command=lambda: self._switch_mode("widget"))
        self.context_menu.add_command(label="市场摘要", command=lambda: self._switch_mode("summary"))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="刷新", command=self.refresh_quotes)
        self.context_menu.add_command(label="设置", command=self.open_settings)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="隐藏到托盘", command=self.hide_to_tray)
        self.context_menu.add_command(label="退出", command=self.quit_app)
        self.bind("<Button-3>", self._show_context_menu)

    def _switch_mode(self, mode: str, save: bool = True) -> None:
        mode = mode if mode in MODE_LABELS else "mini"
        self.config_data.mode = mode
        self.mode_label_var.set(MODE_LABELS[mode])
        self._set_mode_geometry(mode)
        self._render_current_mode()
        if save:
            self._save_current_config()

    def _set_mode_geometry(self, mode: str) -> None:
        if mode == "table":
            self.geometry(self.config_data.geometry if self.config_data.geometry and "x" in self.config_data.geometry else "900x520")
        elif mode == "mini":
            self.geometry("360x110")
        elif mode == "widget":
            self.geometry("340x260")
        else:
            self.geometry("720x48")

    def _render_current_mode(self) -> None:
        self._clear_window()
        self._apply_window_options()

        if self.config_data.mode == "table":
            self._render_table_mode()
        elif self.config_data.mode == "mini":
            self._render_mini_mode()
        elif self.config_data.mode == "widget":
            self._render_widget_mode()
        else:
            self._render_summary_mode()

    def _clear_window(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def _apply_window_options(self) -> None:
        borderless = self.config_data.mode in {"mini", "summary"}
        self.overrideredirect(borderless)
        self.attributes("-topmost", self.topmost_var.get())
        self.attributes("-alpha", max(0.45, min(1.0, float(self.opacity_var.get()))))

    def _render_table_mode(self) -> None:
        root = ttk.Frame(self, style="Panel.TFrame", padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root, style="Header.TFrame", padding=(10, 8))
        header.pack(fill=tk.X)
        ttk.Label(header, text="A 股行情管理", style="Header.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="置顶项会进入托盘和极简模式", style="Header.TLabel").pack(side=tk.LEFT, padx=(14, 0))
        ttk.Button(header, text="设置", command=self.open_settings).pack(side=tk.RIGHT)

        toolbar = ttk.Frame(root, style="Panel.TFrame")
        toolbar.pack(fill=tk.X, pady=(10, 8))
        ttk.Label(toolbar, text="模式").pack(side=tk.LEFT)
        mode_box = ttk.Combobox(toolbar, textvariable=self.mode_label_var, values=list(MODE_LABELS.values()), width=10, state="readonly")
        mode_box.pack(side=tk.LEFT, padx=(6, 14))
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self.set_mode_from_label())
        ttk.Label(toolbar, text="代码").pack(side=tk.LEFT)
        entry = ttk.Entry(toolbar, textvariable=self.symbol_var, width=12)
        entry.pack(side=tk.LEFT, padx=(6, 8))
        entry.bind("<Return>", lambda _event: self.add_symbol())
        ttk.Button(toolbar, text="添加", command=self.add_symbol).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="移除", command=self.remove_selected).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="置顶/取消", command=self.toggle_selected_pin).pack(side=tk.LEFT, padx=(0, 14))
        ttk.Button(toolbar, text="刷新", command=self.refresh_quotes).pack(side=tk.LEFT)
        ttk.Label(toolbar, text="间隔").pack(side=tk.LEFT, padx=(16, 5))
        ttk.Spinbox(toolbar, from_=10, to=300, increment=5, textvariable=self.interval_var, width=5).pack(side=tk.LEFT)
        ttk.Label(toolbar, text="秒").pack(side=tk.LEFT, padx=(4, 12))
        ttk.Checkbutton(toolbar, text="窗口置顶", variable=self.topmost_var, command=self.toggle_window_topmost).pack(side=tk.LEFT)

        columns = ("pin", "code", "name", "price", "change", "percent", "amount", "volume", "time")
        self.tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", lambda _event: self.toggle_selected_pin())
        self.tree.bind("<Button-3>", self._show_context_menu)

        headers = {
            "pin": "置顶",
            "code": "代码",
            "name": "名称",
            "price": "现价",
            "change": "涨跌额",
            "percent": "涨跌幅",
            "amount": "成交额",
            "volume": "成交量",
            "time": "时间",
        }
        widths = {
            "pin": 54,
            "code": 86,
            "name": 118,
            "price": 92,
            "change": 90,
            "percent": 90,
            "amount": 120,
            "volume": 110,
            "time": 90,
        }
        for column in columns:
            self.tree.heading(column, text=headers[column])
            self.tree.column(column, width=widths[column], anchor=tk.E)
        self.tree.column("name", anchor=tk.W)
        self.tree.column("pin", anchor=tk.CENTER)
        self.tree.tag_configure("up", foreground=self._change_color(1))
        self.tree.tag_configure("down", foreground=self._change_color(-1))
        self.tree.tag_configure("flat", foreground="#4d554e")
        self.tree.tag_configure("pinned", background="#f4f6f1")
        self._fill_table(self.last_quotes)

        footer = ttk.Frame(root, style="Panel.TFrame")
        footer.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, text="双击行可置顶；关闭窗口会保留托盘", style="Muted.TLabel").pack(side=tk.RIGHT)

    def _render_mini_mode(self) -> None:
        root = tk.Frame(self, bg="#edf0ec", highlightbackground="#cfd7cf", highlightthickness=1)
        root.pack(fill=tk.BOTH, expand=True)
        self._bind_drag(root)
        quotes = self._pinned_quotes()
        if not quotes:
            self._label(root, "在完整版置顶行情", 11, "#404841", bg="#edf0ec").pack(expand=True)
            return

        for quote in quotes[:3]:
            row = tk.Frame(root, bg="#edf0ec")
            row.pack(fill=tk.X, padx=10, pady=(8 if quote is quotes[0] else 2, 0))
            self._bind_drag(row)
            self._label(row, _short_name(quote.name), 10, "#29312b", bg="#edf0ec", bold=True, width=8, anchor="w").pack(side=tk.LEFT)
            self._label(row, format_price(quote.price), 13, "#29312b", bg="#edf0ec", width=10, anchor="e").pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._label(row, format_percent(quote.change_percent), 10, self._quote_color(quote), bg="#edf0ec", width=9, anchor="e").pack(side=tk.RIGHT)

    def _render_widget_mode(self) -> None:
        root = ttk.Frame(self, style="Panel.TFrame", padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(root, style="Panel.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(header, text="桌面行情", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="设置", command=self.open_settings).pack(side=tk.RIGHT)
        ttk.Button(header, text="隐藏", command=self.hide_to_tray).pack(side=tk.RIGHT, padx=(0, 6))

        body = tk.Frame(root, bg="#eef1ee")
        body.pack(fill=tk.BOTH, expand=True, pady=(10, 8))
        quotes = self.last_quotes or []
        if not quotes:
            self._label(body, "刷新中...", 12, "#3f4740").pack(expand=True)
        for quote in quotes[:5]:
            row = tk.Frame(body, bg="#f8faf7", highlightbackground="#d8ded7", highlightthickness=1)
            row.pack(fill=tk.X, pady=3)
            pin = "★" if quote.secid in self._pinned_set() else " "
            self._label(row, pin, 10, "#626d63", bg="#f8faf7", width=2).pack(side=tk.LEFT, padx=(6, 0))
            self._label(row, _short_name(quote.name), 10, "#263029", bg="#f8faf7", bold=True, width=8, anchor="w").pack(side=tk.LEFT)
            self._label(row, format_price(quote.price), 11, "#263029", bg="#f8faf7", width=10, anchor="e").pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._label(row, format_percent(quote.change_percent), 10, self._quote_color(quote), bg="#f8faf7", width=9, anchor="e").pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Label(root, text=f"更新 {self._last_update_time()}  右键可切换模式", style="Muted.TLabel").pack(anchor="e")

    def _render_summary_mode(self) -> None:
        root = tk.Frame(self, bg="#202721")
        root.pack(fill=tk.BOTH, expand=True)
        self._bind_drag(root)
        quotes = self.last_quotes or []
        if quotes:
            text = "   ".join(f"{_short_name(q.name)} {format_percent(q.change_percent)}" for q in quotes[:4])
            text = f"{text}   {self._last_update_time()}"
        else:
            text = "市场摘要  刷新中..."
        label = tk.Label(root, text=text, bg="#202721", fg="#dfe6df", font=("Microsoft YaHei UI", 11), anchor="w")
        label.pack(fill=tk.BOTH, expand=True, padx=14)
        self._bind_drag(label)

    def set_mode_from_label(self) -> None:
        self._switch_mode(LABEL_TO_MODE.get(self.mode_label_var.get(), "mini"))

    def add_symbol(self) -> None:
        raw_symbol = self.symbol_var.get().strip()
        try:
            symbol = normalize_symbol(raw_symbol)
        except ValueError as exc:
            messagebox.showwarning("代码格式不正确", str(exc))
            return
        if symbol not in self.config_data.symbols:
            self.config_data.symbols.append(symbol)
        self.symbol_var.set("")
        self._save_current_config()
        self.refresh_quotes()

    def remove_selected(self) -> None:
        selected = self._selected_secid()
        if not selected:
            return
        self.config_data.symbols = [symbol for symbol in self.config_data.symbols if normalize_symbol(symbol) != selected]
        self.config_data.pinned_symbols = [symbol for symbol in self.config_data.pinned_symbols if normalize_symbol(symbol) != selected]
        self._save_current_config()
        self.refresh_quotes()

    def toggle_selected_pin(self) -> None:
        selected = self._selected_secid()
        if not selected:
            return
        pinned = self._pinned_set()
        if selected in pinned:
            self.config_data.pinned_symbols = [symbol for symbol in self.config_data.pinned_symbols if normalize_symbol(symbol) != selected]
        else:
            self.config_data.pinned_symbols.append(selected)
        self._save_current_config()
        self._render_current_mode()
        self._refresh_tray()

    def _selected_secid(self) -> str | None:
        tree = getattr(self, "tree", None)
        if tree is None:
            return None
        selected = tree.selection()
        return str(selected[0]) if selected else None

    def refresh_quotes(self) -> None:
        if self.is_loading:
            return
        self.is_loading = True
        self.status_var.set("刷新中...")
        symbols = list(self.config_data.symbols)
        threading.Thread(target=self._fetch_worker, args=(symbols,), daemon=True).start()

    def _fetch_worker(self, symbols: list[str]) -> None:
        try:
            quotes = fetch_quotes(symbols)
        except QuoteError as exc:
            self.result_queue.put(("error", str(exc)))
        except Exception as exc:
            self.result_queue.put(("error", f"未知错误: {exc}"))
        else:
            self.result_queue.put(("quotes", quotes))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "quotes":
                    self.last_quotes = payload  # type: ignore[assignment]
                    self.status_var.set(f"已更新 {len(self.last_quotes)} 项")
                    self._render_current_mode()
                    self._refresh_tray()
                else:
                    self.status_var.set(str(payload))
                self.is_loading = False
                self._schedule_refresh()
        except queue.Empty:
            pass
        self.after(150, self._drain_queue)

    def _fill_table(self, quotes: list[StockQuote]) -> None:
        tree = getattr(self, "tree", None)
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        pinned = self._pinned_set()
        for quote in quotes:
            values = (
                "★" if quote.secid in pinned else "",
                quote.code,
                quote.name,
                format_price(quote.price),
                format_signed(quote.change_amount),
                format_percent(quote.change_percent),
                format_money(quote.amount),
                format_volume(quote.volume),
                quote.update_time,
            )
            tags = [row_tag(quote.change_percent)]
            if quote.secid in pinned:
                tags.append("pinned")
            tree.insert("", tk.END, iid=quote.secid, values=values, tags=tuple(tags))

    def _schedule_refresh(self) -> None:
        if self.refresh_job:
            self.after_cancel(self.refresh_job)
        seconds = max(10, int(self.interval_var.get() or 30))
        self.refresh_job = self.after(seconds * 1000, self.refresh_quotes)

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("设置")
        if ICON_PATH.exists():
            try:
                dialog.iconbitmap(default=str(ICON_PATH))
            except tk.TclError:
                pass
        if hasattr(self, "_icon_photo"):
            try:
                dialog.iconphoto(False, self._icon_photo)
            except tk.TclError:
                pass
        dialog.geometry("390x260")
        dialog.resizable(False, False)
        dialog.configure(bg="#eef1ee")
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="显示模式").grid(row=0, column=0, sticky="w", pady=7)
        mode_box = ttk.Combobox(frame, textvariable=self.mode_label_var, values=list(MODE_LABELS.values()), state="readonly", width=14)
        mode_box.grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(frame, text="刷新间隔").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Spinbox(frame, from_=10, to=300, increment=5, textvariable=self.interval_var, width=8).grid(row=1, column=1, sticky="w", pady=7)
        ttk.Label(frame, text="透明度").grid(row=2, column=0, sticky="w", pady=7)
        tk.Scale(frame, from_=0.55, to=1.0, resolution=0.01, orient=tk.HORIZONTAL, variable=self.opacity_var, bg="#eef1ee", highlightthickness=0).grid(row=2, column=1, sticky="ew", pady=7)
        ttk.Label(frame, text="涨跌颜色").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Combobox(frame, textvariable=self.color_mode_var, values=["neutral", "market"], state="readonly", width=14).grid(row=3, column=1, sticky="ew", pady=7)
        ttk.Checkbutton(frame, text="窗口置顶", variable=self.topmost_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=7)
        ttk.Checkbutton(frame, text="关闭时最小化到任务栏", variable=self.minimize_to_taskbar_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=7)
        ttk.Label(frame, text="系统托盘已常驻，双击托盘图标可打开面板", style="Muted.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        frame.columnconfigure(1, weight=1)

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="保存", command=lambda: self._save_settings_dialog(dialog)).pack(side=tk.RIGHT)

    def _save_settings_dialog(self, dialog: tk.Toplevel) -> None:
        self.config_data.color_mode = self.color_mode_var.get()
        self.config_data.opacity = float(self.opacity_var.get())
        self.config_data.minimize_to_taskbar = self.minimize_to_taskbar_var.get()
        self._switch_mode(LABEL_TO_MODE.get(self.mode_label_var.get(), "mini"), save=False)
        self._save_current_config()
        dialog.destroy()

    def toggle_window_topmost(self) -> None:
        self._apply_window_options()
        self._save_current_config()

    def _save_current_config(self) -> None:
        self.config_data.refresh_seconds = max(10, int(self.interval_var.get() or 30))
        self.config_data.topmost = self.topmost_var.get()
        self.config_data.color_mode = self.color_mode_var.get()
        self.config_data.opacity = float(self.opacity_var.get())
        self.config_data.minimize_to_taskbar = self.minimize_to_taskbar_var.get()
        self.config_data.enable_tray = True
        if self.state() == "normal" and self.config_data.mode == "table":
            self.config_data.geometry = self.geometry()
        save_config(self.config_data)

    def _setup_tray(self, silent: bool) -> bool:
        if self.tray_controller is not None:
            return True
        try:
            self.tray_controller = TrayController(
                show_window=lambda: self.after(0, self.show_window),
                hide_window=lambda: self.after(0, self.withdraw),
                quit_app=lambda: self.after(0, self.quit_app),
                switch_mode=lambda mode: self.after(0, lambda: self.show_mode(mode)),
                get_pinned_lines=self.get_pinned_lines,
            )
            self.tray_controller.start()
            return True
        except TrayUnavailable as exc:
            if not silent:
                messagebox.showinfo("系统托盘不可用", str(exc))
            return False

    def show_mode(self, mode: str) -> None:
        self.deiconify()
        self._switch_mode(mode)
        self.lift()

    def hide_to_tray(self) -> None:
        if self.tray_controller is None:
            self.iconify()
            return
        self.withdraw()

    def show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def quit_app(self) -> None:
        self._save_current_config()
        if self.tray_controller is not None:
            self.tray_controller.stop()
        self.destroy()

    def _refresh_tray(self) -> None:
        if self.tray_controller is not None:
            self.tray_controller.refresh_menu()

    def get_pinned_lines(self) -> list[str]:
        return [
            f"{_short_name(quote.name)} {format_price(quote.price)} {format_percent(quote.change_percent)}"
            for quote in self._pinned_quotes()
        ]

    def _pinned_quotes(self) -> list[StockQuote]:
        pinned = self._pinned_set()
        return [quote for quote in self.last_quotes if quote.secid in pinned]

    def _pinned_set(self) -> set[str]:
        normalized: set[str] = set()
        for symbol in self.config_data.pinned_symbols:
            try:
                normalized.add(normalize_symbol(symbol))
            except ValueError:
                continue
        return normalized

    def _last_update_time(self) -> str:
        if not self.last_quotes:
            return "--:--:--"
        return self.last_quotes[0].update_time

    def _show_context_menu(self, event: tk.Event) -> None:
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _bind_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._start_drag)
        widget.bind("<B1-Motion>", self._drag_window)
        widget.bind("<Button-3>", self._show_context_menu)

    def _start_drag(self, event: tk.Event) -> None:
        self.drag_start = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_window(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        offset_x, offset_y = self.drag_start
        self.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _label(
        self,
        parent: tk.Widget,
        text: str,
        size: int,
        fg: str,
        bg: str = "#eef1ee",
        bold: bool = False,
        width: int | None = None,
        anchor: str = "center",
    ) -> tk.Label:
        font = ("Microsoft YaHei UI", size, "bold") if bold else ("Microsoft YaHei UI", size)
        label = tk.Label(parent, text=text, bg=bg, fg=fg, font=font, width=width or 0, anchor=anchor)
        self._bind_drag(label)
        return label

    def _quote_color(self, quote: StockQuote) -> str:
        if self.config_data.color_mode == "neutral":
            return "#535d55"
        return self._change_color(quote.change_percent or 0)

    def _change_color(self, change: float) -> str:
        if self.config_data.color_mode == "neutral":
            return "#535d55"
        if change > 0:
            return "#b63b38"
        if change < 0:
            return "#247a53"
        return "#535d55"


def row_tag(change_percent: float | None) -> str:
    if change_percent is None or change_percent == 0:
        return "flat"
    return "up" if change_percent > 0 else "down"


def _short_name(name: str) -> str:
    if name == "上证指数":
        return "上证"
    if name == "深证成指":
        return "深成指"
    return name.replace("指数", "").replace("成指", "成")


def format_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def format_signed(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_volume(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿手"
    if value >= 10_000:
        return f"{value / 10_000:.2f}万手"
    return f"{value:.0f}手"


def format_money(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.2f}万"
    return f"{value:.0f}"


def main() -> None:
    configure_windows_app_id()
    app = AStockPanel()
    app.mainloop()


def configure_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LEO0727.AStockPanel")
    except Exception:
        pass
