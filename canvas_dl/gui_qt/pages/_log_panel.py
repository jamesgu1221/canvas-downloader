"""Structured log panel used by the home page."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, SingleDirectionScrollArea


LogLevel = Literal["info", "success", "warning", "error"]


_LEVEL_COLORS: dict[LogLevel, tuple[str, str]] = {
    "info": ("#666666", "#b8b8b8"),
    "success": ("#137333", "#6fd58c"),
    "warning": ("#9a6400", "#e7b866"),
    "error": ("#b3261e", "#ff8a80"),
}


class LogPanel(QWidget):
    """A compact, scrollable stream of sync log entries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)

        self._entries: list[QWidget] = []
        self._last_entry: QWidget | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._scroll = SingleDirectionScrollArea(self, orient=Qt.Orientation.Vertical)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(SingleDirectionScrollArea.Shape.NoFrame)
        self._scroll.setMinimumHeight(220)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)

        self._host = QWidget(self._scroll)
        self._host.setStyleSheet("background: transparent;")
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(0, 2, 12, 2)
        self._layout.setSpacing(2)
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
            QTimer.singleShot(25, self._scroll_to_bottom)

    def clear(self) -> None:
        for entry in self._entries:
            self._layout.removeWidget(entry)
            entry.deleteLater()
        self._entries = []
        self._last_entry = None
        self._placeholder.setVisible(True)

    def _append_line(self, text: str, level: LogLevel) -> None:
        self._placeholder.setVisible(False)

        entry = QWidget(self._host)
        entry.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(entry)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(8)

        light, dark = _LEVEL_COLORS[level]

        message = BodyLabel(text, entry)
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message.setTextColor(light, dark)
        layout.addWidget(message, 1)

        stretch = self._layout.takeAt(self._layout.count() - 1)
        self._layout.addWidget(entry)
        if stretch is not None:
            self._layout.addItem(stretch)
        self._entries.append(entry)
        self._last_entry = entry

    def _scroll_to_bottom(self) -> None:
        self._host.adjustSize()
        if self._last_entry is not None:
            self._scroll.ensureWidgetVisible(self._last_entry, 0, 0)
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_scroll_range_changed(self, _minimum: int, maximum: int) -> None:
        self._scroll.verticalScrollBar().setValue(maximum)
