"""qfluentwidgets 渲染补丁：消除 popup 周围的灰色矩形外框。

现象：在 Win11（含 25H2）+ PySide6 + 主窗口启用 Mica 的组合下，`RoundMenu`
（ComboBox 下拉用的就是它）和 `ToolTip`（导航悬停时弹出的）周围会出现一圈
比 popup 本体大一圈的灰色矩形外框。

根因：popup 窗口虽然设了 `Qt.WA_TranslucentBackground` + `Qt.NoDropShadowWindowHint`，
但 Win11 DWM 仍会按窗口的 `DWMWA_SYSTEMBACKDROP_TYPE` 给客户区外侧（边距区域）
填充默认系统底纹。当父窗口启用了 Mica 时，整套 DWM 默认值会让 popup 显示
出一圈半透明灰色矩形。

修法：
1) 直接对 popup 的窗口句柄设 `DWMWA_SYSTEMBACKDROP_TYPE = DWMSBT_NONE (1)`
   告诉 DWM"该窗口没有系统底纹"。
2) 设 `DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_DONOTROUND (1)` 关掉 Win11
   自动圆角，避免外框残留。
3) 顺手把 qfluentwidgets 自挂的 `QGraphicsDropShadowEffect` 也拿掉——它在
   layered popup 边缘会被裁出硬阴影，多余。

这套补丁仅作用于 popup 类窗口，不影响主窗口的 Mica。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import byref, c_int

from PySide6.QtCore import QEvent
from qfluentwidgets.components.widgets import menu as _menu
from qfluentwidgets.components.widgets import tool_tip as _tool_tip


# DWM 常量（Win11）
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMSBT_NONE = 1
_DWMWCP_DONOTROUND = 1


_applied = False


def apply_popup_shadow_patch() -> None:
    """幂等：把 RoundMenu / ToolTip 的 popup 渲染交给我们的 hWnd 处理。"""
    global _applied
    if _applied:
        return
    _applied = True

    if sys.platform != "win32":
        return

    _patch_round_menu()
    _patch_tooltip()


def _strip_dwm_backdrop(hwnd: int) -> None:
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(
            int(hwnd), _DWMWA_SYSTEMBACKDROP_TYPE, byref(c_int(_DWMSBT_NONE)), 4
        )
        dwm.DwmSetWindowAttribute(
            int(hwnd), _DWMWA_WINDOW_CORNER_PREFERENCE,
            byref(c_int(_DWMWCP_DONOTROUND)), 4
        )
    except Exception:  # noqa: BLE001 — 任何 DWM 失败都降级
        pass


def _patch_round_menu() -> None:
    # 1) 关掉 Qt 图形特效阴影（layered popup 边缘会硬裁，徒增伪影）
    def _no_shadow(self, blurRadius=30, offset=(0, 8), color=None):  # noqa: ARG001
        self.shadowEffect = None
        self.view.setGraphicsEffect(None)

    _menu.RoundMenu.setShadowEffect = _no_shadow

    # 2) winId 就绪 / 显示时，强制清掉 DWM 的默认系统底纹和自动圆角
    _orig_event = _menu.RoundMenu.event

    def _patched_event(self, e):
        if e.type() in (QEvent.WinIdChange, QEvent.Show):
            _strip_dwm_backdrop(self.winId())
        return _orig_event(self, e)

    _menu.RoundMenu.event = _patched_event


def _patch_tooltip() -> None:
    _orig_init = _tool_tip.ToolTip.__init__

    def _patched_init(self, text="", parent=None):
        _orig_init(self, text, parent)
        self.container.setGraphicsEffect(None)
        self.shadowEffect = None

    _tool_tip.ToolTip.__init__ = _patched_init

    _orig_show = _tool_tip.ToolTip.showEvent

    def _patched_show(self, e):
        _strip_dwm_backdrop(self.winId())
        _orig_show(self, e)

    _tool_tip.ToolTip.showEvent = _patched_show
