"""Structured log panel used by the home page."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel


LogLevel = Literal["info", "success", "warning", "error"]


_LEVEL_COLORS: dict[LogLevel, tuple[str, str]] = {
    "info": ("#666666", "#b8b8b8"),
    "success": ("#137333", "#6fd58c"),
    "warning": ("#9a6400", "#e7b866"),
    "error": ("#b3261e", "#ff8a80"),
}


class LogPanel(QWidget):
    """A compact, scrollable list of sync log entries.

    The previous home page treated logs as a terminal text buffer. This widget
    keeps display behavior local to the log area and renders each message as a
    GUI entry instead.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

        self._rows: list[QWidget] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setMinimumHeight(220)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea QWidget { background: transparent; }"
        )

        self._host = QWidget(self._scroll)
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(0, 2, 8, 2)
        self._layout.setSpacing(4)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._placeholder = CaptionLabel("点击「立即运行」后，下载日志将显示在这里。", self._host)
        self._placeholder.setTextColor("#888888", "#aaaaaa")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._placeholder.setWordWrap(True)
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch(1)

        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll)

    def append(self, text: str, level: LogLevel = "info") -> None:
        lines = [line for line in text.rstrip("\n").splitlines() if line.strip()]
        if not lines and text.strip():
            lines = [text.strip()]
        for line in lines:
            self._append_line(line, level)
        if lines:
            QTimer.singleShot(0, self._scroll_to_bottom)

    def clear(self) -> None:
        for row in self._rows:
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows = []
        self._placeholder.setVisible(True)

    def _append_line(self, text: str, level: LogLevel) -> None:
        self._placeholder.setVisible(False)

        row = QWidget(self._host)
        row.setObjectName("logRow")
        row.setStyleSheet(
            "#logRow {"
            "  background: rgba(127, 127, 127, 0.08);"
            "  border-radius: 6px;"
            "}"
        )
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        time_label = CaptionLabel(datetime.now().strftime("%H:%M:%S"), row)
        time_label.setTextColor("#888888", "#999999")
        time_label.setMinimumWidth(58)
        time_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(time_label)

        message = BodyLabel(text, row)
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        light, dark = _LEVEL_COLORS[level]
        message.setTextColor(light, dark)
        layout.addWidget(message, 1)

        stretch = self._layout.takeAt(self._layout.count() - 1)
        self._layout.addWidget(row)
        if stretch is not None:
            self._layout.addItem(stretch)
        self._rows.append(row)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
