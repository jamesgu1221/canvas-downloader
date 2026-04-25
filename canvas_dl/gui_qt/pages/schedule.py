"""定时任务：基于 Windows Task Scheduler，迁移自旧 Tk GUI。

PS 调用是同步的（一次 ~300–500ms 冷启动），必须放到后台线程里跑，
完成后通过 `_PSBridge` signal 回主线程刷新表格。
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, QTime, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    TableWidget,
    TimePicker,
)

from ...util import schedule as sched
from ._content import ContentPage


class _PSBridge(QObject):
    done = Signal(str, int, str, str)  # (op, rc, out, err)


class SchedulePage(ContentPage):
    title = "定时任务"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="SchedulePage", parent=parent)

        self._bridge = _PSBridge()
        self._bridge.done.connect(self._on_ps_done)

        self.add(self._build_action_card())
        self.add(self._build_table_card())
        self.add_stretch()

        self.refresh()

    # ─── UI ───
    def _build_action_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("时间")

        row = QHBoxLayout()
        row.setSpacing(10)

        self._time_picker = TimePicker(card)
        self._time_picker.setTime(QTime(22, 0))
        row.addWidget(self._time_picker)

        self._add_btn = PrimaryPushButton(FIF.ADD, "新增", card)
        self._add_btn.clicked.connect(self._on_add)
        row.addWidget(self._add_btn)

        self._modify_btn = PushButton(FIF.EDIT, "修改选中", card)
        self._modify_btn.clicked.connect(self._on_modify)
        row.addWidget(self._modify_btn)

        self._delete_btn = PushButton(FIF.DELETE, "删除选中", card)
        self._delete_btn.clicked.connect(self._on_delete)
        row.addWidget(self._delete_btn)

        self._refresh_btn = PushButton(FIF.SYNC, "刷新", card)
        self._refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self._refresh_btn)

        row.addStretch(1)

        self._hint = CaptionLabel(
            "每日固定时间自动运行一次 Canvas 同步；依赖 Windows 任务计划程序。",
            card,
        )
        self._hint.setTextColor("#777", "#aaa")

        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        wrap.addLayout(row)
        wrap.addWidget(self._hint)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    def _build_table_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("已注册任务")

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

        self._empty_label = BodyLabel("（尚未注册任何 Canvas 定时任务）", card)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setTextColor("#888", "#aaa")

        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        wrap.addWidget(self._table)
        wrap.addWidget(self._empty_label)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    # ─── data ───
    def refresh(self) -> None:
        self._set_busy(True)
        self._run_ps(
            "refresh",
            lambda: sched.run_ps(sched._QUERY_ALL_SCRIPT),  # type: ignore[attr-defined]
        )

    def _reload_table(self, tasks: list[dict]) -> None:
        tasks.sort(key=lambda t: t.get("time") or "99:99")
        self._table.setRowCount(len(tasks))
        for row, t in enumerate(tasks):
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
            # 把 taskName 挂在第 0 列的 UserRole 上，后续修改/删除直接用
            items[0].setData(Qt.ItemDataRole.UserRole, t.get("taskName", ""))
            for col, item in enumerate(items):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)

        self._empty_label.setVisible(len(tasks) == 0)
        self._table.setVisible(len(tasks) > 0)
        self._on_selection_changed()

    # ─── actions ───
    def _picked_time_str(self) -> str:
        t: QTime = self._time_picker.getTime()
        return f"{t.hour():02d}:{t.minute():02d}"

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

    def _on_add(self) -> None:
        t = self._picked_time_str()
        if t in self._existing_times():
            self._warn("已存在", f"已存在 {t} 的定时任务。")
            return
        _, script = sched.register_script(t)
        self._set_busy(True)
        self._run_ps("add", lambda: sched.run_ps(script))

    def _on_modify(self) -> None:
        old = self._selected_task_name()
        if not old:
            self._warn("未选择", "请先在表格中选择一条要修改的任务。")
            return
        new_time = self._picked_time_str()
        if old == sched.task_name(new_time):
            self._info("提示", "时间未变，无需修改。")
            return
        if new_time in self._existing_times(exclude_task_name=old):
            self._warn("已存在", f"已存在 {new_time} 的定时任务，无法修改。")
            return
        script = sched.modify_script(old, new_time)
        self._set_busy(True)
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
        box = MessageBox("确认删除", f"删除每天 {time_display} 的定时任务？", self.window())
        if not box.exec():
            return
        script = sched.delete_script(task_name)
        self._set_busy(True)
        self._run_ps("delete", lambda: sched.run_ps(script))

    def _on_selection_changed(self) -> None:
        has_sel = self._selected_task_name() is not None
        self._modify_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)
        # 选中后把时间同步到 TimePicker 方便修改
        if has_sel:
            item = self._table.item(self._table.currentRow(), 0)
            if item is not None:
                text = item.text()
                if ":" in text and not text.startswith("-"):
                    hh, mm = text.split(":", 1)
                    if hh.isdigit() and mm.isdigit():
                        self._time_picker.setTime(QTime(int(hh), int(mm)))

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
        self._set_busy(False)
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
                # PowerShell 调用失败：权限不足、execution policy 阻挡等；用户需要知道，
                # 而不是看到一张假装"空"的任务表。
                self._error("刷新失败", err or out or "调用 PowerShell 失败")
            elif parse_failed:
                self._error("刷新失败", "无法解析 PowerShell 返回的任务列表。")
            return

        if rc != 0:
            self._error("失败", f"{op} 失败：{err or out or '未知错误'}")
            return

        # add / modify / delete 成功后一律重新查一次，确保表格与系统一致
        self.refresh()

        labels = {"add": "新增", "modify": "修改", "delete": "删除"}
        InfoBar.success(
            title=labels.get(op, op),
            content=f"{labels.get(op, op)}成功。",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=2500,
        )

    # ─── UI state helpers ───
    def _set_busy(self, busy: bool) -> None:
        for btn in (self._add_btn, self._modify_btn, self._delete_btn, self._refresh_btn):
            btn.setEnabled(not busy)
        # 非 busy 时重新算 modify/delete 的可用性
        if not busy:
            self._on_selection_changed()

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
