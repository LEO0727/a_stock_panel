from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .config import ICON_PATH, ICON_PNG_PATH, AppConfig, load_config, save_config
from .market_data import ChartData, KLinePoint, QuoteError, StockQuote, TrendPoint, fetch_chart_data, fetch_quotes, normalize_symbol
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
        self.is_chart_loading = False
        self.last_quotes: list[StockQuote] = []
        self.last_chart: ChartData | None = None
        self.chart_symbol_var = tk.StringVar()
        self.chart_period_var = tk.StringVar(value="分时")
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
        self.chart_symbol_var.set(self.config_data.symbols[0] if self.config_data.symbols else "1.000001")

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
            self.geometry(self.config_data.geometry if self.config_data.geometry and "x" in self.config_data.geometry else "1120x720")
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
        self.tree = None

    def _apply_window_options(self) -> None:
        borderless = self.config_data.mode in {"mini", "summary"}
        self.overrideredirect(borderless)
        self.attributes("-topmost", self.topmost_var.get())
        if self.config_data.mode == "mini":
            self.attributes("-topmost", True)
            try:
                self.attributes("-transparentcolor", "#010203")
            except tk.TclError:
                pass
        else:
            try:
                self.attributes("-transparentcolor", "")
            except tk.TclError:
                pass
        self.attributes("-alpha", max(0.45, min(1.0, float(self.opacity_var.get()))))

    def _render_table_mode(self) -> None:
        root = tk.Frame(self, bg="#f6f7f9")
        root.pack(fill=tk.BOTH, expand=True)

        self.sidebar = tk.Frame(root, bg="#f7f8fa", width=300, highlightbackground="#e3e6ea", highlightthickness=1)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        self.main_panel = tk.Frame(root, bg="#ffffff")
        self.main_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._render_modern_sidebar()
        self._render_modern_main()

    def _render_watchlist_tab(self, parent: tk.Widget) -> None:
        toolbar = ttk.Frame(parent, style="Panel.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 8))
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
        self.tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
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

        footer = ttk.Frame(parent, style="Panel.TFrame")
        footer.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, text="双击行可置顶；关闭窗口会保留托盘", style="Muted.TLabel").pack(side=tk.RIGHT)

    def _render_modern_sidebar(self) -> None:
        for child in self.sidebar.winfo_children():
            child.destroy()

        search = tk.Frame(self.sidebar, bg="#f7f8fa")
        search.pack(fill=tk.X, padx=12, pady=(12, 8))
        entry = tk.Entry(
            search,
            textvariable=self.symbol_var,
            relief=tk.FLAT,
            font=("Microsoft YaHei UI", 10),
            bg="#ffffff",
            fg="#2b3035",
            insertbackground="#2b3035",
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7)
        entry.bind("<Return>", lambda _event: self.add_symbol())
        tk.Button(search, text="+", command=self.add_symbol, relief=tk.FLAT, bg="#ffffff", fg="#6a7178", font=("Microsoft YaHei UI", 12, "bold"), width=3).pack(side=tk.LEFT, padx=(6, 0), ipady=2)

        tools = tk.Frame(self.sidebar, bg="#f7f8fa")
        tools.pack(fill=tk.X, padx=14, pady=(2, 6))
        tk.Label(tools, text="关注列表", bg="#f7f8fa", fg="#8a9096", font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        tk.Button(tools, text="刷新", command=self.refresh_quotes, relief=tk.FLAT, bg="#f7f8fa", fg="#7b8289").pack(side=tk.RIGHT)

        list_area = tk.Canvas(self.sidebar, bg="#f7f8fa", highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.sidebar, orient=tk.VERTICAL, command=list_area.yview)
        list_area.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        list_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=(0, 10))
        holder = tk.Frame(list_area, bg="#f7f8fa")
        window_id = list_area.create_window((0, 0), window=holder, anchor="nw")
        holder.bind("<Configure>", lambda _event: list_area.configure(scrollregion=list_area.bbox("all")))
        list_area.bind("<Configure>", lambda event: list_area.itemconfigure(window_id, width=event.width))

        selected = self._selected_chart_secid()
        for quote in self.last_quotes:
            self._watch_card(holder, quote, quote.secid == selected)

    def _watch_card(self, parent: tk.Widget, quote: StockQuote, selected: bool) -> None:
        bg = "#e9eaec" if selected else "#ffffff"
        card = tk.Frame(parent, bg=bg, highlightbackground="#edf0f2", highlightthickness=1)
        card.pack(fill=tk.X, padx=0, pady=4)
        card.bind("<Button-1>", lambda _event, secid=quote.secid: self.select_modern_symbol(secid))

        top = tk.Frame(card, bg=bg)
        top.pack(fill=tk.X, padx=12, pady=(9, 0))
        tk.Label(top, text=quote.name, bg=bg, fg="#22272d", font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        pill_bg = "#ffe5e7" if (quote.change_percent or 0) >= 0 else "#def7e6"
        tk.Label(top, text=format_percent(quote.change_percent), bg=pill_bg, fg=self._quote_color(quote), font=("Microsoft YaHei UI", 9, "bold"), padx=8, pady=2).pack(side=tk.RIGHT)

        bottom = tk.Frame(card, bg=bg)
        bottom.pack(fill=tk.X, padx=12, pady=(2, 9))
        pin_text = "★" if quote.secid in self._pinned_set() else "☆"
        tk.Button(bottom, text=pin_text, command=lambda secid=quote.secid: self.toggle_pin(secid), relief=tk.FLAT, bg=bg, fg="#8a9096", width=2).pack(side=tk.LEFT)
        tk.Label(bottom, text=quote.code, bg=bg, fg="#9aa0a6", font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(2, 0))
        tk.Label(bottom, text=format_price(quote.price), bg=bg, fg="#858b92", font=("Microsoft YaHei UI", 10)).pack(side=tk.RIGHT)

    def _render_modern_main(self) -> None:
        for child in self.main_panel.winfo_children():
            child.destroy()

        top = tk.Frame(self.main_panel, bg="#ffffff")
        top.pack(fill=tk.X, padx=24, pady=(14, 8))
        self._render_index_strip(top)

        controls = tk.Frame(top, bg="#ffffff")
        controls.pack(side=tk.RIGHT)
        ttk.Button(controls, text="置顶", command=self.toggle_selected_pin).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(controls, text="移除", command=self.remove_selected).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(controls, text="刷新", command=self.refresh_quotes).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(controls, text="模式", bg="#ffffff", fg="#8a9096").pack(side=tk.LEFT, padx=(0, 5))
        mode_box = ttk.Combobox(controls, textvariable=self.mode_label_var, values=list(MODE_LABELS.values()), width=9, state="readonly")
        mode_box.pack(side=tk.LEFT, padx=(0, 8))
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self.set_mode_from_label())
        ttk.Button(controls, text="设置", command=self.open_settings).pack(side=tk.LEFT)

        tk.Frame(self.main_panel, bg="#eef1f4", height=1).pack(fill=tk.X, padx=24)

        quote = self._selected_quote() or (self.last_quotes[0] if self.last_quotes else None)
        if quote:
            self.chart_symbol_var.set(quote.secid)
            self._render_quote_header(quote)
        else:
            tk.Label(self.main_panel, text="添加自选后显示行情", bg="#ffffff", fg="#7b8289", font=("Microsoft YaHei UI", 16, "bold")).pack(expand=True)
            return

        tab_bar = tk.Frame(self.main_panel, bg="#ffffff")
        tab_bar.pack(fill=tk.X, padx=24, pady=(10, 0))
        for text in ("分时", "日K", "周K", "月K"):
            active = self.chart_period_var.get() == text
            label = tk.Label(
                tab_bar,
                text=text,
                bg="#ffffff",
                fg="#2f8cff" if active else "#30363d",
                font=("Microsoft YaHei UI", 11, "bold" if active else "normal"),
                padx=10,
                pady=7,
            )
            label.pack(side=tk.LEFT, padx=(0, 18))
            label.bind("<Button-1>", lambda _event, value=text: self.select_period(value))
            if active:
                label.bind("<Configure>", lambda event: event.widget.master.after(10, lambda widget=event.widget: self._underline_active_tab(widget)))

        self.chart_canvas = tk.Canvas(self.main_panel, bg="#ffffff", highlightthickness=0)
        self.chart_canvas.pack(fill=tk.BOTH, expand=True, padx=24, pady=(4, 10))
        self.chart_canvas.bind("<Configure>", lambda _event: self.draw_chart())
        self.chart_status_var = tk.StringVar(value="")
        tk.Label(self.main_panel, textvariable=self.chart_status_var, bg="#ffffff", fg="#9aa0a6", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24, pady=(0, 10))
        self.after(80, self.refresh_chart)

    def _render_index_strip(self, parent: tk.Widget) -> None:
        for quote in self.last_quotes[:5]:
            item = tk.Frame(parent, bg="#ffffff")
            item.pack(side=tk.LEFT, padx=(0, 32))
            tk.Label(item, text=_short_name(quote.name), bg="#ffffff", fg="#9aa0a6", font=("Microsoft YaHei UI", 10)).pack(anchor="w")
            tk.Label(item, text=format_price(quote.price), bg="#ffffff", fg="#252b31", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
            tk.Label(item, text=f"{format_signed(quote.change_amount)}({format_percent(quote.change_percent)})", bg="#ffffff", fg=self._quote_color(quote), font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")

    def _render_quote_header(self, quote: StockQuote) -> None:
        header = tk.Frame(self.main_panel, bg="#ffffff")
        header.pack(fill=tk.X, padx=24, pady=(8, 0))
        left = tk.Frame(header, bg="#ffffff")
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(left, text=quote.name, bg="#ffffff", fg="#24292f", font=("Microsoft YaHei UI", 21, "bold")).pack(anchor="w")
        tk.Label(left, text=f"{quote.code}", bg="#ffffff", fg="#8c939b", font=("Microsoft YaHei UI", 11)).pack(anchor="w")

        right = tk.Frame(header, bg="#ffffff")
        right.pack(side=tk.RIGHT)
        tk.Label(right, text=format_price(quote.price), bg="#ffffff", fg="#252b31", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="e")
        badge_bg = "#ffe5e7" if (quote.change_percent or 0) >= 0 else "#def7e6"
        tk.Label(right, text=format_percent(quote.change_percent), bg=badge_bg, fg=self._quote_color(quote), font=("Microsoft YaHei UI", 10, "bold"), padx=10, pady=3).pack(anchor="e", pady=(3, 0))

        stats = tk.Frame(self.main_panel, bg="#ffffff")
        stats.pack(fill=tk.X, padx=24, pady=(14, 8))
        stat_values = [
            ("今开", format_price(None if quote.previous_close is None else quote.previous_close + (quote.change_amount or 0))),
            ("昨收", format_price(quote.previous_close)),
            ("涨跌额", format_signed(quote.change_amount)),
            ("成交量", format_volume(quote.volume)),
            ("成交额", format_money(quote.amount)),
            ("时间", quote.update_time),
        ]
        for idx, (label, value) in enumerate(stat_values):
            cell = tk.Frame(stats, bg="#ffffff")
            cell.grid(row=idx // 3, column=idx % 3, sticky="w", padx=(0, 90), pady=4)
            tk.Label(cell, text=f"{label}：", bg="#ffffff", fg="#59616a", font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
            tk.Label(cell, text=value, bg="#ffffff", fg="#24292f", font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)

    def _underline_active_tab(self, widget: tk.Widget) -> None:
        x = widget.winfo_x()
        y = widget.winfo_y() + widget.winfo_height() - 2
        line = tk.Frame(widget.master, bg="#2f8cff", height=2, width=widget.winfo_width())
        line.place(x=x, y=y)

    def _render_chart_tab(self, parent: tk.Widget) -> None:
        layout = ttk.Frame(parent, style="Panel.TFrame")
        layout.pack(fill=tk.BOTH, expand=True)

        sidebar = ttk.Frame(layout, style="Panel.TFrame", padding=(0, 0, 10, 0))
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(sidebar, text="选择标的", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.chart_list = tk.Listbox(sidebar, width=18, height=16, activestyle="none", exportselection=False)
        self.chart_list.pack(fill=tk.Y, expand=True)
        self.chart_list.bind("<<ListboxSelect>>", lambda _event: self.select_chart_symbol())

        for index, symbol in enumerate(self.config_data.symbols):
            quote = next((item for item in self.last_quotes if item.secid == normalize_symbol(symbol)), None)
            text = f"{quote.name} {quote.code}" if quote else normalize_symbol(symbol)
            self.chart_list.insert(tk.END, text)
            if normalize_symbol(symbol) == normalize_symbol(self.chart_symbol_var.get()):
                self.chart_list.selection_set(index)

        main = ttk.Frame(layout, style="Panel.TFrame")
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        controls = ttk.Frame(main, style="Panel.TFrame")
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, textvariable=self.chart_symbol_var, font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        period_box = ttk.Combobox(controls, textvariable=self.chart_period_var, values=["分时", "日K", "周K", "月K"], width=8, state="readonly")
        period_box.pack(side=tk.LEFT, padx=(14, 8))
        period_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_chart())
        ttk.Button(controls, text="刷新图表", command=self.refresh_chart).pack(side=tk.LEFT)
        ttk.Label(controls, text="左侧选标的；上方切换分时/K线", style="Muted.TLabel").pack(side=tk.RIGHT)

        self.chart_canvas = tk.Canvas(main, bg="#fbfcfa", highlightthickness=1, highlightbackground="#d5ddd5")
        self.chart_canvas.pack(fill=tk.BOTH, expand=True)
        self.chart_canvas.bind("<Configure>", lambda _event: self.draw_chart())
        self.chart_status_var = tk.StringVar(value="选择标的后显示图表")
        ttk.Label(main, textvariable=self.chart_status_var, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        if self.chart_symbol_var.get():
            self.after(100, self.refresh_chart)

    def _render_mini_mode(self) -> None:
        root = tk.Frame(self, bg="#010203", highlightthickness=0)
        root.pack(fill=tk.BOTH, expand=True)
        self._bind_drag(root)
        root.bind("<Double-Button-1>", lambda _event: self.hide_to_tray())
        quotes = self._pinned_quotes()
        if not quotes:
            label = self._label(root, "在完整版置顶行情", 11, "#404841", bg="#010203")
            label.bind("<Double-Button-1>", lambda _event: self.hide_to_tray())
            label.pack(expand=True)
            return

        for quote in quotes[:3]:
            row = tk.Frame(root, bg="#010203")
            row.pack(fill=tk.X, padx=10, pady=(8 if quote is quotes[0] else 2, 0))
            self._bind_drag(row)
            row.bind("<Double-Button-1>", lambda _event: self.hide_to_tray())
            name_label = self._label(row, _short_name(quote.name), 10, "#29312b", bg="#010203", bold=True, width=8, anchor="w")
            price_label = self._label(row, format_price(quote.price), 13, "#29312b", bg="#010203", width=10, anchor="e")
            percent_label = self._label(row, format_percent(quote.change_percent), 10, self._quote_color(quote), bg="#010203", width=9, anchor="e")
            for label in (name_label, price_label, percent_label):
                label.bind("<Double-Button-1>", lambda _event: self.hide_to_tray())
            name_label.pack(side=tk.LEFT)
            price_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
            percent_label.pack(side=tk.RIGHT)

    def _render_widget_mode(self) -> None:
        root = ttk.Frame(self, style="Panel.TFrame", padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(root, style="Panel.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(header, text="桌面行情", font=("Microsoft YaHei UI", 12, "bold")).pack(side=tk.LEFT)

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

    def select_modern_symbol(self, secid: str) -> None:
        self.chart_symbol_var.set(secid)
        self.last_chart = None
        self._render_current_mode()

    def select_period(self, label: str) -> None:
        self.chart_period_var.set(label)
        self.last_chart = None
        self._render_current_mode()

    def toggle_pin(self, secid: str) -> None:
        pinned = self._pinned_set()
        if secid in pinned:
            self.config_data.pinned_symbols = [symbol for symbol in self.config_data.pinned_symbols if normalize_symbol(symbol) != secid]
        else:
            self.config_data.pinned_symbols.append(secid)
        self._save_current_config()
        self._render_current_mode()
        self._refresh_tray()

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
        if tree is not None:
            selected = tree.selection()
            if selected:
                return str(selected[0])
        return self._selected_chart_secid()

    def _selected_chart_secid(self) -> str | None:
        value = self.chart_symbol_var.get().strip()
        if not value:
            return None
        try:
            return normalize_symbol(value)
        except ValueError:
            return None

    def _selected_quote(self) -> StockQuote | None:
        selected = self._selected_chart_secid()
        if selected:
            for quote in self.last_quotes:
                if quote.secid == selected:
                    return quote
        return None

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
                    if self.config_data.mode == "table" and hasattr(self, "tree"):
                        self._fill_table(self.last_quotes)
                    else:
                        self._render_current_mode()
                    self._refresh_tray()
                elif kind == "chart":
                    self.last_chart = payload  # type: ignore[assignment]
                    self.is_chart_loading = False
                    self.draw_chart()
                    if self.last_chart:
                        self.chart_status_var.set(f"{self.last_chart.name} {period_label(self.last_chart.period)} 已更新")
                else:
                    self.status_var.set(str(payload))
                    self.is_chart_loading = False
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
        dialog.geometry("460x390")
        dialog.minsize(420, 320)
        dialog.resizable(True, True)
        dialog.configure(bg="#eef1ee")
        dialog.transient(self)
        dialog.grab_set()

        canvas = tk.Canvas(dialog, bg="#eef1ee", highlightthickness=0)
        scrollbar = ttk.Scrollbar(dialog, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        frame = ttk.Frame(canvas, style="Panel.TFrame", padding=16)
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
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

    def select_chart_symbol(self) -> None:
        selection = self.chart_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.config_data.symbols):
            return
        self.chart_symbol_var.set(normalize_symbol(self.config_data.symbols[index]))
        self.refresh_chart()

    def refresh_chart(self) -> None:
        if self.is_chart_loading:
            return
        symbol = self.chart_symbol_var.get().strip()
        if not symbol:
            return
        self.is_chart_loading = True
        if hasattr(self, "chart_status_var"):
            self.chart_status_var.set("图表加载中...")
        period = chart_period_value(self.chart_period_var.get())
        threading.Thread(target=self._fetch_chart_worker, args=(symbol, period), daemon=True).start()

    def _fetch_chart_worker(self, symbol: str, period: str) -> None:
        try:
            data = fetch_chart_data(symbol, period)
        except Exception as exc:
            self.result_queue.put(("error", f"图表加载失败: {exc}"))
        else:
            self.result_queue.put(("chart", data))

    def draw_chart(self) -> None:
        canvas = getattr(self, "chart_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        data = self.last_chart
        width = max(canvas.winfo_width(), 480)
        height = max(canvas.winfo_height(), 260)
        pad_left, pad_right, pad_top, pad_bottom = 54, 22, 28, 46
        volume_height = 58
        chart_bottom = height - pad_bottom - volume_height
        volume_top = chart_bottom + 18
        canvas.create_rectangle(0, 0, width, height, fill="#fbfcfa", outline="")
        if not data or not data.points:
            canvas.create_text(width / 2, height / 2, text="暂无图表数据", fill="#6b746c", font=("Microsoft YaHei UI", 12))
            return

        title = f"{data.name} {data.code}  {period_label(data.period)}"
        canvas.create_text(pad_left, 16, text=title, anchor="w", fill="#263029", font=("Microsoft YaHei UI", 10, "bold"))
        if data.period == "trend":
            self._draw_trend(canvas, data, width, chart_bottom, volume_top, pad_left, pad_right, pad_top, pad_bottom)
        else:
            self._draw_kline(canvas, data, width, chart_bottom, volume_top, pad_left, pad_right, pad_top, pad_bottom)

    def _draw_trend(self, canvas: tk.Canvas, data: ChartData, width: int, chart_bottom: int, volume_top: int, pad_left: int, pad_right: int, pad_top: int, pad_bottom: int) -> None:
        points = [point for point in data.points if isinstance(point, TrendPoint)]
        prices = [point.price for point in points]
        averages = [point.average for point in points if point.average is not None]
        values = prices + averages + ([data.previous_close] if data.previous_close else [])
        min_price, max_price = _range(values)
        max_volume = max((point.volume for point in points), default=1)
        plot_width = width - pad_left - pad_right
        plot_height = chart_bottom - pad_top
        self._draw_grid(canvas, width, chart_bottom, volume_top, pad_left, pad_right, pad_top, pad_bottom, min_price, max_price)

        if data.previous_close:
            y = _scale(data.previous_close, min_price, max_price, chart_bottom, pad_top)
            canvas.create_line(pad_left, y, width - pad_right, y, fill="#d5b46c", dash=(4, 3))
        price_points = []
        avg_points = []
        for idx, point in enumerate(points):
            x = pad_left + plot_width * idx / max(1, len(points) - 1)
            price_points.extend((x, _scale(point.price, min_price, max_price, chart_bottom, pad_top)))
            if point.average is not None:
                avg_points.extend((x, _scale(point.average, min_price, max_price, chart_bottom, pad_top)))
            bar_h = (point.volume / max_volume) * (pad_bottom + 28)
            canvas.create_line(x, height_for_volume(volume_top, pad_bottom, canvas) - bar_h, x, height_for_volume(volume_top, pad_bottom, canvas), fill="#cbd6cb")
        if len(price_points) >= 4:
            canvas.create_line(*price_points, fill="#3f6f8f", width=2)
        if len(avg_points) >= 4:
            canvas.create_line(*avg_points, fill="#b08945", width=1)

    def _draw_kline(self, canvas: tk.Canvas, data: ChartData, width: int, chart_bottom: int, volume_top: int, pad_left: int, pad_right: int, pad_top: int, pad_bottom: int) -> None:
        points = [point for point in data.points if isinstance(point, KLinePoint)]
        prices = [value for point in points for value in (point.open, point.close, point.high, point.low)]
        min_price, max_price = _range(prices)
        max_volume = max((point.volume for point in points), default=1)
        plot_width = width - pad_left - pad_right
        candle_w = max(3, min(10, plot_width / max(1, len(points)) * 0.62))
        self._draw_grid(canvas, width, chart_bottom, volume_top, pad_left, pad_right, pad_top, pad_bottom, min_price, max_price)

        for idx, point in enumerate(points):
            x = pad_left + plot_width * (idx + 0.5) / max(1, len(points))
            y_open = _scale(point.open, min_price, max_price, chart_bottom, pad_top)
            y_close = _scale(point.close, min_price, max_price, chart_bottom, pad_top)
            y_high = _scale(point.high, min_price, max_price, chart_bottom, pad_top)
            y_low = _scale(point.low, min_price, max_price, chart_bottom, pad_top)
            color = "#b63b38" if point.close >= point.open else "#247a53"
            canvas.create_line(x, y_high, x, y_low, fill=color)
            canvas.create_rectangle(x - candle_w / 2, min(y_open, y_close), x + candle_w / 2, max(y_open, y_close) + 1, outline=color, fill="#fbfcfa" if point.close >= point.open else color)
            bar_h = (point.volume / max_volume) * (pad_bottom + 28)
            base = height_for_volume(volume_top, pad_bottom, canvas)
            canvas.create_rectangle(x - candle_w / 2, base - bar_h, x + candle_w / 2, base, outline="", fill="#d6ddd5")

    def _draw_grid(self, canvas: tk.Canvas, width: int, chart_bottom: int, volume_top: int, pad_left: int, pad_right: int, pad_top: int, pad_bottom: int, min_price: float, max_price: float) -> None:
        for i in range(5):
            y = pad_top + (chart_bottom - pad_top) * i / 4
            value = max_price - (max_price - min_price) * i / 4
            canvas.create_line(pad_left, y, width - pad_right, y, fill="#e5ebe4")
            canvas.create_text(pad_left - 8, y, text=f"{value:.2f}", anchor="e", fill="#778278", font=("Microsoft YaHei UI", 8))
        canvas.create_line(pad_left, volume_top, width - pad_right, volume_top, fill="#e5ebe4")

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


def chart_period_value(label: str) -> str:
    return {
        "分时": "trend",
        "日K": "day",
        "周K": "week",
        "月K": "month",
    }.get(label, "trend")


def period_label(period: str) -> str:
    return {
        "trend": "分时",
        "day": "日K",
        "week": "周K",
        "month": "月K",
    }.get(period, period)


def _range(values: list[float | None]) -> tuple[float, float]:
    clean_values = [value for value in values if value is not None]
    if not clean_values:
        return 0.0, 1.0
    low = min(clean_values)
    high = max(clean_values)
    if low == high:
        padding = max(abs(low) * 0.01, 1)
        return low - padding, high + padding
    padding = (high - low) * 0.08
    return low - padding, high + padding


def _scale(value: float, low: float, high: float, bottom: int, top: int) -> float:
    return bottom - (value - low) / max(high - low, 0.000001) * (bottom - top)


def height_for_volume(volume_top: int, pad_bottom: int, canvas: tk.Canvas) -> int:
    return max(volume_top + 20, canvas.winfo_height() - pad_bottom + 28)


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
