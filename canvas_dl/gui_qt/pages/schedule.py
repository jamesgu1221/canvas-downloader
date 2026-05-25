"""自动任务：基于 Windows Task Scheduler 的 Qt 页面。

PS 调用是同步的（一次 ~300–500ms 冷启动），必须放到后台线程里跑，
完成后通过 `_PSBridge` signal 回主线程刷新表格。
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, QTime, Qt, QTimer, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
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
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SwitchButton,
    TableWidget,
    TimePicker,
)

from ...util import schedule as sched
from ._content import ContentPage


class _PSBridge(QObject):
    done = Signal(str, int, str, str)  # (op, rc, out, err)


class SchedulePage(ContentPage):
    title = "自动任务"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="SchedulePage", parent=parent)

        self._bridge = _PSBridge()
        self._bridge.done.connect(self._on_ps_done)
        self._startup_enabled = False
        self._startup_desired_enabled = False
        self._startup_update_running = False
        self._startup_user_dirty = False
        self._startup_pin_generation = 0
        self._refresh_pending = 0
        self._refresh_requested = False
        self._schedule_busy = False

        self.add(self._build_startup_card())
        self.add(self._build_schedule_card())
        self.add_stretch()

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(30_000)
        self._auto_refresh_timer.timeout.connect(self.refresh)
        self._auto_refresh_timer.start()

        self.refresh()

    # ─── UI ───
    def _build_startup_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("开机自动下载")

        row = QHBoxLayout()
        row.setSpacing(10)

        label_wrap = QVBoxLayout()
        label_wrap.setSpacing(2)

        title = BodyLabel("Windows 登录后自动运行一次 Canvas 同步", card)
        desc = CaptionLabel("关闭开关会删除对应的任务计划程序任务。", card)
        desc.setTextColor("#777", "#aaa")
        label_wrap.addWidget(title)
        label_wrap.addWidget(desc)

        self._startup_switch = SwitchButton(card)
        self._startup_switch.setOnText("")
        self._startup_switch.setOffText("")
        self._startup_switch.checkedChanged.connect(self._on_startup_switch_changed)

        row.addLayout(label_wrap, 1)
        row.addWidget(self._startup_switch, 0, Qt.AlignmentFlag.AlignVCenter)

        container = QWidget(card)
        container.setLayout(row)
        card.viewLayout.addWidget(container)
        return card

    def _build_schedule_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("定时下载")

        row = QHBoxLayout()
        row.setSpacing(10)

        self._add_btn = PrimaryPushButton(FIF.ADD, "新增定时", card)
        self._add_btn.clicked.connect(self._on_add)
        row.addWidget(self._add_btn)

        self._modify_btn = PushButton(FIF.EDIT, "修改选中", card)
        self._modify_btn.clicked.connect(self._on_modify)
        row.addWidget(self._modify_btn)

        self._delete_btn = PushButton(FIF.DELETE, "删除选中", card)
        self._delete_btn.clicked.connect(self._on_delete)
        row.addWidget(self._delete_btn)

        row.addStretch(1)

        self._hint = CaptionLabel(
            "可设置多个每日固定时间点自动同步；列表会自动刷新。",
            card,
        )
        self._hint.setTextColor("#777", "#aaa")

        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        wrap.addLayout(row)
        wrap.addWidget(self._hint)

        self._table = TableWidget(card)
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["时间", "状态", "下次运行", "上次运行"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(220)
        self._table.setBorderVisible(True)
        self._table.setBorderRadius(6)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self._empty_label = BodyLabel("（尚未注册任何 Canvas 定时下载任务）", card)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setTextColor("#888", "#aaa")

        wrap.addWidget(self._table)
        wrap.addWidget(self._empty_label)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    # ─── data ───
    def refresh(self) -> None:
        if self._refresh_pending > 0:
            self._refresh_requested = True
            return
        self._refresh_pending = 1
        self._run_ps(
            "refresh",
            lambda: sched.run_ps(sched._QUERY_ALL_SCRIPT),  # type: ignore[attr-defined]
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _reload_table(self, tasks: list[dict]) -> None:
        backend_startup_enabled = any(self._is_startup_task(t) for t in tasks)
        self._startup_enabled = backend_startup_enabled
        if self._startup_update_running or self._startup_user_dirty:
            if (
                backend_startup_enabled == self._startup_desired_enabled
                and not self._startup_update_running
            ):
                self._startup_user_dirty = False
            elif not self._startup_update_running:
                self._sync_startup_task()
        else:
            self._startup_desired_enabled = backend_startup_enabled
            self._sync_startup_switch(backend_startup_enabled)

        daily_tasks = [t for t in tasks if not self._is_startup_task(t)]
        daily_tasks.sort(key=lambda t: t.get("time") or "99:99")
        self._table.setRowCount(len(daily_tasks))
        for row, t in enumerate(daily_tasks):
            time_val = t.get("time") or "--:--"
            state_text = sched.STATE_MAP.get(t.get("state", 0), "未知")
            next_run = t.get("nextRun") or "—"
            last_run = sched.format_last_run(t)

            items = [
                QTableWidgetItem(time_val),
                QTableWidgetItem(state_text),
                QTableWidgetItem(next_run),
                QTableWidgetItem(last_run),
            ]
            items[0].setData(Qt.ItemDataRole.UserRole, t.get("taskName", ""))
            for col, item in enumerate(items):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)

        self._empty_label.setVisible(len(daily_tasks) == 0)
        self._table.setVisible(len(daily_tasks) > 0)
        self._on_selection_changed()

    # ─── actions ───
    def _format_time(self, t: QTime) -> str:
        return f"{t.hour():02d}:{t.minute():02d}"

    def _prompt_time(self, title: str, initial: QTime | None = None) -> str | None:
        dialog = QDialog(self.window())
        dialog.setWindowTitle(title)
        dialog.setModal(True)

        picker = TimePicker(dialog)
        picker.setTime(initial or QTime.currentTime())

        ok_btn = PrimaryPushButton("确定", dialog)
        cancel_btn = PushButton("取消", dialog)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)
        layout.addWidget(picker)
        layout.addLayout(btn_row)

        if not dialog.exec():
            return None
        return self._format_time(picker.getTime())

    def _selected_time(self) -> QTime | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        text = item.text()
        if ":" not in text:
            return None
        hh, mm = text.split(":", 1)
        if not (hh.isdigit() and mm.isdigit()):
            return None
        return QTime(int(hh), int(mm))

    def _existing_times(self, exclude_task_name: str | None = None) -> set[str]:
        times: set[str] = set()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is None:
                continue
            if exclude_task_name and item.data(Qt.ItemDataRole.UserRole) == exclude_task_name:
                continue
            times.add(item.text())
        return times

    def _selected_task_name(self) -> str | None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not rows:
            return None
        row = min(rows)
        item = self._table.item(row, 0)
        return None if item is None else (item.data(Qt.ItemDataRole.UserRole) or None)

    @staticmethod
    def _is_startup_task(task: dict) -> bool:
        return (
            task.get("triggerKind") == "startup"
            or task.get("taskName") == sched.startup_task_name()
        )

    def _on_add(self) -> None:
        t = self._prompt_time("新增定时下载")
        if t is None:
            return
        if t in self._existing_times():
            self._warn("已存在", f"已存在 {t} 的定时下载任务。")
            return
        _, script = sched.register_script(t)
        self._set_schedule_busy(True)
        self._run_ps("add", lambda: sched.run_ps(script))

    def _on_startup_switch_changed(self, checked: bool) -> None:
        self._startup_desired_enabled = checked
        self._startup_user_dirty = self._startup_update_running or checked != self._startup_enabled
        self._pin_startup_switch(checked)
        self._sync_startup_task()

    def _on_modify(self) -> None:
        old = self._selected_task_name()
        if not old:
            self._warn("未选择", "请先在表格中选择一条要修改的任务。")
            return
        new_time = self._prompt_time("修改定时下载", self._selected_time())
        if new_time is None:
            return
        if old == sched.task_name(new_time):
            self._info("提示", "时间未变，无需修改。")
            return
        if new_time in self._existing_times(exclude_task_name=old):
            self._warn("已存在", f"已存在 {new_time} 的定时下载任务，无法修改。")
            return
        script = sched.modify_script(old, new_time)
        self._set_schedule_busy(True)
        self._run_ps("modify", lambda: sched.run_ps(script))

    def _on_delete(self) -> None:
        task_name = self._selected_task_name()
        if not task_name:
            self._warn("未选择", "请先在表格中选择一条要删除的任务。")
            return
        # 从表格里取当前行的时间用于确认文案
        row = next(
            (r for r in range(self._table.rowCount())
             if (it := self._table.item(r, 0)) is not None
             and it.data(Qt.ItemDataRole.UserRole) == task_name),
            -1,
        )
        time_display = self._table.item(row, 0).text() if row >= 0 else ""
        box = MessageBox("确认删除", f"删除每天 {time_display} 的定时下载任务？", self.window())
        if not box.exec():
            return
        script = sched.delete_script(task_name)
        self._set_schedule_busy(True)
        self._run_ps("delete", lambda: sched.run_ps(script))

    def _on_selection_changed(self) -> None:
        if self._schedule_busy:
            self._modify_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            return
        has_sel = self._selected_task_name() is not None
        self._modify_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)

    # ─── PS worker glue ───
    def _run_ps(self, op: str, fn: Callable[[], tuple[int, str, str]]) -> None:
        def worker() -> None:
            try:
                rc, out, err = fn()
            except Exception as e:  # noqa: BLE001
                rc, out, err = 1, "", str(e)
            self._bridge.done.emit(op, rc, out, err)

        threading.Thread(target=worker, daemon=True).start()

    def _on_ps_done(self, op: str, rc: int, out: str, err: str) -> None:
        if op in {"enable_startup", "disable_startup"}:
            self._startup_update_running = False
            target_enabled = op == "enable_startup"
            if rc != 0:
                self._startup_desired_enabled = self._startup_enabled
                self._startup_user_dirty = False
                self._sync_startup_switch(self._startup_enabled)
                self._error("失败", f"{op} 失败：{err or out or '未知错误'}")
                return
            self._startup_enabled = target_enabled
            if self._startup_desired_enabled != self._startup_enabled:
                self._startup_user_dirty = True
                self._sync_startup_task()
            else:
                self._startup_user_dirty = False
            self.refresh()
            return

        if op == "refresh":
            tasks: list[dict] = []
            parse_failed = False
            if rc == 0 and out:
                try:
                    import json as _json
                    data = _json.loads(out)
                    if isinstance(data, dict):
                        data = [data]
                    if isinstance(data, list):
                        tasks = [
                            d for d in data
                            if isinstance(d, dict) and isinstance(d.get("taskName"), str)
                        ]
                    else:
                        parse_failed = True
                except Exception:  # noqa: BLE001
                    parse_failed = True
            self._reload_table(tasks)
            if rc != 0:
                self._error("刷新失败", err or out or "调用 PowerShell 失败")
            elif parse_failed:
                self._error("刷新失败", "无法解析 PowerShell 返回的任务列表。")
            self._refresh_pending -= 1
            if self._refresh_pending <= 0:
                self._refresh_pending = 0
                if self._refresh_requested:
                    self._refresh_requested = False
                    self.refresh()
            return

        # Course file schedule ops
        self._set_schedule_busy(False)
        if rc != 0:
            self._error("失败", f"{op} 失败：{err or out or '未知错误'}")
            return

        self.refresh()

        labels = {
            "add": "新增",
            "modify": "修改",
            "delete": "删除",
        }
        InfoBar.success(
            title=labels.get(op, op),
            content=f"{labels.get(op, op)}成功。",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )

    # ─── UI state helpers ───
    def _set_schedule_busy(self, busy: bool) -> None:
        self._schedule_busy = busy
        self._add_btn.setEnabled(not busy)
        # 非 busy 时重新算 modify/delete 的可用性
        if not busy:
            self._on_selection_changed()
        else:
            self._modify_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)

    def _sync_startup_task(self) -> None:
        if self._startup_update_running:
            return
        if self._startup_desired_enabled == self._startup_enabled:
            self._startup_user_dirty = False
            self._pin_startup_switch(self._startup_desired_enabled)
            return

        self._startup_update_running = True
        if self._startup_desired_enabled:
            _, script = sched.register_startup_script()
            op = "enable_startup"
        else:
            script = sched.delete_script(sched.startup_task_name())
            op = "disable_startup"
        self._run_ps(op, lambda: sched.run_ps(script))

    def _sync_startup_switch(self, checked: bool) -> None:
        self._startup_switch.blockSignals(True)
        self._startup_switch.setChecked(checked)
        self._startup_switch.blockSignals(False)

    def _pin_startup_switch(self, checked: bool) -> None:
        self._startup_pin_generation += 1
        generation = self._startup_pin_generation
        for delay in (0, 60, 140, 260):
            QTimer.singleShot(
                delay,
                lambda value=checked, gen=generation: self._sync_startup_switch_if_current(
                    value,
                    gen,
                ),
            )

    def _sync_startup_switch_if_current(self, checked: bool, generation: int) -> None:
        if generation == self._startup_pin_generation:
            self._sync_startup_switch(checked)

    # ─── small UI helpers ───
    def _warn(self, title: str, content: str) -> None:
        InfoBar.warning(
            title=title, content=content,
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(), duration=3000,
        )

    def _info(self, title: str, content: str) -> None:
        InfoBar.info(
            title=title, content=content,
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(), duration=2500,
        )

    def _error(self, title: str, content: str) -> None:
        InfoBar.error(
            title=title, content=content,
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(), duration=4500,
        )
