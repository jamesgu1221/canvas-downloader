"""系统主题检测与 qfluentwidgets 主题桥接。

darkdetect.listener 阻塞在后台线程里监听系统主题切换；通过 Qt Signal 把事件
投递到主线程（Signal 默认 AutoConnection，跨线程自动 QueuedConnection），
避免直接用 QMetaObject.invokeMethod + 裸 Python 字符串（PySide6 下需要 Q_ARG
包裹，否则 silently no-op / TypeError）。

Phase 2：设置页新增「浅/深/跟随系统」切换。`_follow_system` 用来让后台监听器
在用户手动锁定主题时不再触发 setTheme；用户切回「跟随系统」后再生效。
"""

from __future__ import annotations

import threading

import darkdetect
from PySide6.QtCore import QObject, Signal
from qfluentwidgets import Theme, setTheme


_follow_system: bool = True
_signal_bus: "_ThemeSignalBus | None" = None
_is_dark_theme: bool = False


def set_follow_system(value: bool) -> None:
    global _follow_system
    _follow_system = value


def _current_theme() -> Theme:
    return Theme.DARK if darkdetect.isDark() else Theme.LIGHT


class _ThemeSignalBus(QObject):
    """Process-local notification bus for widgets that mirror the app theme."""

    theme_applied = Signal(bool)  # is_dark


def theme_signal_bus() -> _ThemeSignalBus:
    global _signal_bus
    if _signal_bus is None:
        _signal_bus = _ThemeSignalBus()
    return _signal_bus


def apply_theme(theme: Theme) -> None:
    global _is_dark_theme
    _is_dark_theme = theme == Theme.DARK
    setTheme(theme)
    theme_signal_bus().theme_applied.emit(_is_dark_theme)


def apply_system_theme() -> None:
    apply_theme(_current_theme())


def is_dark_theme() -> bool:
    return _is_dark_theme


class _ThemeBridge(QObject):
    """跨线程主题事件桥：后台线程 emit → 主线程 slot 应用 setTheme。"""

    theme_changed = Signal(str)  # "Dark" / "Light"

    def __init__(self) -> None:
        super().__init__()
        # 未指定 Qt.QueuedConnection，但 signal 从非 GUI 线程 emit 时
        # AutoConnection 会自动降级为 QueuedConnection，安全投递到主线程。
        self.theme_changed.connect(self._apply)

    @staticmethod
    def _apply(name: str) -> None:
        if not _follow_system:
            return
        apply_theme(Theme.DARK if name == "Dark" else Theme.LIGHT)


_bridge: _ThemeBridge | None = None


def install_theme_listener() -> None:
    """在后台线程订阅系统主题切换事件。幂等。"""
    global _bridge
    if _bridge is not None:
        return
    _bridge = _ThemeBridge()
    bridge = _bridge

    def _run() -> None:
        global _bridge
        try:
            darkdetect.listener(bridge.theme_changed.emit)
        except Exception:
            # Windows 7 / 某些 Linux DE 上无法实现，静默忽略
            if _bridge is bridge:
                _bridge = None

    threading.Thread(target=_run, daemon=True).start()
