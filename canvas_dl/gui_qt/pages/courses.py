"""课程管理：基于 courses.json 的 SwitchButton 列表。

外部变更通过 `QFileSystemWatcher` 监听。Windows 下编辑器常先删后建
（保存原子化），fileChanged 会失效，因此同时监听所在目录，任何写入都重新
stat → 比对 mtime 后决定是否重新渲染。
"""

from __future__ import annotations

import json
from PySide6.QtCore import (
    QFileSystemWatcher,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
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
    PushButton,
    StrongBodyLabel,
    SwitchButton,
)

from ... import courses_config as cc
from ...paths import get_app_paths
from ._content import ContentPage


APP_PATHS = get_app_paths()
COURSES_JSON = APP_PATHS.courses_file


class CoursesPage(ContentPage):
    title = "课程管理"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="CoursesPage", parent=parent)

        self._courses_data: dict | None = None
        self._rows: list[tuple[SwitchButton, dict]] = []
        self._last_mtime: float | None = None
        # 记录自写后的 mtime：只要磁盘 mtime 和它相等就认为是自写，防抖里可以安全跳过。
        # 若在 200ms 窗口内外部再写一次导致 mtime 不同，就当作真实外部变更处理。
        self._self_saved_mtime: float | None = None

        # 轻微防抖：write 事件常一次编辑触发多次（PS/编辑器），200ms 内合并重载
        self._reload_debounce = QTimer(self)
        self._reload_debounce.setSingleShot(True)
        self._reload_debounce.setInterval(200)
        self._reload_debounce.timeout.connect(self._maybe_reload_from_disk)

        self.add(self._build_header_card())
        self.add(self._build_list_card())
        self.add_stretch()

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)
        self._install_watchers()

        self.load()

    # ─── UI ───
    def _build_header_card(self) -> QWidget:
        container = QWidget(self)
        container.setStyleSheet("background: transparent;")

        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 2, 0)
        row.addStretch(1)

        self._reload_btn = PushButton(FIF.SYNC, "重新加载", container)
        self._reload_btn.clicked.connect(self.load)
        row.addWidget(self._reload_btn)

        return container

    def _build_list_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("课程列表")

        self._list_host = QWidget(card)
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._empty_label = BodyLabel("", card)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._empty_label.setTextColor("#888", "#aaa")
        self._empty_label.setWordWrap(True)

        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(4)
        wrap.addWidget(self._empty_label)
        wrap.addWidget(self._list_host)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    # ─── load / render ───
    def load(self) -> None:
        self._clear_rows()
        self._last_mtime = self._stat_mtime()

        if not COURSES_JSON.exists():
            self._empty_label.setText(
                "尚未生成 courses.json —— 请先到「课件下载」点击「立即运行」，首次同步后会自动生成。"
            )
            self._empty_label.setVisible(True)
            self._list_host.setVisible(False)
            self._courses_data = None
            return

        try:
            self._courses_data = cc.load_or_init(COURSES_JSON)
        except json.JSONDecodeError as e:
            self._empty_label.setText(f"解析 courses.json 失败：{e}")
            self._empty_label.setVisible(True)
            self._list_host.setVisible(False)
            self._courses_data = None
            return

        self._empty_label.setVisible(False)
        self._list_host.setVisible(True)
        self._render_rows()

    def _render_rows(self) -> None:
        courses = (self._courses_data or {}).get("courses", [])
        actives = [c for c in courses if c.get("active", True)]
        inactives = [c for c in courses if not c.get("active", True)]

        for entry in actives + inactives:
            self._list_layout.addWidget(self._make_row(entry))

        if not courses:
            placeholder = BodyLabel("（Canvas 上未返回任何课程）", self._list_host)
            placeholder.setTextColor("#888", "#aaa")
            self._list_layout.addWidget(placeholder)

    def _make_row(self, entry: dict) -> QWidget:
        row = QWidget(self._list_host)
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(10)

        is_inactive = not entry.get("active", True)
        name = entry.get("name", f"course_{entry.get('id')}")
        cid = entry.get("id")

        name_label = StrongBodyLabel(name, row)
        name_label.setWordWrap(True)
        name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        id_label = CaptionLabel(f"[{cid}]", row)
        id_label.setTextColor("#777", "#aaa")
        id_label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )

        lay.addWidget(name_label, 1)
        lay.addWidget(id_label)

        if is_inactive:
            tag = CaptionLabel("Canvas 上已不可见，开启以强制激活", row)
            tag.setTextColor("#b26a00", "#e7a24a")
            tag.setWordWrap(True)
            tag.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Preferred,
            )
            lay.addWidget(tag)

        sw = SwitchButton(row)
        sw.setOnText("")
        sw.setOffText("")
        sw.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        sw.setChecked(bool(entry.get("enabled", True)))
        sw.checkedChanged.connect(lambda _checked, e=entry: self._on_switch_changed(e))
        lay.addWidget(sw)

        self._rows.append((sw, entry))
        return row

    def _clear_rows(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []

    # ─── save ───
    def _on_switch_changed(self, entry: dict) -> None:
        if self._courses_data is None:
            return
        # 读当前所有 switch 的状态，批量写 data；同时记录是否触发了 active 翻转，
        # 只有翻转才需要重排 UI——否则全量重建会让其它已启用开关也跟着滑动一次。
        needs_reorder = False
        for sw, e in self._rows:
            new_enabled = sw.isChecked()
            old_active = e.get("active", True)
            new_active = True if (new_enabled and not old_active) else old_active
            if new_active != old_active:
                needs_reorder = True
            e["enabled"] = new_enabled
            e["active"] = new_active

        try:
            cc.save(COURSES_JSON, self._courses_data)
            mtime = self._stat_mtime()
            self._last_mtime = mtime
            self._self_saved_mtime = mtime
        except OSError as e:
            self._self_saved_mtime = None
            InfoBar.error(
                title="保存失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=self.window(),
                duration=5000,
            )
            # 回滚到磁盘状态
            self.load()
            return

        # 仅在 inactive 课程被重新启用、需要从下半区挪到上半区并去掉橙色标签时
        # 才整体重建。普通启用/禁用切换不重建，这样其它开关不会被动重画。
        if needs_reorder:
            self._clear_rows()
            self._render_rows()

    # ─── file system watcher ───
    def _install_watchers(self) -> None:
        paths: list[str] = [str(APP_PATHS.base_dir)]
        if COURSES_JSON.exists():
            paths.append(str(COURSES_JSON))
        # 已有的 watchers 清空再加
        existing = self._watcher.files() + self._watcher.directories()
        if existing:
            self._watcher.removePaths(existing)
        self._watcher.addPaths(paths)

    def _on_fs_event(self, _path: str) -> None:
        # 文件被保存时某些编辑器会短暂移除监听目标，需要重新 addPath
        if not self._watcher.files() or str(COURSES_JSON) not in self._watcher.files():
            if COURSES_JSON.exists():
                self._watcher.addPath(str(COURSES_JSON))
        self._reload_debounce.start()

    def _maybe_reload_from_disk(self) -> None:
        mtime = self._stat_mtime()
        if mtime == self._last_mtime:
            return
        # 用 mtime 精确比对自写标记：仅当磁盘 mtime 就是我们刚写完记下的那个 mtime 时，
        # 才能确定这一波 fs 事件全部来自自身（防抖窗口内可能合并多个事件）。
        # 若外部工具在 200ms 内又写了一次，mtime 会不同，此时应按真实外部变更刷新。
        if self._self_saved_mtime is not None and mtime == self._self_saved_mtime:
            self._last_mtime = mtime
            self._self_saved_mtime = None
            return
        self._self_saved_mtime = None
        self.load()

    def _stat_mtime(self) -> float | None:
        try:
            return COURSES_JSON.stat().st_mtime if COURSES_JSON.exists() else None
        except OSError:
            return None
