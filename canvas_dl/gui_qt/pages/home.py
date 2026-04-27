"""主页：立即运行 + 两级进度条 + 日志。

子进程协议与旧版 Tk GUI 一致：
- 用 `python -u -m canvas_dl` 启动（`-u` 避免日志延迟）；
- 环境变量 `CANVAS_DL_GUI_MODE=1` 让子进程切到 `_GuiBar` 管道输出；
- 子进程 stdout 里，`@@PROGRESS@@\t...` 由 `_parse_progress_line` 解析为进度事件，
  其他行当作普通日志追加。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    HeaderCardWidget,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
)

from ._content import ContentPage


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PYTHON = sys.executable
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class _ProcBridge(QObject):
    """把子进程线程的事件投递回 GUI 线程。

    PySide6 跨线程 signal 在 AutoConnection 下会自动走 QueuedConnection，
    因此在 `_pump_stdout` 里直接 emit 即可安全回到主线程槽函数。
    """

    line = Signal(str)            # 一般日志行（含换行）
    progress = Signal(list)       # 解析后的 @@PROGRESS@@ 字段列表
    finished = Signal(int)        # 退出码


class HomePage(ContentPage):
    title = "主页"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="HomePage", parent=parent)

        self._proc: subprocess.Popen | None = None
        self._bridge = _ProcBridge()
        self._bridge.line.connect(self._append_log)
        self._bridge.progress.connect(self._handle_progress)
        self._bridge.finished.connect(self._on_proc_end)

        self._current_course_name: str = ""
        self._course_value = 0
        self._file_value = 0

        self.add(self._build_action_card())
        self.add(self._build_progress_card())
        self.add(self._build_log_card())
        self.add_stretch()

        self._reset_progress()

    # ─── UI ───
    def _build_action_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("立即运行")

        row = QHBoxLayout()
        row.setSpacing(12)

        self._run_btn = PrimaryPushButton(FIF.PLAY, "立即运行", card)
        self._run_btn.clicked.connect(self._on_run_clicked)
        row.addWidget(self._run_btn)

        self._stop_btn = PushButton(FIF.CLOSE, "终止", card)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        row.addWidget(self._stop_btn)

        self._status_label = CaptionLabel("未运行", card)
        self._status_label.setTextColor("#666", "#bbb")
        row.addWidget(self._status_label, 1)

        container = QWidget(card)
        container.setLayout(row)
        card.viewLayout.addWidget(container)
        return card

    def _build_progress_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("进度")

        wrap = QVBoxLayout()
        wrap.setSpacing(6)

        self._course_label = BodyLabel("总进度  0 / 0 门", card)
        self._course_bar = ProgressBar(card)
        self._course_bar.setRange(0, 1)
        self._course_bar.setValue(0)
        wrap.addWidget(self._course_label)
        wrap.addWidget(self._course_bar)

        wrap.addSpacing(4)

        self._file_label = BodyLabel("当前课  0 / 0 文件", card)
        self._file_bar = ProgressBar(card)
        self._file_bar.setRange(0, 1)
        self._file_bar.setValue(0)
        wrap.addWidget(self._file_label)
        wrap.addWidget(self._file_bar)

        self._file_postfix = CaptionLabel("", card)
        self._file_postfix.setTextColor("#777", "#aaa")
        wrap.addWidget(self._file_postfix)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    def _build_log_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("日志")

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 6)
        bar.addStretch(1)
        self._clear_btn = PushButton(FIF.DELETE, "清空日志", card)
        self._clear_btn.clicked.connect(lambda: self._log.clear())
        bar.addWidget(self._clear_btn)

        self._log = QPlainTextEdit(card)
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(180)
        self._log.setPlaceholderText("点击「立即运行」后，下载日志将显示在这里。")
        self._log.setStyleSheet(
            "QPlainTextEdit { "
            "   background: rgba(255,255,255,0.55); "
            "   border: 1px solid rgba(0,0,0,0.08); "
            "   border-radius: 6px; "
            "   padding: 6px; "
            "}"
        )

        wrap = QVBoxLayout()
        wrap.setSpacing(0)
        wrap.addLayout(bar)
        wrap.addWidget(self._log)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    # ─── actions ───
    def _on_run_clicked(self) -> None:
        if self._proc and self._proc.poll() is None:
            InfoBar.warning(
                title="已在运行",
                content="当前已有下载子进程，请等待完成或点击「终止」。",
                position=InfoBarPosition.TOP,
                parent=self.window(),
                duration=3000,
            )
            return

        cmd = [_PYTHON, "-u", "-m", "canvas_dl"]
        env = {**os.environ, "CANVAS_DL_GUI_MODE": "1", "PYTHONIOENCODING": "utf-8"}

        self._reset_progress()
        self._log.clear()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText("运行中…")

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as e:
            self._append_log(f"[启动失败] {e}\n")
            self._run_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._status_label.setText("启动失败")
            self._proc = None
            return

        threading.Thread(target=self._pump_stdout, args=(self._proc,), daemon=True).start()

    def _on_stop_clicked(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._status_label.setText("终止中…")

    def _pump_stdout(self, proc: subprocess.Popen) -> None:
        rc = -1
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("@@PROGRESS@@\t"):
                    self._bridge.progress.emit(line.rstrip("\r\n").split("\t"))
                else:
                    self._bridge.line.emit(line)
        except Exception as e:  # noqa: BLE001 — pump 线程必须永不抛，否则 UI 永远等不到 finished
            self._bridge.line.emit(f"[读取错误] {e}\n")
        finally:
            rc = proc.wait()
        self._bridge.finished.emit(rc)

    # ─── slots on GUI thread ───
    def _append_log(self, text: str) -> None:
        self._log.moveCursor(self._log.textCursor().MoveOperation.End)
        self._log.insertPlainText(text)
        self._log.moveCursor(self._log.textCursor().MoveOperation.End)

    def _progress_int(self, value: str, field: str) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            self._append_log(f"[进度解析跳过] {field}={value!r}\n")
            return None

    def _handle_progress(self, parts: list[str]) -> None:
        if len(parts) < 3:
            return
        # parts[0] == '@@PROGRESS@@'
        kind, event = parts[1], parts[2]
        rest = parts[3:]
        if kind == "course":
            if event == "start" and rest:
                total_raw = self._progress_int(rest[0], "course.total")
                if total_raw is None:
                    return
                total = max(total_raw, 1)
                self._course_value = 0
                self._course_bar.setRange(0, total)
                self._course_bar.setValue(0)
                self._course_label.setText(f"总进度  0 / {rest[0]} 门")
            elif event == "tick" and rest:
                step = self._progress_int(rest[0], "course.tick")
                if step is None:
                    return
                self._course_value += step
                total = self._course_bar.maximum()
                self._course_bar.setValue(self._course_value)
                self._course_label.setText(f"总进度  {self._course_value} / {total} 门")
        elif kind == "file":
            if event == "start" and len(rest) >= 2:
                course_name, total_s = rest[0], rest[1]
                total_raw = self._progress_int(total_s, "file.total")
                if total_raw is None:
                    return
                total = max(total_raw, 1)
                self._current_course_name = course_name
                self._file_value = 0
                self._file_bar.setRange(0, total)
                self._file_bar.setValue(0)
                self._file_label.setText(f"当前课  0 / {total_s} 文件 — {course_name}")
                self._file_postfix.setText("")
            elif event == "tick" and rest:
                step = self._progress_int(rest[0], "file.tick")
                if step is None:
                    return
                self._file_value += step
                total = self._file_bar.maximum()
                self._file_bar.setValue(self._file_value)
                self._file_label.setText(
                    f"当前课  {self._file_value} / {total} 文件 — {self._current_course_name}"
                )
            elif event == "postfix" and rest:
                self._file_postfix.setText(rest[0])
            elif event == "end":
                self._file_postfix.setText("")

    def _on_proc_end(self, rc: int) -> None:
        if rc != 0:
            self._append_log(f"\n[退出码 {rc}]\n")
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_label.setText(f"完成（退出码 {rc}）" if rc == 0 else f"失败（退出码 {rc}）")
        self._proc = None

        win = self.window()
        if rc == 0:
            InfoBar.success(
                title="完成",
                content="本次 Canvas 同步已完成。",
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=win,
                duration=3000,
            )
        else:
            InfoBar.error(
                title="失败",
                content=f"下载子进程以退出码 {rc} 结束，详见日志。",
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=win,
                duration=5000,
            )

    # ─── reset ───
    def _reset_progress(self) -> None:
        self._course_bar.setRange(0, 1)
        self._course_bar.setValue(0)
        self._file_bar.setRange(0, 1)
        self._file_bar.setValue(0)
        self._course_label.setText("总进度  0 / 0 门")
        self._file_label.setText("当前课  0 / 0 文件")
        self._file_postfix.setText("")
        self._current_course_name = ""
        self._course_value = 0
        self._file_value = 0

    # ─── cleanup ───
    def shutdown(self) -> None:
        """窗口关闭前外部调用，防止子进程泄漏。"""
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass
