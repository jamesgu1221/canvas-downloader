"""课件下载：立即运行 + 两级进度条 + 日志。

GUI 不再启动 CLI 子进程，而是在后台 QThread 中直接调用 SyncService；
核心层通过 SyncEvent 回传进度和日志。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
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
from ._log_panel import LogLevel, LogPanel


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
    title = "课件下载"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="HomePage", parent=parent)

        self._thread: QThread | None = None
        self._worker: _SyncWorker | None = None
        self._current_course_name: str = ""
        self._course_value = 0
        self._file_value = 0
        self._last_downloaded = 0
        self._last_cancelled = False

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

        self._log = LogPanel(card)

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
        self._last_downloaded = 0
        self._last_cancelled = False
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
        if not self.is_running():
            self._run_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._status_label.setText("未运行")
            self._worker = None
            self._thread = None
            return
        if self._worker is not None:
            self._worker.cancel_token.cancel()
            self._status_label.setText("终止中...")

    def _append_log(self, text: str, level: LogLevel = "info") -> None:
        self._log.append(text, level)

    def _log_level(self, message: str) -> LogLevel:
        if "错误" in message or "失败" in message or "空间不足" in message:
            return "error"
        if "跳过" in message or "不可见" in message:
            return "warning"
        if message.startswith("完成") or message.startswith("已取消"):
            return "success"
        return "info"

    def _handle_event(self, event: SyncEvent) -> None:
        if isinstance(event, RunStarted):
            self._append_log(f"Canvas: {event.canvas_url}")
            self._append_log(f"下载到: {event.download_dir}")
            if event.dry_run:
                self._append_log("[DRY RUN 模式 — 不实际下载]")
            return
        if isinstance(event, LogEvent):
            self._append_log(event.message, self._log_level(event.message))
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
            self._file_label.setText(f"当前课  0 / {total} 文件 — {event.course_name}")
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
            self._last_downloaded = event.downloaded
            self._last_cancelled = event.cancelled
            if event.cancelled:
                self._append_log(f"已取消，已下载 {event.downloaded} 个文件", "success")
            else:
                self._append_log(f"完成！本次共下载 {event.downloaded} 个文件", "success")

    def _on_worker_finished(self, rc: int) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if self._last_cancelled:
            self._status_label.setText("已终止")
        else:
            self._status_label.setText("完成" if rc == 0 else f"失败（退出码 {rc}）")
        self._worker = None
        self._thread = None

        win = self.window()
        if self._last_cancelled:
            InfoBar.warning(
                title="已终止",
                content=f"共下载 {self._last_downloaded} 个文件",
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=win,
                duration=3000,
            )
        elif rc == 0:
            InfoBar.success(
                title="完成",
                content=f"共下载 {self._last_downloaded} 个文件",
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
        try:
            return self._thread is not None and self._thread.isRunning()
        except RuntimeError:
            self._thread = None
            self._worker = None
            return False

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel_token.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
