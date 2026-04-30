"""Application-wide native window policy for qfluentwidgets overlays.

On Windows 11, a Mica-enabled main window can cause translucent top-level
overlay windows to inherit a DWM system backdrop around their client area.
qfluentwidgets also adds QGraphicsDropShadowEffect to many translucent popup
containers; on layered popup windows that shadow margin can be composited as a
faint rectangular frame.

This module keeps the fix at the application window boundary instead of
monkey-patching individual qfluentwidgets controls.  New popup-like controls are
covered as long as they use normal Qt top-level overlay window flags.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import byref, c_int

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget


_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWCP_DONOTROUND = 1
_DWMSBT_NONE = 1
_WINDOW_TYPE_MASK = 0x000000FF

_installed = False
_overlay_filter: "_OverlayWindowPolicy | None" = None


def install_overlay_window_policy(app: QApplication) -> None:
    """Install the global overlay policy once.

    The policy is deliberately non-fatal: if PySide, DWM, or a specific widget
    behaves unexpectedly, startup and normal rendering must continue.
    """
    global _installed, _overlay_filter
    if _installed or sys.platform != "win32":
        return

    _installed = True
    _overlay_filter = _OverlayWindowPolicy(app)
    app.installEventFilter(_overlay_filter)


class _OverlayWindowPolicy(QObject):
    def eventFilter(self, obj, event):  # noqa: N802
        try:
            if (
                isinstance(obj, QWidget)
                and obj.isWindow()
                and _event_type_is(event, "WinIdChange", "Show")
                and self._is_overlay_window(obj)
            ):
                self._apply_overlay_policy(obj)
        except Exception:  # noqa: BLE001
            pass

        return super().eventFilter(obj, event)

    def _is_overlay_window(self, widget: QWidget) -> bool:
        window_type = _qt_int(widget.windowFlags()) & _WINDOW_TYPE_MASK
        overlay_types = {
            _qt_int(Qt.WindowType.Popup),
            _qt_int(Qt.WindowType.Tool),
            _qt_int(Qt.WindowType.ToolTip),
            _qt_int(Qt.WindowType.SplashScreen),
        }
        if window_type in overlay_types:
            return True

        return (
            widget.parentWidget() is not None
            and widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        )

    def _apply_overlay_policy(self, widget: QWidget) -> None:
        _disable_dwm_backdrop(widget.winId())
        _strip_overlay_effects(widget)
        # Some qfluentwidgets popups recreate their shadow after showEvent.
        QTimer.singleShot(0, lambda w=widget: _apply_delayed_overlay_policy(w))
        QTimer.singleShot(50, lambda w=widget: _apply_delayed_overlay_policy(w))


def _apply_delayed_overlay_policy(widget: QWidget) -> None:
    try:
        if widget.isWindow():
            _disable_dwm_backdrop(widget.winId())
            _strip_overlay_effects(widget)
    except RuntimeError:
        pass


def _disable_dwm_backdrop(hwnd: int) -> None:
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(
            int(hwnd),
            _DWMWA_SYSTEMBACKDROP_TYPE,
            byref(c_int(_DWMSBT_NONE)),
            4,
        )
        dwm.DwmSetWindowAttribute(
            int(hwnd),
            _DWMWA_WINDOW_CORNER_PREFERENCE,
            byref(c_int(_DWMWCP_DONOTROUND)),
            4,
        )
    except Exception:  # noqa: BLE001
        pass


def _strip_overlay_effects(widget: QWidget) -> None:
    try:
        widget.setGraphicsEffect(None)
        for child in widget.findChildren(QWidget):
            child.setGraphicsEffect(None)
    except RuntimeError:
        pass


def _qt_int(value) -> int:
    if hasattr(value, "value"):
        return int(value.value)
    return int(value)


def _event_type_is(event, *names: str) -> bool:
    event_type = event.type()
    for name in names:
        qt_type = getattr(getattr(QEvent, "Type", QEvent), name, None)
        if qt_type is None:
            qt_type = getattr(QEvent, name, None)
        if qt_type is not None and event_type == qt_type:
            return True
    return False
