"""下载路径：PushSettingCard + QFileDialog，写入 `.env` 的 CANVAS_DOWNLOAD_DIR。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PushSettingCard,
    SettingCardGroup,
)

from ...util import env as env_util
from ._content import ContentPage


class PathPage(ContentPage):
    title = "下载路径"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="PathPage", parent=parent)

        group = SettingCardGroup("下载目录", self)

        current = env_util.get_download_dir()
        self._card = PushSettingCard(
            "修改路径",
            FIF.FOLDER,
            "Canvas 课件下载目录",
            current or "（尚未配置，将使用默认路径）",
            group,
        )
        self._card.clicked.connect(self._on_pick_dir)
        group.addSettingCard(self._card)

        self.add(group)
        self.add_stretch()

    def _on_pick_dir(self) -> None:
        current = env_util.get_download_dir()
        initial = current if current and Path(current).exists() else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择下载路径", initial, QFileDialog.Option.ShowDirsOnly
        )
        if not chosen:
            return
        new_dir = str(Path(chosen))
        try:
            env_util.set_download_dir(new_dir)
        except OSError as e:
            InfoBar.error(
                title="保存失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=self.window(),
                duration=5000,
            )
            return
        self._card.setContent(new_dir)
        InfoBar.success(
            title="已更新",
            content=f"下载路径已写入 .env：{new_dir}",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=3000,
        )
