"""Application-wide font tuning for qfluentwidgets controls."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel


_installed = False
_font_filter: "_FontPolicy | None" = None


def install_font_policy(app: QApplication) -> None:
    """Install title font tuning once for current and future widgets."""
    global _installed, _font_filter
    if _installed:
        return
    _installed = True

    _font_filter = _FontPolicy(app)
    app.installEventFilter(_font_filter)


class _FontPolicy(QObject):
    def eventFilter(self, obj, event):  # noqa: N802
        try:
            if (
                isinstance(obj, QLabel)
                and obj.objectName() == "headerLabel"
                and _event_type_is(event, "Polish", "Show")
            ):
                _tune_header_label(obj)
        except Exception:  # noqa: BLE001
            pass
        return super().eventFilter(obj, event)


def _tune_header_label(label: QLabel) -> None:
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(15)
    font.setWeight(QFont.Weight.DemiBold)
    font.setKerning(True)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    label.setFont(font)
    QTimer.singleShot(0, lambda w=label: _retune_header_label(w))
    QTimer.singleShot(80, lambda w=label: _retune_header_label(w))


def _retune_header_label(label: QLabel) -> None:
    try:
        font = label.font()
        font.setFamily("Microsoft YaHei UI")
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.DemiBold)
        font.setKerning(True)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        label.setFont(font)
    except RuntimeError:
        pass


def _event_type_is(event, *names: str) -> bool:
    event_type = event.type()
    for name in names:
        qt_type = getattr(getattr(QEvent, "Type", QEvent), name, None)
        if qt_type is None:
            qt_type = getattr(QEvent, name, None)
        if qt_type is not None and event_type == qt_type:
            return True
    return False
