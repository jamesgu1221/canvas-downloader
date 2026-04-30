"""主窗口：FluentWindow 子类，开启 Mica + 装配 NavigationInterface。"""

from __future__ import annotations

import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QCloseEvent, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import FluentWindow, MessageBox, NavigationItemPosition

from ._patches import apply_popup_shadow_patch
from .pages import (
    CoursesPage,
    HomePage,
    PathPage,
    SchedulePage,
    SettingsPage,
)
from .theme import apply_system_theme, install_theme_listener


class CanvasApp(FluentWindow):
    """Canvas 下载器主窗口。

    FluentWindow 已内置 NavigationInterface + StackedWidget；我们只需要
    addSubInterface 把每个页面挂上去。setMicaEffectEnabled(True) 在 Win11 22H2+
    直接开启 Mica 背景（Windows 10 会自动降级为纯色主题，不会报错）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Canvas 课件下载器")
        self.resize(1100, 720)
        self.setMinimumSize(QSize(860, 560))
        self.setWindowIcon(self._make_window_icon())

        self._build_pages()
        self._tune_navigation()
        self._enable_mica()

    def _build_pages(self) -> None:
        self.home_page = HomePage(self)
        self.schedule_page = SchedulePage(self)
        self.courses_page = CoursesPage(self)
        self.path_page = PathPage(self)
        self.settings_page = SettingsPage(self)

        self.addSubInterface(self.home_page, FIF.HOME, "主页")
        self.addSubInterface(self.schedule_page, FIF.DATE_TIME, "定时任务")
        self.addSubInterface(self.courses_page, FIF.EDUCATION, "课程管理")
        self.addSubInterface(self.path_page, FIF.FOLDER, "下载路径")
        self.addSubInterface(
            self.settings_page,
            FIF.SETTING,
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )

    def _make_window_icon(self) -> QIcon:
        """Create a fixed high-contrast app icon for light title bars."""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0078d4"))
        painter.drawRoundedRect(6, 6, 52, 52, 12, 12)

        pen = QPen(QColor("#ffffff"), 6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(32, 17, 32, 38)
        painter.drawLine(22, 29, 32, 39)
        painter.drawLine(42, 29, 32, 39)
        painter.drawLine(21, 47, 43, 47)
        painter.end()

        return QIcon(pixmap)

    def _tune_navigation(self) -> None:
        """关掉 StackedWidget 的整页滑动动画，改由每个页面自己在 showEvent
        里做淡入（见 ContentPage._play_fade_in）。
        这样标题即时到位、其余元素平滑出现，视觉更稳定。
        """
        sw = self.stackedWidget
        if hasattr(sw, "setAnimationEnabled"):
            sw.setAnimationEnabled(False)

    def _enable_mica(self) -> None:
        # 包装在 try 里：在老系统 / 不支持 DWM Mica 的环境下不应让 UI 崩溃
        try:
            self.setMicaEffectEnabled(True)
        except Exception as e:  # noqa: BLE001 — 任何 DWM 失败都降级
            print(f"[gui_qt] Mica 未启用：{e}", file=sys.stderr)

    def closeEvent(self, event: QCloseEvent) -> None:
        """关窗前若下载任务仍在运行，弹确认框并请求停止。"""
        if self.home_page.is_running():
            box = MessageBox("确认退出", "下载仍在进行，终止并退出吗？", self)
            if not box.exec():
                event.ignore()
                return
            self.home_page.shutdown()
        event.accept()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)

    apply_popup_shadow_patch()
    apply_system_theme()
    install_theme_listener()

    window = CanvasApp()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
