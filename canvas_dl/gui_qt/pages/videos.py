"""课堂视频：扫码登录 → 急切扫描全部可下载课程节次 → 按课程/节次树选择下载。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from PySide6.QtCore import (
    QObject,
    QPersistentModelIndex,
    QRectF,
    QSize,
    QThread,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QTreeWidgetItem,
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
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    TreeItemDelegate,
    TreeWidget,
    themeColor,
)

from ...config import ConfigError, load_config
from ...cookie_cache import load_cookie_cache, save_cookie_cache
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
    VideoBytesFinished,
    VideoBytesProgress,
    VideoBytesStarted,
)
from ...jaccount_qr import (
    JAccountQrError,
    QrLoginMonitor,
    cookie_dicts,
    fetch_qr_code,
    finish_qr_login,
    resolve_video_url,
    scan_canvas_video_courses,
    start_qr_login,
)
from ...paths import get_app_paths
from ...service import CancelToken, SyncError
from ...util import env as env_util
from ...videos import (
    SjtuiVsProvider,
    VideoEntry,
    VideoError,
    VideoRunOptions,
    VideoService,
    apply_session_cookies,
    parse_lecture_filter,
    parse_sjtu_token,
)
from ._content import ContentPage
from ._log_panel import LogLevel, LogPanel


CANVAS_LOGIN_URL = "https://oc.sjtu.edu.cn/login/openid_connect"


def _format_bytes(n: int) -> str:
    n = int(n)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _format_eta(seconds: float) -> str:
    if seconds < 1:
        return "<1s"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s:02d}s"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m"


@dataclass
class LoginResult:
    cookies: list[dict]


class VideoLoginDialog(QDialog):
    """二维码扫码登录。成功后只把 Cookie 返出去；URL 解析交给上层扫描流程。"""

    qr_ready = Signal(bytes)
    login_ready = Signal()
    login_error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("登录课堂视频")
        self.resize(360, 430)
        self.result_data: LoginResult | None = None
        self._session = requests.Session()
        self._state = None
        self._monitor: QrLoginMonitor | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self._status = CaptionLabel("正在加载 jAccount 二维码...", self)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._qr_label = QLabel(self)
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setMinimumSize(280, 280)
        layout.addWidget(self._qr_label, 1)

        self._refresh_btn = PushButton(FIF.SYNC, "刷新二维码", self)
        self._refresh_btn.clicked.connect(self._refresh_qr)
        layout.addWidget(self._refresh_btn)

        self.qr_ready.connect(self._set_qr_image)
        self.login_ready.connect(self._finish_login)
        self.login_error.connect(self._set_error)
        self._start()

    def _start(self) -> None:
        try:
            self._state = start_qr_login(self._session, CANVAS_LOGIN_URL)
            self._monitor = QrLoginMonitor(
                self._state.uuid,
                self._session,
                self._on_qr_params,
                self._on_login_signal,
                self._on_monitor_error,
            )
            self._monitor.start()
        except (JAccountQrError, requests.RequestException, RuntimeError) as e:
            self._set_error(str(e))

    def _refresh_qr(self) -> None:
        if self._monitor is not None:
            self._monitor.refresh()

    def _on_qr_params(self, ts: str, sig: str) -> None:
        if self._state is None:
            return
        try:
            content = fetch_qr_code(self._session, self._state.uuid, ts, sig)
        except (JAccountQrError, requests.RequestException) as e:
            self.login_error.emit(str(e))
            return
        self.qr_ready.emit(content)

    def _on_login_signal(self) -> None:
        self.login_ready.emit()

    def _on_monitor_error(self, message: str) -> None:
        if message:
            self.login_error.emit(message)

    def _set_qr_image(self, content: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(content):
            self._qr_label.setPixmap(
                pixmap.scaled(
                    280,
                    280,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._status.setText("请使用交我办 / jAccount 扫码确认登录")
        else:
            self._set_error("二维码图片解析失败。")

    def _finish_login(self) -> None:
        if self._state is None:
            self._set_error("登录状态缺失。")
            return
        try:
            finish_qr_login(self._session, self._state.uuid)
        except (JAccountQrError, requests.RequestException) as e:
            self._set_error(str(e))
            return
        cookies = cookie_dicts(self._session)
        self.result_data = LoginResult(cookies=cookies)
        try:
            save_cookie_cache(cookies, get_app_paths().video_cookies_file)
        except OSError:
            pass  # cookie 缓存不是阻塞登录的硬条件
        if self._monitor is not None:
            self._monitor.close()
            self._monitor = None
        self.accept()

    def _set_error(self, message: str) -> None:
        self._status.setText(message)

    def closeEvent(self, event) -> None:
        if self._monitor is not None:
            self._monitor.close()
        super().closeEvent(event)


class _EagerScanWorker(QObject):
    """扫码成功后跑一次性的全量扫描：找出含课堂视频入口的课程，逐门拉节次和下载 URL。"""

    log = Signal(str, str)  # message, level
    course_started = Signal(int, int, str)  # current, total, course_name
    course_finished = Signal(dict)  # {id, name, tool_url, lectures: [...], cached_at}
    # 本次扫描中暂时无法完成的课程（解析入口失败 / 缺 tokenId / 节次接口失败 / 无节次）：
    # UI 拿着 (id, name, tool_url) 去合并旧缓存，避免临时网络抖动一次扫描就把旧课程从树里清掉。
    course_failed = Signal(int, str, str)  # course_id, course_name, tool_url
    finished = Signal(bool, str)  # success, error_message

    def __init__(self, cookies: list[dict]) -> None:
        super().__init__()
        self.cookies = cookies
        self.cancel_token = CancelToken()

    def run(self) -> None:
        try:
            self._run()
        except (ConfigError, SyncError, VideoError, RuntimeError) as e:
            self.finished.emit(False, str(e))
        except BaseException as e:  # noqa: BLE001 - last-resort to keep UI alive
            self.finished.emit(False, f"扫描中出现未处理错误：{e}")

    def _run(self) -> None:
        paths = get_app_paths()
        config = load_config(None, paths)
        service = VideoService(config, paths)
        canvas_courses = [
            {"id": c.id, "name": getattr(c, "name", f"course_{c.id}")}
            for c in service.client.get_courses()
        ]
        session = requests.Session()
        apply_session_cookies(session, self.cookies)
        self.log.emit(f"Canvas 共 {len(canvas_courses)} 门课程，正在筛选含课堂视频入口的课程...", "info")
        video_courses = scan_canvas_video_courses(session, canvas_courses, self.cancel_token)
        if self.cancel_token.is_cancelled():
            self.log.emit("扫描已取消。", "warning")
            self.finished.emit(False, "用户取消")
            return
        self.log.emit(f"找到 {len(video_courses)} 门可下载视频课程，开始拉取节次。", "info")

        provider = SjtuiVsProvider(session=session)
        provider.cookies = {
            c["name"]: c["value"]
            for c in self.cookies
            if isinstance(c, dict) and "name" in c and "value" in c
        }

        total = len(video_courses)
        for idx, course in enumerate(video_courses, start=1):
            if self.cancel_token.is_cancelled():
                self.log.emit("扫描已取消。", "warning")
                self.finished.emit(False, "用户取消")
                return
            course_id = int(course["id"])
            course_name = str(course["name"])
            tool_url = str(course["tool_url"])
            self.course_started.emit(idx, total, course_name)
            try:
                video_url = resolve_video_url(session, tool_url)
            except (JAccountQrError, requests.RequestException) as e:
                self.log.emit(f"  [{course_name}] 解析视频入口失败：{e}", "warning")
                self.course_failed.emit(course_id, course_name, tool_url)
                continue
            token_id = parse_sjtu_token(video_url) or ""
            if not token_id:
                self.log.emit(f"  [{course_name}] 未取到 tokenId，跳过。", "warning")
                self.course_failed.emit(course_id, course_name, tool_url)
                continue
            entry = VideoEntry(url=video_url, token_id=token_id, canvas_course_id=course_id)
            try:
                lectures = provider.list_lectures(entry)
            except Exception as e:  # noqa: BLE001 - provider 在多种失败模式下抛
                self.log.emit(f"  [{course_name}] 读取节次失败：{e}", "warning")
                self.course_failed.emit(course_id, course_name, tool_url)
                continue
            if not lectures:
                self.log.emit(f"  [{course_name}] 未发现可下载节次。", "warning")
                self.course_failed.emit(course_id, course_name, tool_url)
                continue
            from ...videos import _save_lecture_cache  # noqa: PLC0415 - 同包私有

            _save_lecture_cache(paths.video_lectures_cache_file, course_id, course_name, lectures)
            self.course_finished.emit(
                {
                    "id": course_id,
                    "name": course_name,
                    "tool_url": tool_url,
                    "lectures": [
                        {
                            "index": lec.index,
                            "title": lec.title,
                            "lecture_id": lec.lecture_id,
                        }
                        for lec in lectures
                    ],
                }
            )
            self.log.emit(f"  [{course_name}] 已缓存 {len(lectures)} 节。", "success")

        self.finished.emit(True, "")


class _DownloadWorker(QObject):
    """按 (course_id → 选中的 lecture index 集合) 触发下载。"""

    event = Signal(object)
    finished = Signal(int)

    def __init__(
        self,
        selection: dict[int, set[int]],
        download_dir: str,
        cookies: list[dict],
        dry_run: bool,
    ) -> None:
        super().__init__()
        self.selection = selection
        self.download_dir = download_dir
        self.cookies = cookies
        self.dry_run = dry_run
        self.cancel_token = CancelToken()

    def run(self) -> None:
        rc = 0
        try:
            paths = get_app_paths()
            config = load_config(None, paths, require_token=False)
            options = VideoRunOptions(
                dry_run=self.dry_run,
                only_courses=list(self.selection.keys()),
                download_dir=(
                    Path(self.download_dir) if self.download_dir else config.download_dir
                ),
                per_course_lectures={cid: set(lids) for cid, lids in self.selection.items()},
                cached_cookies=True,
                session_cookies=list(self.cookies),
                max_concurrent_videos=env_util.get_video_max_concurrent_videos(),
                max_workers_per_video=env_util.get_video_max_workers_per_video(),
            )
            service = VideoService(config, paths)
            reporter = CallbackReporter(self.event.emit)
            service.run(options, reporter, self.cancel_token)
        except (ConfigError, SyncError, VideoError, RuntimeError) as e:
            rc = 1
            self.event.emit(LogEvent(str(e)))
        except BaseException as e:  # noqa: BLE001
            rc = 1
            self.event.emit(LogEvent(f"未处理错误：{e}"))
        self.finished.emit(rc)


class _CourseBatchControls(QWidget):
    """Per-course inline toolbar: range input + 应用 / 反选 / 全清 buttons.

    Hosted via `QTreeWidget.setItemWidget` on a non-checkable placeholder child
    so each course exposes its own batch-select controls right above its
    lecture list.
    """

    apply_requested = Signal(int, str)   # course_id, range text
    invert_requested = Signal(int)       # course_id
    clear_requested = Signal(int)        # course_id

    # qfluentwidgets LineEdit has a QSS-enforced min-height of 33px; setting
    # fixed-height below that vertically clips the placeholder/text. All
    # widgets in this row share the LineEdit's height so the row stays aligned.
    _ROW_HEIGHT = 33

    def __init__(self, course_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._course_id = course_id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        layout.addWidget(CaptionLabel("范围", self))
        self._edit = LineEdit(self)
        self._edit.setPlaceholderText("如 1-4, 7")
        self._edit.setClearButtonEnabled(True)
        self._edit.setFixedHeight(self._ROW_HEIGHT)
        self._edit.returnPressed.connect(self._emit_apply)
        layout.addWidget(self._edit, 1)

        self._apply_btn = PushButton("应用", self)
        self._apply_btn.setFixedHeight(self._ROW_HEIGHT)
        self._apply_btn.clicked.connect(self._emit_apply)
        layout.addWidget(self._apply_btn)

        self._invert_btn = PushButton("反选", self)
        self._invert_btn.setFixedHeight(self._ROW_HEIGHT)
        self._invert_btn.clicked.connect(lambda: self.invert_requested.emit(self._course_id))
        layout.addWidget(self._invert_btn)

        self._clear_btn = PushButton("全清", self)
        self._clear_btn.setFixedHeight(self._ROW_HEIGHT)
        self._clear_btn.clicked.connect(lambda: self.clear_requested.emit(self._course_id))
        layout.addWidget(self._clear_btn)

        # The host placeholder row in the tree also needs to be tall enough,
        # otherwise the tree clips the widget vertically. Add a bit of vertical
        # padding so the row height = ROW_HEIGHT + 8px margins.
        self.setMinimumHeight(self._ROW_HEIGHT + 8)

    def _emit_apply(self) -> None:
        self.apply_requested.emit(self._course_id, self._edit.text())


class _AnimatedCheckTreeDelegate(TreeItemDelegate):
    """TreeItemDelegate variant that draws a soft accent-colored halo around the
    check indicator on each state change, then fades and expands it outward.

    The actual checkbox visual is drawn by the parent delegate as before;
    we only paint an overlay during the ~260ms transition.
    """

    ANIM_MS = 260

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._prev: dict[QPersistentModelIndex, Qt.CheckState] = {}
        self._active: dict[QPersistentModelIndex, float] = {}
        self._suspended = False
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._on_tick)
        model = parent.model() if parent is not None else None
        if model is not None:
            model.dataChanged.connect(self._on_data_changed)

    def suspend(self, value: bool) -> None:
        self._suspended = value

    def _on_data_changed(self, top_left, _bottom_right, roles) -> None:
        if roles and Qt.ItemDataRole.CheckStateRole not in roles:
            return
        if not top_left.isValid():
            return
        raw = top_left.data(Qt.ItemDataRole.CheckStateRole)
        if raw is None:
            return
        new_state = Qt.CheckState(raw)
        pkey = QPersistentModelIndex(top_left)
        prev = self._prev.get(pkey)
        self._prev[pkey] = new_state
        if self._suspended or prev is None or prev == new_state:
            return
        self._active[pkey] = time.monotonic()
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def _on_tick(self) -> None:
        now = time.monotonic()
        expired = [k for k, t in self._active.items() if (now - t) * 1000 >= self.ANIM_MS]
        for k in expired:
            self._active.pop(k, None)
        view = self.parent()
        if view is not None:
            view.viewport().update()
        if not self._active:
            self._tick_timer.stop()

    def _drawCheckBox(self, painter, option, index):  # noqa: N802 - Qt naming
        super()._drawCheckBox(painter, option, index)
        pkey = QPersistentModelIndex(index)
        start_t = self._active.get(pkey)
        if start_t is None:
            return
        elapsed_ms = (time.monotonic() - start_t) * 1000
        if elapsed_ms >= self.ANIM_MS:
            return
        t = elapsed_ms / self.ANIM_MS
        eased = 1 - (1 - t) ** 3

        # Same geometry as TreeItemDelegate._drawCheckBox upstream.
        x = option.rect.x() + 23
        y = option.rect.center().y() - 9
        box = QRectF(x, y, 19, 19)
        radius = 4.5

        expansion = 7.0 * eased
        halo = box.adjusted(-expansion, -expansion, expansion, expansion)
        ring_alpha = max(0.0, 0.55 * (1 - eased))

        color = QColor(themeColor())
        color.setAlphaF(ring_alpha)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(halo, radius + expansion, radius + expansion)
        painter.restore()


class VideosPage(ContentPage):
    title = "课堂视频"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="VideosPage", parent=parent)
        self._login_cookies: list[dict] | None = None
        self._courses: list[dict] = []  # [{id, name, tool_url}]
        self._lectures_by_course: dict[int, list[dict]] = {}
        self._selection: dict[int, set[int]] = {}
        self._scan_thread: QThread | None = None
        self._scan_worker: _EagerScanWorker | None = None
        # 每次扫描前快照旧缓存，扫描结束时把"本轮失败"的课程从快照里捞回来，
        # 避免一次部分失败的扫描覆盖掉先前的整份课程列表。
        self._scan_old_courses_by_id: dict[int, dict] = {}
        self._scan_failed_courses: dict[int, dict] = {}
        self._dl_thread: QThread | None = None
        self._dl_worker: _DownloadWorker | None = None
        self._course_value = 0
        self._file_value = 0
        self._current_course_name = ""
        # asset_key → {"downloaded": int, "total": int, "label": str}
        self._active_videos: dict[str, dict] = {}
        # monotonically increasing across all videos this course (only ↑),
        # paired with sample timestamps so speed = derivative over a sliding window
        self._lifetime_bytes = 0
        self._speed_samples: list[tuple[float, int]] = []

        self.add(self._build_form_card())
        self.add(self._build_progress_card())
        self.add(self._build_log_card())
        self.add_stretch()
        self._reset_progress()
        self._load_cached_state()

    # ─── UI ───
    def _build_form_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("课程与节次")

        layout = QVBoxLayout()
        layout.setSpacing(10)

        toolbar_row = QHBoxLayout()
        toolbar_row.setSpacing(8)
        toolbar_row.addWidget(
            CaptionLabel("展开课程后可在每门课内单独按节次范围批量勾选。", card)
        )
        toolbar_row.addStretch(1)
        self._expand_all_btn = PushButton("全部展开", card)
        self._expand_all_btn.clicked.connect(lambda: self._tree.expandAll())
        toolbar_row.addWidget(self._expand_all_btn)
        self._collapse_all_btn = PushButton("全部收起", card)
        self._collapse_all_btn.clicked.connect(lambda: self._tree.collapseAll())
        toolbar_row.addWidget(self._collapse_all_btn)
        layout.addLayout(toolbar_row)

        self._tree = TreeWidget(card)
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumHeight(280)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setUniformRowHeights(False)
        self._tree_delegate = _AnimatedCheckTreeDelegate(self._tree)
        self._tree.setItemDelegate(self._tree_delegate)
        self._tree.itemChanged.connect(self._on_tree_item_changed)
        layout.addWidget(self._tree)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._login_btn = PushButton(FIF.PLAY, "扫码登录刷新缓存", card)
        self._login_btn.clicked.connect(self._on_login)
        row.addWidget(self._login_btn)

        self._dry_btn = PushButton(FIF.SYNC, "预览", card)
        self._dry_btn.clicked.connect(lambda: self._start_download(True))
        row.addWidget(self._dry_btn)

        self._download_btn = PrimaryPushButton(FIF.SAVE, "下载", card)
        self._download_btn.clicked.connect(lambda: self._start_download(False))
        row.addWidget(self._download_btn)

        self._stop_btn = PushButton(FIF.CLOSE, "终止", card)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        row.addWidget(self._stop_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self._status_label = CaptionLabel("尚未登录。", card)
        self._status_label.setTextColor("#666", "#bbb")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        container = QWidget(card)
        container.setLayout(layout)
        card.viewLayout.addWidget(container)
        return card

    def _build_progress_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("进度")
        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        self._course_label = BodyLabel("总进度  0 / 0 门", card)
        self._course_bar = ProgressBar(card)
        self._file_label = BodyLabel("当前课  0 / 0 视频", card)
        self._file_bar = ProgressBar(card)
        self._file_postfix = CaptionLabel("", card)
        self._file_postfix.setTextColor("#777", "#aaa")
        self._video_label = BodyLabel("当前视频  —", card)
        self._video_bar = ProgressBar(card)
        wrap.addWidget(self._course_label)
        wrap.addWidget(self._course_bar)
        wrap.addWidget(self._file_label)
        wrap.addWidget(self._file_bar)
        wrap.addWidget(self._file_postfix)
        wrap.addWidget(self._video_label)
        wrap.addWidget(self._video_bar)
        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    def _build_log_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("日志")
        self._log = LogPanel(card)
        card.viewLayout.addWidget(self._log)
        return card

    # ─── cached state ───
    def _load_cached_state(self) -> None:
        paths = get_app_paths()
        courses = self._read_auto_courses(paths.video_auto_courses_file)
        if not courses:
            self._status_label.setText(
                "尚未登录。点击「扫码登录刷新缓存」获取课程节次与下载 URL。"
            )
            return
        lectures_cache = self._read_lectures_cache(paths.video_lectures_cache_file)
        self._courses = []
        self._lectures_by_course = {}
        self._selection = {}
        cached_at_max = ""
        for course in courses:
            course_id = course.get("id")
            if not isinstance(course_id, int):
                continue
            self._courses.append(
                {
                    "id": course_id,
                    "name": course.get("name") or f"course_{course_id}",
                    "tool_url": course.get("tool_url") or "",
                }
            )
            entry = lectures_cache.get(str(course_id))
            if isinstance(entry, dict):
                raw_lectures = entry.get("lectures") or []
                self._lectures_by_course[course_id] = [
                    lec for lec in raw_lectures if isinstance(lec, dict)
                ]
                cached_at = str(entry.get("cached_at") or "")
                if cached_at > cached_at_max:
                    cached_at_max = cached_at
            else:
                self._lectures_by_course[course_id] = []
            self._selection[course_id] = set(course.get("selected_lectures") or [])

        self._render_tree()
        if cached_at_max:
            self._status_label.setText(
                f"已加载 {len(self._courses)} 门课程的缓存（{cached_at_max[:19].replace('T', ' ')}）。"
            )
        else:
            self._status_label.setText(f"已加载 {len(self._courses)} 门课程的缓存。")

    @staticmethod
    def _read_auto_courses(path: Path) -> list[dict]:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _read_lectures_cache(path: Path) -> dict:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_auto_courses(self) -> None:
        paths = get_app_paths()
        payload = [
            {
                "id": course["id"],
                "name": course["name"],
                "tool_url": course["tool_url"],
                "selected_lectures": sorted(self._selection.get(course["id"], set())),
            }
            for course in self._courses
        ]
        try:
            paths.video_auto_courses_file.parent.mkdir(parents=True, exist_ok=True)
            paths.video_auto_courses_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            self._append_log(f"保存选择失败：{e}", "warning")

    # ─── tree rendering ───
    def _render_tree(self) -> None:
        delegate = getattr(self, "_tree_delegate", None)
        if delegate is not None:
            delegate.suspend(True)
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for course in self._courses:
                course_id = course["id"]
                lectures = self._lectures_by_course.get(course_id, [])
                top = QTreeWidgetItem([f"{course['name']} [{course_id}]  ·  共 {len(lectures)} 节"])
                top.setData(0, Qt.ItemDataRole.UserRole, ("course", course_id))
                top.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsAutoTristate
                )
                top.setCheckState(0, Qt.CheckState.Unchecked)
                self._tree.addTopLevelItem(top)
                selected = self._selection.get(course_id, set())
                if lectures:
                    placeholder = QTreeWidgetItem()
                    placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    placeholder.setData(0, Qt.ItemDataRole.UserRole, ("batch", course_id))
                    # Must fit _CourseBatchControls' minimum height (LineEdit 33px
                    # natural + 8px vertical margins) — anything smaller clips the
                    # LineEdit visually and the placeholder text gets cramped.
                    placeholder.setSizeHint(0, QSize(0, 44))
                    top.addChild(placeholder)
                for lec in lectures:
                    index = self._safe_int(lec.get("index"))
                    if index is None:
                        continue
                    title = str(lec.get("title") or f"第{index}节")
                    child = QTreeWidgetItem([f"{index}. {title}"])
                    child.setData(0, Qt.ItemDataRole.UserRole, ("lecture", course_id, index))
                    child.setFlags(
                        Qt.ItemFlag.ItemIsEnabled
                        | Qt.ItemFlag.ItemIsUserCheckable
                    )
                    child.setCheckState(
                        0,
                        Qt.CheckState.Checked if index in selected else Qt.CheckState.Unchecked,
                    )
                    top.addChild(child)
                if lectures:
                    controls = _CourseBatchControls(course_id, parent=self._tree)
                    controls.apply_requested.connect(self._on_course_batch_apply)
                    controls.invert_requested.connect(self._on_course_batch_invert)
                    controls.clear_requested.connect(self._on_course_batch_clear)
                    # placeholder is the first child after we added it above.
                    self._tree.setItemWidget(top.child(0), 0, controls)
        finally:
            self._tree.blockSignals(False)
            if delegate is not None:
                delegate.suspend(False)
        # Re-sync selection from tree state (in case auto-tristate adjusted parents).
        self._sync_selection_from_tree()

    def _on_tree_item_changed(self, _item: QTreeWidgetItem, _column: int) -> None:
        self._sync_selection_from_tree()
        self._save_auto_courses()

    def _sync_selection_from_tree(self) -> None:
        new_selection: dict[int, set[int]] = {}
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            tag = top.data(0, Qt.ItemDataRole.UserRole)
            if not (isinstance(tag, tuple) and tag and tag[0] == "course"):
                continue
            course_id = tag[1]
            picked: set[int] = set()
            for j in range(top.childCount()):
                child = top.child(j)
                child_tag = child.data(0, Qt.ItemDataRole.UserRole)
                if (
                    isinstance(child_tag, tuple)
                    and len(child_tag) == 3
                    and child_tag[0] == "lecture"
                    and child.checkState(0) == Qt.CheckState.Checked
                ):
                    picked.add(child_tag[2])
            new_selection[course_id] = picked
        self._selection = new_selection

    # ─── per-course batch selection ───
    def _on_course_batch_apply(self, course_id: int, text: str) -> None:
        text = text.strip()
        if not text:
            return
        try:
            flt = parse_lecture_filter(text)
        except VideoError as e:
            self._show_error("节次范围格式错误", str(e))
            return
        if not flt.ranges:
            return
        self._apply_course_leaf_predicate(course_id, lambda idx: flt.matches(idx))

    def _on_course_batch_clear(self, course_id: int) -> None:
        self._apply_course_leaf_predicate(course_id, lambda _idx: False)

    def _on_course_batch_invert(self, course_id: int) -> None:
        self._apply_course_leaf_predicate(course_id, None)  # None = toggle

    def _find_course_item(self, course_id: int) -> QTreeWidgetItem | None:
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            tag = top.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(tag, tuple) and tag and tag[0] == "course" and tag[1] == course_id:
                return top
        return None

    def _apply_course_leaf_predicate(self, course_id: int, predicate) -> None:
        """Walk one course's lecture leaves and set their check state.

        - predicate=None: toggle the existing state.
        - predicate(idx)->bool: Checked when True, Unchecked otherwise.

        Suppresses QTreeWidget.itemChanged so we save once at the end, but
        QAbstractItemModel.dataChanged still fires — so the animated delegate
        sees each transition.
        """
        top = self._find_course_item(course_id)
        if top is None:
            return
        changed = False
        self._tree.blockSignals(True)
        try:
            for j in range(top.childCount()):
                child = top.child(j)
                tag = child.data(0, Qt.ItemDataRole.UserRole)
                if not (isinstance(tag, tuple) and len(tag) == 3 and tag[0] == "lecture"):
                    continue
                idx = tag[2]
                current = child.checkState(0)
                if predicate is None:
                    target = (
                        Qt.CheckState.Unchecked
                        if current == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
                else:
                    target = (
                        Qt.CheckState.Checked if predicate(idx) else Qt.CheckState.Unchecked
                    )
                if target != current:
                    child.setCheckState(0, target)
                    changed = True
        finally:
            self._tree.blockSignals(False)
        if changed:
            self._sync_selection_from_tree()
            self._save_auto_courses()

    # ─── login + scan ───
    def _on_login(self) -> None:
        if self._is_scanning() or self._is_downloading():
            self._show_error("正忙", "请等待当前任务完成后再扫码。")
            return
        dialog = VideoLoginDialog(parent=self)
        if not dialog.exec() or dialog.result_data is None:
            return
        self._login_cookies = dialog.result_data.cookies
        self._append_log("已完成扫码登录，开始全量扫描课程节次。", "success")
        self._start_eager_scan()

    def _start_eager_scan(self) -> None:
        if self._login_cookies is None:
            return
        self._reset_progress()
        self._set_busy(True, scanning=True)
        # 在清空内存状态之前快照旧缓存（含 selected_lectures），扫描结束时
        # 把 course_failed 上报的课程从快照里捞回来。
        paths = get_app_paths()
        snapshot = self._read_auto_courses(paths.video_auto_courses_file)
        self._scan_old_courses_by_id = {
            int(c["id"]): c for c in snapshot if isinstance(c.get("id"), int)
        }
        self._scan_failed_courses = {}
        self._scan_thread = QThread(self)
        self._scan_worker = _EagerScanWorker(list(self._login_cookies))
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.log.connect(self._on_scan_log)
        self._scan_worker.course_started.connect(self._on_scan_course_started)
        self._scan_worker.course_finished.connect(self._on_scan_course_finished)
        self._scan_worker.course_failed.connect(self._on_scan_course_failed)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_worker.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        # 重新扫描，先清空内存中的课程缓存（落盘等扫完一起写）
        self._courses = []
        self._lectures_by_course = {}
        self._selection = {}
        self._tree.clear()
        self._scan_thread.start()

    def _on_scan_log(self, message: str, level: str) -> None:
        self._append_log(message, level if level in {"info", "success", "warning", "error"} else "info")

    def _on_scan_course_started(self, current: int, total: int, course_name: str) -> None:
        if total > 0:
            self._course_bar.setRange(0, total)
            self._course_bar.setValue(current - 1)
        self._course_label.setText(f"扫描进度  {current} / {total} 门 — {course_name}")

    def _on_scan_course_finished(self, course: dict) -> None:
        course_id = int(course["id"])
        # 这门课成功扫到了节次，丢掉之前记下的"待保留"标记。
        self._scan_failed_courses.pop(course_id, None)
        # 把这门课塞进内存里
        if not any(c["id"] == course_id for c in self._courses):
            self._courses.append(
                {
                    "id": course_id,
                    "name": course["name"],
                    "tool_url": course["tool_url"],
                }
            )
        self._lectures_by_course[course_id] = [
            {
                "index": lec["index"],
                "title": lec["title"],
                "lecture_id": lec["lecture_id"],
            }
            for lec in course.get("lectures") or []
        ]
        # 默认不预选
        self._selection.setdefault(course_id, set())
        # 增量推进进度条
        self._course_bar.setValue(self._course_bar.value() + 1)

    def _on_scan_course_failed(self, course_id: int, name: str, tool_url: str) -> None:
        cid = int(course_id)
        self._scan_failed_courses[cid] = {
            "id": cid,
            "name": str(name),
            "tool_url": str(tool_url),
        }

    def _on_scan_finished(self, success: bool, error: str) -> None:
        self._set_busy(False, scanning=True)
        if not success:
            self._append_log(f"扫描结束：{error}", "warning")
        else:
            # 部分课程在本轮扫描中失败（resolve_video_url / list_lectures 等），
            # 但整体 success=True：把这些课程从扫描前的快照里恢复回来，避免
            # 旧课程被一次抖动的扫描整门清掉。lecture 缓存（disk）由
            # _save_lecture_cache 按课程粒度更新，失败课程的旧节次条目本就保留。
            recovered = 0
            for cid, info in self._scan_failed_courses.items():
                if any(c["id"] == cid for c in self._courses):
                    continue
                old = self._scan_old_courses_by_id.get(cid)
                if old is None:
                    continue
                self._courses.append(
                    {
                        "id": cid,
                        "name": old.get("name") or info["name"] or f"course_{cid}",
                        "tool_url": old.get("tool_url") or info["tool_url"] or "",
                    }
                )
                self._selection.setdefault(
                    cid,
                    {
                        int(i)
                        for i in (old.get("selected_lectures") or [])
                        if isinstance(i, int)
                    },
                )
                recovered += 1
            if recovered:
                self._append_log(
                    f"已保留 {recovered} 门课程的旧缓存（本轮扫描未成功）。",
                    "info",
                )
            self._save_auto_courses()
        self._load_cached_state()
        self._scan_thread = None
        self._scan_worker = None
        self._scan_failed_courses = {}
        self._scan_old_courses_by_id = {}
        if success:
            self._append_log("全部扫描完成，可在树形列表中勾选要下载的节次。", "success")

    # ─── download ───
    def _start_download(self, dry_run: bool) -> None:
        if self._is_scanning() or self._is_downloading():
            self._show_error("已在运行", "当前已有视频任务。")
            return
        self._sync_selection_from_tree()
        selection = {cid: lids for cid, lids in self._selection.items() if lids}
        if not selection:
            self._show_error("未勾选", "请先在课程节次树里勾选要下载的节次。")
            return
        cookies = self._resolve_cookies()
        if cookies is None:
            self._show_error(
                "无可用 Cookie",
                "下载需要 Cookie：请点击「扫码登录刷新缓存」重新登录。",
            )
            return

        self._reset_progress()
        self._log.clear()
        self._set_busy(True, scanning=False)
        self._dl_thread = QThread(self)
        self._dl_worker = _DownloadWorker(
            selection=selection,
            download_dir=env_util.get_video_download_dir() or env_util.get_download_dir(),
            cookies=cookies,
            dry_run=dry_run,
        )
        self._dl_worker.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.event.connect(self._handle_event)
        self._dl_worker.finished.connect(self._on_download_finished)
        self._dl_worker.finished.connect(self._dl_thread.quit)
        self._dl_worker.finished.connect(self._dl_worker.deleteLater)
        self._dl_thread.finished.connect(self._dl_thread.deleteLater)
        self._dl_thread.start()

    def _resolve_cookies(self) -> list[dict] | None:
        if self._login_cookies:
            return list(self._login_cookies)
        path = get_app_paths().video_cookies_file
        if not path.exists():
            return None
        try:
            cached = load_cookie_cache(path)
        except (OSError, ValueError):
            return None
        if isinstance(cached, list) and cached:
            return cached
        return None

    def _on_stop(self) -> None:
        if self._dl_worker is not None:
            self._dl_worker.cancel_token.cancel()
            self._status_label.setText("终止中...")
        elif self._scan_worker is not None:
            self._scan_worker.cancel_token.cancel()
            self._status_label.setText("终止扫描中...")

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
            self._file_label.setText(f"当前课  0 / {total} 视频 — {event.course_name}")
            return
        if isinstance(event, FileProgressTick):
            self._file_value += event.step
            total = self._file_bar.maximum()
            self._file_bar.setValue(self._file_value)
            self._file_label.setText(
                f"当前课  {self._file_value} / {total} 视频 — {self._current_course_name}"
            )
            return
        if isinstance(event, FilePostfix):
            self._file_postfix.setText(event.text)
            return
        if isinstance(event, FileProgressEnded):
            self._file_postfix.setText("")
            self._reset_video_progress()
            return
        if isinstance(event, VideoBytesStarted):
            self._active_videos[event.asset_key] = {
                "downloaded": 0,
                "total": int(event.total_bytes or 0),
                "label": event.label,
            }
            self._render_video_progress()
            return
        if isinstance(event, VideoBytesProgress):
            entry = self._active_videos.get(event.asset_key)
            if entry is None:
                entry = {"downloaded": 0, "total": 0, "label": ""}
                self._active_videos[event.asset_key] = entry
            delta = int(event.downloaded) - int(entry["downloaded"])
            if delta > 0:
                self._lifetime_bytes += delta
                now = time.monotonic()
                self._speed_samples.append((now, self._lifetime_bytes))
                cutoff = now - 2.5
                # keep at least 2 samples for derivative even after long pauses
                while len(self._speed_samples) > 2 and self._speed_samples[0][0] < cutoff:
                    self._speed_samples.pop(0)
            entry["downloaded"] = int(event.downloaded)
            if event.total:
                entry["total"] = int(event.total)
            self._render_video_progress()
            return
        if isinstance(event, VideoBytesFinished):
            self._active_videos.pop(event.asset_key, None)
            self._render_video_progress()
            return
        if isinstance(event, RunFinished):
            if event.cancelled:
                self._append_log(f"已取消，已下载 {event.downloaded} 个视频", "success")
            else:
                self._append_log(f"完成！本次共下载 {event.downloaded} 个视频", "success")

    def _on_download_finished(self, rc: int) -> None:
        self._set_busy(False, scanning=False)
        self._status_label.setText("完成" if rc == 0 else f"失败（退出码 {rc}）")
        self._dl_worker = None
        self._dl_thread = None

    # ─── state helpers ───
    def _set_busy(self, busy: bool, *, scanning: bool) -> None:
        self._login_btn.setEnabled(not busy)
        self._dry_btn.setEnabled(not busy)
        self._download_btn.setEnabled(not busy)
        self._tree.setEnabled(not busy)
        self._expand_all_btn.setEnabled(not busy)
        self._collapse_all_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        if busy and scanning:
            self._status_label.setText("正在扫描全部课程的节次...")
        elif busy:
            self._status_label.setText("正在下载...")

    def _reset_progress(self) -> None:
        self._course_bar.setRange(0, 1)
        self._course_bar.setValue(0)
        self._file_bar.setRange(0, 1)
        self._file_bar.setValue(0)
        self._course_label.setText("总进度  0 / 0 门")
        self._file_label.setText("当前课  0 / 0 视频")
        self._file_postfix.setText("")
        self._course_value = 0
        self._file_value = 0
        self._current_course_name = ""
        self._reset_video_progress()

    def _reset_video_progress(self) -> None:
        self._active_videos.clear()
        self._lifetime_bytes = 0
        self._speed_samples.clear()
        self._video_bar.setRange(0, 1)
        self._video_bar.setValue(0)
        self._video_label.setText("当前视频  —")

    def _render_video_progress(self) -> None:
        if not self._active_videos:
            self._video_bar.setRange(0, 1)
            self._video_bar.setValue(0)
            self._video_label.setText("当前视频  —")
            return

        agg_done = sum(int(v["downloaded"]) for v in self._active_videos.values())
        has_unknown = any(int(v["total"]) <= 0 for v in self._active_videos.values())
        agg_total = sum(int(v["total"]) for v in self._active_videos.values())

        # Speed: bytes/sec over the sliding window of lifetime byte samples.
        speed_bps = 0.0
        if len(self._speed_samples) >= 2:
            t0, b0 = self._speed_samples[0]
            t1, b1 = self._speed_samples[-1]
            dt = t1 - t0
            if dt > 0:
                speed_bps = max(0.0, (b1 - b0) / dt)

        if has_unknown or agg_total <= 0:
            # HLS or pre-HEAD: bar in indeterminate "busy" mode.
            self._video_bar.setRange(0, 0)
        else:
            pct = int(min(100, 100 * agg_done / agg_total))
            self._video_bar.setRange(0, 100)
            self._video_bar.setValue(pct)

        if len(self._active_videos) == 1:
            label = next(iter(self._active_videos.values()))["label"] or "—"
        else:
            label = f"{len(self._active_videos)} 个视频并行"

        parts = [f"当前视频  {label}"]
        if has_unknown or agg_total <= 0:
            parts.append(f"已下载 {_format_bytes(agg_done)}")
        else:
            parts.append(f"{_format_bytes(agg_done)} / {_format_bytes(agg_total)}")
        if speed_bps > 0:
            parts.append(f"{_format_bytes(int(speed_bps))}/s")
            if not has_unknown and agg_total > agg_done:
                eta = (agg_total - agg_done) / speed_bps
                parts.append(f"ETA {_format_eta(eta)}")
        self._video_label.setText("  ·  ".join(parts))

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

    def _show_error(self, title: str, content: str) -> None:
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=4000,
        )

    @staticmethod
    def _safe_int(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _is_scanning(self) -> bool:
        try:
            return self._scan_thread is not None and self._scan_thread.isRunning()
        except RuntimeError:
            self._scan_thread = None
            self._scan_worker = None
            return False

    def _is_downloading(self) -> bool:
        try:
            return self._dl_thread is not None and self._dl_thread.isRunning()
        except RuntimeError:
            self._dl_thread = None
            self._dl_worker = None
            return False

    def is_running(self) -> bool:
        return self._is_scanning() or self._is_downloading()

    def shutdown(self) -> None:
        if self._scan_worker is not None:
            self._scan_worker.cancel_token.cancel()
        if self._dl_worker is not None:
            self._dl_worker.cancel_token.cancel()
        for thread in (self._scan_thread, self._dl_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(3000)
