"""主页：立即运行 + 两级进度条 + 日志。

GUI 不再启动 CLI 子进程，而是在后台 QThread 中直接调用 SyncService；
核心层通过 SyncEvent 回传进度和日志。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Qt, Signal
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

from ...config import ConfigError, load_config
from ...events import (
    CallbackReporter,
    CourseProgressStarted,
    CourseProgressTick,
    FilePostfix,
    FileProgressEnded,
    FileProgressStarted,
    FileProgressTick,
    LogEvent,
    RunFinished,
    RunStarted,
    SyncEvent,
)
from ...paths import get_app_paths
from ...service import CancelToken, RunOptions, SyncError, SyncService
from ._content import ContentPage


class _SyncWorker(QObject):
    event = Signal(object)
    finished = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.cancel_token = CancelToken()

    def run(self) -> None:
        rc = 0
        try:
            paths = get_app_paths()
            config = load_config(None, paths)
            reporter = CallbackReporter(self.event.emit)
            service = SyncService(config, paths)
            service.run(RunOptions(), reporter, self.cancel_token)
        except (ConfigError, SyncError, RuntimeError) as e:
            rc = 1
            self.event.emit(LogEvent(str(e)))
        except BaseException as e:  # noqa: BLE001 - keep GUI state recoverable
            rc = 1
            self.event.emit(LogEvent(f"未处理错误：{e}"))
        self.finished.emit(rc)


class HomePage(ContentPage):
    title = "主页"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="HomePage", parent=parent)

        self._thread: QThread | None = None
        self._worker: _SyncWorker | None = None
        self._current_course_name: str = ""
        self._course_value = 0
        self._file_value = 0

        self.add(self._build_action_card())
        self.add(self._build_progress_card())
        self.add(self._build_log_card())
        self.add_stretch()

        self._reset_progress()

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

    def _on_run_clicked(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            InfoBar.warning(
                title="已在运行",
                content="当前已有下载任务，请等待完成或点击「终止」。",
                position=InfoBarPosition.TOP,
                parent=self.window(),
                duration=3000,
            )
            return

        self._reset_progress()
        self._log.clear()
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText("运行中...")

        self._thread = QThread(self)
        self._worker = _SyncWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._handle_event)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_stop_clicked(self) -> None:
        if self._worker is not None:
            self._worker.cancel_token.cancel()
            self._status_label.setText("终止中...")

    def _append_log(self, text: str) -> None:
        self._log.moveCursor(self._log.textCursor().MoveOperation.End)
        self._log.insertPlainText(text.rstrip("\n") + "\n")
        self._log.moveCursor(self._log.textCursor().MoveOperation.End)

    def _handle_event(self, event: SyncEvent) -> None:
        if isinstance(event, RunStarted):
            self._append_log(f"Canvas: {event.canvas_url}")
            self._append_log(f"下载到: {event.download_dir}")
            if event.dry_run:
                self._append_log("[DRY RUN 模式 — 不实际下载]")
            return
        if isinstance(event, LogEvent):
            self._append_log(event.message)
            return
        if isinstance(event, CourseProgressStarted):
            total = max(event.total, 1)
            self._course_value = 0
            self._course_bar.setRange(0, total)
            self._course_bar.setValue(0)
            self._course_label.setText(f"总进度  0 / {event.total} 门")
            return
        if isinstance(event, CourseProgressTick):
            self._course_value += event.step
            total = self._course_bar.maximum()
            self._course_bar.setValue(self._course_value)
            self._course_label.setText(f"总进度  {self._course_value} / {total} 门")
            return
        if isinstance(event, FileProgressStarted):
            total = max(event.total, 1)
            self._current_course_name = event.course_name
            self._file_value = 0
            self._file_bar.setRange(0, total)
            self._file_bar.setValue(0)
            self._file_label.setText(f"当前课  0 / {event.total} 文件 — {event.course_name}")
            self._file_postfix.setText("")
            return
        if isinstance(event, FileProgressTick):
            self._file_value += event.step
            total = self._file_bar.maximum()
            self._file_bar.setValue(self._file_value)
            self._file_label.setText(
                f"当前课  {self._file_value} / {total} 文件 — {self._current_course_name}"
            )
            return
        if isinstance(event, FilePostfix):
            self._file_postfix.setText(event.text)
            return
        if isinstance(event, FileProgressEnded):
            self._file_postfix.setText("")
            return
        if isinstance(event, RunFinished):
            if event.cancelled:
                self._append_log(f"已取消，已下载 {event.downloaded} 个文件")
            else:
                self._append_log(f"完成！本次共下载 {event.downloaded} 个文件")

    def _on_worker_finished(self, rc: int) -> None:
        if self.sender() is not self._worker:
            return

        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_label.setText(f"完成（退出码 {rc}）" if rc == 0 else f"失败（退出码 {rc}）")
        self._worker = None
        self._thread = None

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
                content=f"下载任务以退出码 {rc} 结束，详见日志。",
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=win,
                duration=5000,
            )

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

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel_token.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
