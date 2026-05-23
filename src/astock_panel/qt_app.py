from __future__ import annotations

import sys
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .config import ICON_PATH, AppConfig, load_config, save_config
from .market_data import ChartData, KLinePoint, QuoteError, StockQuote, TrendPoint, fetch_chart_data, fetch_quotes, normalize_symbol


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn: Callable, *args: object) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.result.emit(self.fn(*self.args))
        except Exception as exc:
            self.signals.error.emit(str(exc))


class ChartWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.data: ChartData | None = None
        self.setMinimumHeight(260)

    def set_data(self, data: ChartData | None) -> None:
        self.data = data
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if not self.data or not self.data.points:
            painter.setPen(QColor("#9aa0a6"))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无图表数据")
            return

        width = self.width()
        height = self.height()
        left, right, top, bottom = 58, 22, 24, 42
        volume_h = 58
        chart_bottom = height - bottom - volume_h
        volume_top = chart_bottom + 18

        painter.setPen(QPen(QColor("#e9edf1"), 1))
        prices = self._price_values()
        low, high = price_range(prices)
        for i in range(5):
            y = top + (chart_bottom - top) * i / 4
            painter.drawLine(left, int(y), width - right, int(y))
            value = high - (high - low) * i / 4
            painter.setPen(QColor("#a8afb7"))
            painter.drawText(4, int(y + 4), f"{value:.2f}")
            painter.setPen(QPen(QColor("#e9edf1"), 1))
        painter.drawLine(left, volume_top, width - right, volume_top)

        if self.data.period == "trend":
            self._draw_trend(painter, left, right, top, chart_bottom, volume_top, bottom, low, high)
        else:
            self._draw_kline(painter, left, right, top, chart_bottom, volume_top, bottom, low, high)

    def _price_values(self) -> list[float]:
        if not self.data:
            return []
        values: list[float] = []
        for point in self.data.points:
            if isinstance(point, TrendPoint):
                values.append(point.price)
                if point.average is not None:
                    values.append(point.average)
            else:
                values.extend([point.open, point.close, point.high, point.low])
        if self.data.previous_close:
            values.append(self.data.previous_close)
        return values

    def _draw_trend(self, painter: QPainter, left: int, right: int, top: int, chart_bottom: int, volume_top: int, bottom: int, low: float, high: float) -> None:
        assert self.data is not None
        points = [p for p in self.data.points if isinstance(p, TrendPoint)]
        if len(points) < 2:
            return
        max_volume = max((p.volume for p in points), default=1)
        plot_w = self.width() - left - right
        base = self.height() - bottom + 28
        price_path: list[tuple[float, float]] = []
        avg_path: list[tuple[float, float]] = []
        for idx, point in enumerate(points):
            x = left + plot_w * idx / max(1, len(points) - 1)
            y = scale(point.price, low, high, chart_bottom, top)
            price_path.append((x, y))
            if point.average is not None:
                avg_path.append((x, scale(point.average, low, high, chart_bottom, top)))
            bar_h = point.volume / max_volume * (bottom + 24)
            painter.setPen(QPen(QColor("#d8e0e7"), 1))
            painter.drawLine(int(x), int(base - bar_h), int(x), int(base))

        if self.data.previous_close:
            y = scale(self.data.previous_close, low, high, chart_bottom, top)
            pen = QPen(QColor("#d5b46c"), 1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(left, int(y), self.width() - right, int(y))

        draw_polyline(painter, price_path, QColor("#2f8cff"), 2)
        draw_polyline(painter, avg_path, QColor("#f28b32"), 1)

    def _draw_kline(self, painter: QPainter, left: int, right: int, top: int, chart_bottom: int, volume_top: int, bottom: int, low: float, high: float) -> None:
        assert self.data is not None
        points = [p for p in self.data.points if isinstance(p, KLinePoint)]
        if not points:
            return
        max_volume = max((p.volume for p in points), default=1)
        plot_w = self.width() - left - right
        candle_w = max(3, min(10, plot_w / max(1, len(points)) * 0.58))
        base = self.height() - bottom + 28
        for idx, point in enumerate(points):
            x = left + plot_w * (idx + 0.5) / max(1, len(points))
            color = QColor("#f02f3d") if point.close >= point.open else QColor("#22c55e")
            painter.setPen(QPen(color, 1))
            painter.setBrush(QBrush(QColor("#ffffff") if point.close >= point.open else color))
            y_open = scale(point.open, low, high, chart_bottom, top)
            y_close = scale(point.close, low, high, chart_bottom, top)
            y_high = scale(point.high, low, high, chart_bottom, top)
            y_low = scale(point.low, low, high, chart_bottom, top)
            painter.drawLine(int(x), int(y_high), int(x), int(y_low))
            painter.drawRect(int(x - candle_w / 2), int(min(y_open, y_close)), int(candle_w), max(1, int(abs(y_open - y_close))))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#d8e0e7"))
            bar_h = point.volume / max_volume * (bottom + 24)
            painter.drawRect(int(x - candle_w / 2), int(base - bar_h), int(candle_w), int(bar_h))


class StockCard(QFrame):
    def __init__(self, quote: StockQuote, selected: bool, pinned: bool, on_select: Callable[[str], None], on_pin: Callable[[str], None]) -> None:
        super().__init__()
        self.quote = quote
        self.on_select = on_select
        self.setObjectName("stockCardSelected" if selected else "stockCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        top = QHBoxLayout()
        name = QLabel(quote.name)
        name.setObjectName("cardName")
        badge = QLabel(format_percent(quote.change_percent))
        badge.setObjectName("redBadge" if (quote.change_percent or 0) >= 0 else "greenBadge")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(badge)
        bottom = QHBoxLayout()
        pin = QPushButton("★" if pinned else "☆")
        pin.setObjectName("iconButton")
        pin.clicked.connect(lambda: on_pin(quote.secid))
        code = QLabel(quote.code)
        code.setObjectName("muted")
        price = QLabel(format_price(quote.price))
        price.setObjectName("muted")
        bottom.addWidget(pin)
        bottom.addWidget(code)
        bottom.addStretch()
        bottom.addWidget(price)
        layout.addLayout(top)
        layout.addLayout(bottom)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.on_select(self.quote.secid)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config_data = load_config()
        self.thread_pool = QThreadPool.globalInstance()
        self.quotes: list[StockQuote] = []
        self.chart_data: ChartData | None = None
        self.selected_secid = self.config_data.symbols[0] if self.config_data.symbols else "1.000001"
        self.period = "trend"
        self.mode = self.config_data.mode
        self._drag_offset = None

        self.setWindowTitle("A Stock Panel")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1120, 720)
        self.setMinimumSize(760, 480)
        self.setStyleSheet(STYLESHEET)
        self._build_tray()
        self._build_ui()
        self.refresh_quotes()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_quotes)
        self.timer.start(max(10, self.config_data.refresh_seconds) * 1000)

    def _build_ui(self) -> None:
        if self.mode == "mini":
            self._build_mini()
        elif self.mode == "widget":
            self._build_widget()
        elif self.mode == "summary":
            self._build_summary()
        else:
            self._build_full()

    def _clear(self) -> None:
        old = self.centralWidget()
        if old:
            old.deleteLater()

    def _build_full(self) -> None:
        self._clear()
        flags = Qt.Window
        if self.config_data.topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowOpacity(1.0)
        root = QWidget()
        root.setObjectName("root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(300)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 12, 14)
        side_layout.setSpacing(10)
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入股票名称、代码")
        self.search_input.returnPressed.connect(self.add_symbol)
        add_btn = QPushButton("+")
        add_btn.setObjectName("roundButton")
        add_btn.clicked.connect(self.add_symbol)
        search_row.addWidget(self.search_input)
        search_row.addWidget(add_btn)
        side_layout.addLayout(search_row)
        header = QHBoxLayout()
        title = QLabel("关注列表")
        title.setObjectName("sectionTitle")
        refresh = QPushButton("刷新")
        refresh.setObjectName("ghostButton")
        refresh.clicked.connect(self.refresh_quotes)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(refresh)
        side_layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        cards = QWidget()
        self.cards_layout = QVBoxLayout(cards)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        scroll.setWidget(cards)
        side_layout.addWidget(scroll)

        main = QFrame()
        main.setObjectName("main")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(26, 16, 26, 18)
        main_layout.setSpacing(12)
        self.index_row = QHBoxLayout()
        main_layout.addLayout(self.index_row)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        main_layout.addWidget(divider)
        self.detail = QVBoxLayout()
        main_layout.addLayout(self.detail)
        self.period_row = QHBoxLayout()
        main_layout.addLayout(self.period_row)
        self.chart = ChartWidget()
        self.chart.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        main_layout.addWidget(self.chart, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        main_layout.addWidget(self.status_label)
        layout.addWidget(sidebar)
        layout.addWidget(main, 1)
        self.setCentralWidget(root)
        self._render_full_content()

    def _build_mini(self) -> None:
        self._clear()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(1.0)
        root = QWidget()
        root.setObjectName("miniRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        for quote in self._pinned_quotes()[:3]:
            row = QHBoxLayout()
            name = QLabel(short_name(quote.name))
            name.setObjectName("miniName")
            price = QLabel(format_price(quote.price))
            price.setObjectName("miniPrice")
            pct = QLabel(format_percent(quote.change_percent))
            pct.setObjectName("miniRed" if (quote.change_percent or 0) >= 0 else "miniGreen")
            row.addWidget(name)
            row.addStretch()
            row.addWidget(price)
            row.addWidget(pct)
            layout.addLayout(row)
        self.setCentralWidget(root)
        self.resize(330, 92)
        self.show()

    def _build_widget(self) -> None:
        self._clear()
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.config_data.topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.config_data.opacity)
        root = QWidget()
        root.setObjectName("widgetRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel("A 股关注")
        title.setObjectName("floatingTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        for quote in self._pinned_quotes()[:2]:
            chip = QLabel(f"{short_name(quote.name)} {format_percent(quote.change_percent)}")
            chip.setObjectName("redChip" if (quote.change_percent or 0) >= 0 else "greenChip")
            title_row.addWidget(chip)
        layout.addLayout(title_row)

        for quote in self.quotes[:6]:
            row = QFrame()
            row.setObjectName("widgetRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            name = QLabel(short_name(quote.name))
            name.setObjectName("widgetName")
            price = QLabel(format_price(quote.price))
            price.setObjectName("widgetPrice")
            change = QLabel(format_percent(quote.change_percent))
            change.setObjectName("redText" if (quote.change_percent or 0) >= 0 else "greenText")
            row_layout.addWidget(name)
            row_layout.addStretch()
            row_layout.addWidget(price)
            row_layout.addWidget(change)
            layout.addWidget(row)

        self.setCentralWidget(root)
        self.resize(420, 80 + min(len(self.quotes), 6) * 54)
        self.show()

    def _build_summary(self) -> None:
        self._clear()
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.config_data.topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.config_data.opacity)
        root = QWidget()
        root.setObjectName("summaryRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(16)

        quotes = self._pinned_quotes() or self.quotes[:2]
        for quote in quotes[:4]:
            name = QLabel(short_name(quote.name))
            name.setObjectName("summaryName")
            price = QLabel(format_price(quote.price))
            price.setObjectName("summaryPrice")
            change = QLabel(format_percent(quote.change_percent))
            change.setObjectName("miniRed" if (quote.change_percent or 0) >= 0 else "miniGreen")
            layout.addWidget(name)
            layout.addWidget(price)
            layout.addWidget(change)
        layout.addStretch()

        self.setCentralWidget(root)
        self.resize(620, 48)
        self.show()

    def _render_full_content(self) -> None:
        if not hasattr(self, "cards_layout"):
            return
        clear_layout(self.cards_layout)
        pinned = set(normalize_symbol(s) for s in self.config_data.pinned_symbols)
        for quote in self.quotes:
            self.cards_layout.addWidget(StockCard(quote, quote.secid == self.selected_secid, quote.secid in pinned, self.select_symbol, self.toggle_pin))
        self.cards_layout.addStretch()

        clear_layout(self.index_row)
        for quote in self.quotes[:5]:
            item = QVBoxLayout()
            name = QLabel(short_name(quote.name))
            name.setObjectName("indexName")
            price = QLabel(format_price(quote.price))
            price.setObjectName("indexPrice")
            change = QLabel(f"{format_signed(quote.change_amount)}({format_percent(quote.change_percent)})")
            change.setObjectName("redText" if (quote.change_percent or 0) >= 0 else "greenText")
            item.addWidget(name)
            item.addWidget(price)
            item.addWidget(change)
            self.index_row.addLayout(item)
        self.index_row.addStretch()
        for text, callback in (("置顶", self.toggle_selected_pin), ("移除", self.remove_selected), ("设置", self.open_settings)):
            button = QPushButton(text)
            button.setObjectName("ghostButton")
            button.clicked.connect(callback)
            self.index_row.addWidget(button)
        mode = QComboBox()
        mode.addItems(["完整版", "极简置顶", "桌面组件", "市场摘要"])
        mode.setCurrentText({"table": "完整版", "mini": "极简置顶", "widget": "桌面组件", "summary": "市场摘要"}.get(self.mode, "完整版"))
        mode.currentTextChanged.connect(self.switch_mode_label)
        self.index_row.addWidget(mode)

        clear_layout(self.detail)
        quote = self.selected_quote()
        if quote:
            top = QHBoxLayout()
            left = QVBoxLayout()
            name = QLabel(quote.name)
            name.setObjectName("heroName")
            code = QLabel(f"{quote.code}")
            code.setObjectName("heroCode")
            left.addWidget(name)
            left.addWidget(code)
            right = QVBoxLayout()
            right.setAlignment(Qt.AlignRight)
            price = QLabel(format_price(quote.price))
            price.setObjectName("heroPrice")
            badge = QLabel(format_percent(quote.change_percent))
            badge.setObjectName("redBadge" if (quote.change_percent or 0) >= 0 else "greenBadge")
            right.addWidget(price, alignment=Qt.AlignRight)
            right.addWidget(badge, alignment=Qt.AlignRight)
            top.addLayout(left)
            top.addStretch()
            top.addLayout(right)
            self.detail.addLayout(top)
            grid = QGridLayout()
            stats = [
                ("昨收", format_price(quote.previous_close)),
                ("涨跌额", format_signed(quote.change_amount)),
                ("成交量", format_volume(quote.volume)),
                ("成交额", format_money(quote.amount)),
                ("时间", quote.update_time),
            ]
            for idx, (label, value) in enumerate(stats):
                cell = QLabel(f"{label}： <b>{value}</b>")
                cell.setObjectName("stat")
                grid.addWidget(cell, idx // 3, idx % 3)
            self.detail.addLayout(grid)

        clear_layout(self.period_row)
        group = QButtonGroup(self)
        for label, value in (("分时", "trend"), ("日K", "day"), ("周K", "week"), ("月K", "month")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(self.period == value)
            button.setObjectName("tabButton")
            button.clicked.connect(lambda _checked=False, p=value: self.select_period(p))
            group.addButton(button)
            self.period_row.addWidget(button)
        self.period_row.addStretch()

    def refresh_quotes(self) -> None:
        worker = Worker(fetch_quotes, list(self.config_data.symbols))
        worker.signals.result.connect(self.on_quotes)
        worker.signals.error.connect(lambda message: self.set_status(f"行情失败：{message}"))
        self.thread_pool.start(worker)

    def refresh_chart(self) -> None:
        self.set_status("图表加载中...")
        worker = Worker(fetch_chart_data, self.selected_secid, self.period)
        worker.signals.result.connect(self.on_chart)
        worker.signals.error.connect(lambda message: self.set_status(f"图表失败：{message}"))
        self.thread_pool.start(worker)

    def on_quotes(self, result: object) -> None:
        self.quotes = result  # type: ignore[assignment]
        if self.selected_secid not in [q.secid for q in self.quotes] and self.quotes:
            self.selected_secid = self.quotes[0].secid
        if self.mode == "mini":
            self._build_mini()
        elif self.mode == "widget":
            self._build_widget()
        elif self.mode == "summary":
            self._build_summary()
        else:
            self._render_full_content()
        self.update_tray()
        self.refresh_chart()

    def on_chart(self, result: object) -> None:
        self.chart_data = result  # type: ignore[assignment]
        if hasattr(self, "chart"):
            self.chart.set_data(self.chart_data)
        if self.chart_data:
            self.set_status(f"{self.chart_data.name} {period_label(self.chart_data.period)} 已更新")

    def select_symbol(self, secid: str) -> None:
        self.selected_secid = secid
        self._render_full_content()
        self.refresh_chart()

    def select_period(self, period: str) -> None:
        self.period = period
        self._render_full_content()
        self.refresh_chart()

    def add_symbol(self) -> None:
        raw = self.search_input.text().strip()
        try:
            secid = normalize_symbol(raw)
        except Exception as exc:
            QMessageBox.warning(self, "代码格式不正确", str(exc))
            return
        if secid not in self.config_data.symbols:
            self.config_data.symbols.append(secid)
            save_config(self.config_data)
        self.search_input.clear()
        self.refresh_quotes()

    def remove_selected(self) -> None:
        self.config_data.symbols = [s for s in self.config_data.symbols if normalize_symbol(s) != self.selected_secid]
        self.config_data.pinned_symbols = [s for s in self.config_data.pinned_symbols if normalize_symbol(s) != self.selected_secid]
        save_config(self.config_data)
        self.refresh_quotes()

    def toggle_selected_pin(self) -> None:
        self.toggle_pin(self.selected_secid)

    def toggle_pin(self, secid: str) -> None:
        pinned = set(normalize_symbol(s) for s in self.config_data.pinned_symbols)
        if secid in pinned:
            self.config_data.pinned_symbols = [s for s in self.config_data.pinned_symbols if normalize_symbol(s) != secid]
        else:
            self.config_data.pinned_symbols.append(secid)
        save_config(self.config_data)
        self._render_full_content()
        self.update_tray()

    def selected_quote(self) -> StockQuote | None:
        return next((q for q in self.quotes if q.secid == self.selected_secid), None)

    def _pinned_quotes(self) -> list[StockQuote]:
        pinned = set(normalize_symbol(s) for s in self.config_data.pinned_symbols)
        return [q for q in self.quotes if q.secid in pinned]

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(str(ICON_PATH)) if ICON_PATH.exists() else self.style().standardIcon(QStyle.SP_ComputerIcon), self)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()
        self.update_tray()

    def update_tray(self) -> None:
        menu = QMenu()
        open_action = QAction("打开面板", self)
        open_action.triggered.connect(self.show_full)
        menu.addAction(open_action)
        for quote in self._pinned_quotes():
            action = QAction(f"{short_name(quote.name)} {format_price(quote.price)} {format_percent(quote.change_percent)}", self)
            action.setEnabled(False)
            menu.addAction(action)
        menu.addSeparator()
        mini_action = QAction("极简置顶", self)
        mini_action.triggered.connect(lambda: self.switch_mode("mini"))
        menu.addAction(mini_action)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_full()

    def switch_mode_label(self, label: str) -> None:
        mapping = {"完整版": "table", "极简置顶": "mini", "桌面组件": "widget", "市场摘要": "summary"}
        self.switch_mode(mapping.get(label, "table"))

    def switch_mode(self, mode: str) -> None:
        self.mode = mode
        self.config_data.mode = mode
        save_config(self.config_data)
        if mode == "mini":
            self._build_mini()
        elif mode == "widget":
            self._build_widget()
        elif mode == "summary":
            self._build_summary()
        else:
            self._build_full()
        self.show()

    def show_full(self) -> None:
        self.switch_mode("table")
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config_data, self)
        if dialog.exec() == QDialog.Accepted:
            self.config_data = dialog.config_data
            save_config(self.config_data)
            self.timer.setInterval(max(10, self.config_data.refresh_seconds) * 1000)
            self.switch_mode(self.config_data.mode)

    def set_status(self, text: str) -> None:
        if hasattr(self, "status_label"):
            self.status_label.setText(text)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if self.mode == "mini":
            self.hide()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self.mode in {"mini", "widget", "summary"} and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()
        self.hide()


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_data = config
        self.setWindowTitle("设置")
        self.setFixedSize(430, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        form = QGridLayout()
        self.interval = QLineEdit(str(config.refresh_seconds))
        self.opacity = QLineEdit(str(config.opacity))
        self.topmost = QCheckBox("窗口置顶")
        self.topmost.setChecked(config.topmost)
        form.addWidget(QLabel("刷新间隔"), 0, 0)
        form.addWidget(self.interval, 0, 1)
        form.addWidget(QLabel("透明度"), 1, 0)
        form.addWidget(self.opacity, 1, 1)
        form.addWidget(self.topmost, 2, 1)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        save = QPushButton("保存")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept_settings)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def accept_settings(self) -> None:
        try:
            refresh_seconds = int(self.interval.text() or "30")
            opacity = float(self.opacity.text() or "1")
        except ValueError:
            QMessageBox.warning(self, "设置格式不正确", "刷新间隔请输入整数，透明度请输入 0.45 到 1 之间的小数。")
            return
        self.config_data.refresh_seconds = max(10, refresh_seconds)
        self.config_data.opacity = max(0.45, min(1.0, opacity))
        self.config_data.topmost = self.topmost.isChecked()
        self.accept()


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def draw_polyline(painter: QPainter, points: list[tuple[float, float]], color: QColor, width: int) -> None:
    if len(points) < 2:
        return
    painter.setPen(QPen(color, width))
    for left, right in zip(points, points[1:]):
        painter.drawLine(int(left[0]), int(left[1]), int(right[0]), int(right[1]))


def price_range(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 1.0
    low = min(values)
    high = max(values)
    if low == high:
        pad = max(abs(low) * 0.01, 1)
        return low - pad, high + pad
    pad = (high - low) * 0.08
    return low - pad, high + pad


def scale(value: float, low: float, high: float, bottom: int, top: int) -> float:
    return bottom - (value - low) / max(high - low, 0.000001) * (bottom - top)


def short_name(name: str) -> str:
    if name == "上证指数":
        return "上证指数"
    if name == "深证成指":
        return "深证成指"
    return name


def period_label(period: str) -> str:
    return {"trend": "分时", "day": "日K", "week": "周K", "month": "月K"}.get(period, period)


def format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}".rstrip("0").rstrip(".")


def format_signed(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{'+' if value > 0 else ''}{value:.2f}".rstrip("0").rstrip(".")


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{'+' if value > 0 else ''}{value:.2f}%"


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


STYLESHEET = """
QWidget#root { background: #ffffff; }
QFrame#sidebar { background: #f7f9fc; border-right: 1px solid #e7ebf0; }
QFrame#main { background: #ffffff; }
QLineEdit { border: 1px solid #dde3ea; border-radius: 12px; padding: 9px 12px; background: white; color: #252b31; }
QPushButton { border: 0; border-radius: 10px; padding: 8px 12px; background: #eef2f7; color: #30363d; font-weight: 600; }
QPushButton:hover { background: #e1e7ef; }
QPushButton#ghostButton { background: transparent; color: #69727d; }
QPushButton#ghostButton:hover { background: #f1f4f7; }
QPushButton#roundButton { min-width: 34px; max-width: 34px; border-radius: 17px; background: #ffffff; color: #7b8289; }
QPushButton#iconButton { background: transparent; color: #89919a; padding: 0; }
QPushButton#tabButton { background: transparent; border-radius: 0; padding: 8px 12px; color: #30363d; }
QPushButton#tabButton:checked { color: #2f8cff; border-bottom: 2px solid #2f8cff; }
QFrame#stockCard, QFrame#stockCardSelected { border: 1px solid #edf0f2; border-radius: 12px; background: #ffffff; }
QFrame#stockCardSelected { background: #edf4ff; border: 1px solid #c9ddff; }
QLabel#sectionTitle { color: #8b929a; font-weight: 700; }
QLabel#cardName { color: #22272d; font-size: 14px; font-weight: 700; }
QLabel#muted, QLabel#indexName, QLabel#heroCode { color: #8b929a; }
QLabel#indexPrice { color: #252b31; font-size: 24px; font-weight: 800; }
QLabel#heroName { color: #24292f; font-size: 30px; font-weight: 900; }
QLabel#heroPrice { color: #252b31; font-size: 24px; font-weight: 900; }
QLabel#redText, QLabel#miniRed { color: #f02f3d; font-weight: 700; }
QLabel#greenText, QLabel#miniGreen { color: #22c55e; font-weight: 700; }
QLabel#redBadge { background: #ffe5e7; color: #f02f3d; border-radius: 11px; padding: 3px 9px; font-weight: 800; }
QLabel#greenBadge { background: #def7e6; color: #22c55e; border-radius: 11px; padding: 3px 9px; font-weight: 800; }
QLabel#stat { color: #4f5862; font-size: 14px; }
QFrame#divider { background: #edf0f2; }
QWidget#miniRoot { background: transparent; }
QLabel#miniName { color: #252b31; font-weight: 800; }
QLabel#miniPrice { color: #252b31; font-size: 17px; font-weight: 800; }
QWidget#widgetRoot { background: rgba(250, 252, 255, 235); border: 1px solid rgba(210, 218, 229, 210); border-radius: 16px; }
QFrame#widgetRow { background: rgba(255, 255, 255, 225); border: 1px solid rgba(232, 237, 243, 220); border-radius: 12px; }
QLabel#floatingTitle { color: #22272d; font-size: 16px; font-weight: 900; }
QLabel#widgetName { color: #252b31; font-weight: 800; }
QLabel#widgetPrice { color: #252b31; font-size: 18px; font-weight: 900; }
QLabel#redChip { background: #ffe5e7; color: #f02f3d; border-radius: 10px; padding: 3px 8px; font-weight: 800; }
QLabel#greenChip { background: #def7e6; color: #22c55e; border-radius: 10px; padding: 3px 8px; font-weight: 800; }
QWidget#summaryRoot { background: rgba(255, 255, 255, 232); border: 1px solid rgba(218, 224, 232, 220); border-radius: 14px; }
QLabel#summaryName { color: #7b838c; font-weight: 800; }
QLabel#summaryPrice { color: #22272d; font-size: 16px; font-weight: 900; }
QScrollArea { border: 0; background: transparent; }
QComboBox { border: 1px solid #dde3ea; border-radius: 10px; padding: 6px 10px; background: white; }
QDialog { background: #ffffff; }
QCheckBox { color: #4f5862; font-weight: 600; }
"""


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
