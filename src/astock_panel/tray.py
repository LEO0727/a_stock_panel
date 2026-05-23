from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable


if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
ICON_PNG_PATH = PROJECT_ROOT / "assets" / "app_icon.png"


class TrayUnavailable(RuntimeError):
    pass


class TrayController:
    def __init__(
        self,
        show_window: Callable[[], None],
        hide_window: Callable[[], None],
        quit_app: Callable[[], None],
        switch_mode: Callable[[str], None],
        get_pinned_lines: Callable[[], list[str]],
    ) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise TrayUnavailable("系统托盘依赖未安装，请先运行 install_deps.bat") from exc

        self.pystray = pystray
        self.show_window = show_window
        self.hide_window = hide_window
        self.quit_app = quit_app
        self.switch_mode = switch_mode
        self.get_pinned_lines = get_pinned_lines
        self.icon = pystray.Icon("a_stock_panel", self._build_image(), "Market Summary")
        self.refresh_menu()

    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        self.icon.stop()

    def refresh_menu(self) -> None:
        pystray = self.pystray
        pinned_items = [
            pystray.MenuItem(line, lambda _icon, _item: None, enabled=False)
            for line in self.get_pinned_lines()
        ]
        if not pinned_items:
            pinned_items = [pystray.MenuItem("没有置顶行情", lambda _icon, _item: None, enabled=False)]

        self.icon.menu = pystray.Menu(
            pystray.MenuItem("打开面板", lambda _icon, _item: self.show_window(), default=True),
            pystray.MenuItem("隐藏面板", lambda _icon, _item: self.hide_window()),
            pystray.Menu.SEPARATOR,
            *pinned_items,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("完整管理", lambda _icon, _item: self.switch_mode("table")),
            pystray.MenuItem("极简置顶", lambda _icon, _item: self.switch_mode("mini")),
            pystray.MenuItem("桌面组件", lambda _icon, _item: self.switch_mode("widget")),
            pystray.MenuItem("市场摘要", lambda _icon, _item: self.switch_mode("summary")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda _icon, _item: self.quit_app()),
        )
        self.icon.title = self._tooltip()

    def _tooltip(self) -> str:
        lines = self.get_pinned_lines()
        if not lines:
            return "Market Summary"
        return "Market Summary\n" + "\n".join(lines[:4])

    def _build_image(self):
        from PIL import Image, ImageDraw

        if ICON_PNG_PATH.exists():
            return Image.open(ICON_PNG_PATH)

        image = Image.new("RGB", (64, 64), "#f0f2ef")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((9, 12, 55, 52), radius=8, outline="#4d5951", width=4, fill="#f7f8f6")
        draw.line((18, 40, 28, 32, 38, 35, 49, 24), fill="#4d5951", width=4)
        return image
