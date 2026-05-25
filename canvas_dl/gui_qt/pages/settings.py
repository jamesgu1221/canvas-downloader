"""设置页：下载路径 + 视频下载设置 + 主题切换 + API Token + 关于。

主题：Light / Dark / 跟随系统。"跟随系统"会重新绑定 darkdetect 监听；其它两档
则直接 setTheme 并取消跟随。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    HeaderCardWidget,
    HyperlinkButton,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    PushSettingCard,
    SettingCard,
    SettingCardGroup,
    SpinBox,
    StrongBodyLabel,
    Theme,
)

from ...stores import (
    VIDEO_MAX_CONCURRENT_VIDEOS_RANGE,
    VIDEO_MAX_WORKERS_PER_VIDEO_RANGE,
)
from ...util import env as env_util
from ..theme import apply_system_theme, apply_theme, install_theme_listener, set_follow_system
from ._content import ContentPage


_APP_VERSION = "v1.1.0"
_REPO_URL = "https://github.com/jamesgu1221/canvas-downloader"


class SettingsPage(ContentPage):
    title = "设置"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="SettingsPage", parent=parent)

        self.add(self._build_download_path_group())
        self.add(self._build_video_download_group())
        self.add(self._build_appearance_card())
        self.add(self._build_canvas_card())
        self.add(self._build_about_card())
        self.add_stretch()

    # ─── 下载路径 ───
    def _build_download_path_group(self) -> QWidget:
        group = SettingCardGroup("课件下载设置", self)

        current = env_util.get_download_dir()
        self._download_path_card = PushSettingCard(
            "修改路径",
            FIF.FOLDER,
            "Canvas 课件下载目录",
            current or "（尚未配置，将使用默认路径）",
            group,
        )
        self._download_path_card.clicked.connect(self._on_pick_download_dir)
        group.addSettingCard(self._download_path_card)

        return group

    def _on_pick_download_dir(self) -> None:
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
            self._show_save_error(e)
            return
        self._download_path_card.setContent(new_dir)
        InfoBar.success(
            title="已更新",
            content=f"下载路径已更新：{new_dir}",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=3000,
        )

    # ─── 视频下载设置 ───
    def _build_video_download_group(self) -> QWidget:
        group = SettingCardGroup("视频下载设置", self)

        current_video = env_util.get_video_download_dir()
        self._video_path_card = PushSettingCard(
            "修改路径",
            FIF.VIDEO,
            "课堂视频下载目录",
            current_video or "（留空则与课件目录相同）",
            group,
        )
        self._video_path_card.clicked.connect(self._on_pick_video_dir)
        self._video_path_clear_btn = PushButton("恢复默认", self._video_path_card)
        self._video_path_clear_btn.setFixedWidth(96)
        self._video_path_clear_btn.clicked.connect(self._on_clear_video_dir)
        idx = self._video_path_card.hBoxLayout.indexOf(self._video_path_card.button)
        self._video_path_card.hBoxLayout.insertWidget(idx, self._video_path_clear_btn)
        self._video_path_card.hBoxLayout.insertSpacing(idx + 1, 8)
        group.addSettingCard(self._video_path_card)

        self._video_count_card, self._video_count_spin = self._build_spin_card(
            group,
            FIF.SYNC,
            "同时下载的视频数 (K)",
            "并行下载多个视频。增大可提速，但占用更多连接和带宽。",
            env_util.get_video_max_concurrent_videos(),
            VIDEO_MAX_CONCURRENT_VIDEOS_RANGE,
            env_util.set_video_max_concurrent_videos,
        )
        self._video_worker_card, self._video_worker_spin = self._build_spin_card(
            group,
            FIF.SPEED_HIGH,
            "每视频的下载线程数 (N)",
            "对单个视频使用多线程分片下载。M3U8 并发分片 / MP4 用 HTTP Range 拆分。",
            env_util.get_video_max_workers_per_video(),
            VIDEO_MAX_WORKERS_PER_VIDEO_RANGE,
            env_util.set_video_max_workers_per_video,
        )

        return group

    def _build_spin_card(
        self,
        group: SettingCardGroup,
        icon,
        title: str,
        content: str,
        initial: int,
        bounds: tuple[int, int],
        setter,
    ) -> tuple[SettingCard, SpinBox]:
        card = SettingCard(icon, title, content, group)
        spin = SpinBox(card)
        spin.setRange(bounds[0], bounds[1])
        spin.setValue(int(initial))
        spin.valueChanged.connect(lambda v: self._on_spin_changed(setter, int(v)))
        card.hBoxLayout.addWidget(spin)
        card.hBoxLayout.addSpacing(16)
        group.addSettingCard(card)
        return card, spin

    def _on_spin_changed(self, setter, value: int) -> None:
        try:
            setter(value)
        except OSError as e:
            self._show_save_error(e)

    def _on_pick_video_dir(self) -> None:
        current = env_util.get_video_download_dir() or env_util.get_download_dir()
        initial = current if current and Path(current).exists() else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "选择课堂视频下载路径", initial, QFileDialog.Option.ShowDirsOnly
        )
        if not chosen:
            return
        new_dir = str(Path(chosen))
        try:
            env_util.set_video_download_dir(new_dir)
        except OSError as e:
            self._show_save_error(e)
            return
        self._video_path_card.setContent(new_dir)
        InfoBar.success(
            title="已更新",
            content=f"视频下载路径已更新：{new_dir}",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=3000,
        )

    def _on_clear_video_dir(self) -> None:
        try:
            env_util.set_video_download_dir("")
        except OSError as e:
            self._show_save_error(e)
            return
        self._video_path_card.setContent("（留空则与课件目录相同）")
        InfoBar.success(
            title="已恢复默认",
            content="视频下载路径已恢复默认，将跟随课件目录。",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=3000,
        )

    def _show_save_error(self, error: OSError) -> None:
        InfoBar.error(
            title="保存失败",
            content=str(error),
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=5000,
        )

    # ─── 外观 ───
    def _build_appearance_card(self) -> QWidget:
        group = SettingCardGroup("外观", self)
        card = SettingCard(
            FIF.PALETTE,
            "主题模式",
            "跟随 Windows 系统颜色，或固定为浅色 / 深色。",
            group,
        )
        self._theme_combo = ComboBox(card)
        self._theme_combo.addItems(["跟随系统", "浅色", "深色"])
        self._theme_combo.setCurrentIndex(0)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        card.hBoxLayout.addWidget(self._theme_combo)
        card.hBoxLayout.addSpacing(16)
        group.addSettingCard(card)
        return group

    def _on_theme_changed(self, idx: int) -> None:
        if idx == 0:  # 跟随系统
            set_follow_system(True)
            apply_system_theme()
            install_theme_listener()
        elif idx == 1:  # 浅色
            set_follow_system(False)
            apply_theme(Theme.LIGHT)
        elif idx == 2:  # 深色
            set_follow_system(False)
            apply_theme(Theme.DARK)

    # ─── Canvas 连接 ───
    def _build_canvas_card(self) -> QWidget:
        group = SettingCardGroup("Canvas 连接", self)

        self._canvas_url_card = SettingCard(
            FIF.LINK,
            "Canvas 实例地址",
            self._canvas_url_summary(),
            group,
        )
        url_btn = PushButton("修改", self._canvas_url_card)
        url_btn.clicked.connect(self._on_edit_canvas_url)
        self._canvas_url_card.hBoxLayout.addWidget(url_btn)
        self._canvas_url_card.hBoxLayout.addSpacing(16)
        group.addSettingCard(self._canvas_url_card)

        self._canvas_token_card = SettingCard(
            FIF.SAVE,
            "Canvas API Token",
            self._canvas_token_summary(),
            group,
        )
        token_btn = PushButton("修改", self._canvas_token_card)
        token_btn.clicked.connect(self._on_edit_api_token)
        self._canvas_token_card.hBoxLayout.addWidget(token_btn)
        self._canvas_token_card.hBoxLayout.addSpacing(16)
        group.addSettingCard(self._canvas_token_card)

        return group

    def _canvas_url_summary(self) -> str:
        return env_util.get_canvas_url() or "尚未配置 Canvas 实例地址"

    def _canvas_token_summary(self) -> str:
        token = env_util.get_api_token()
        return "已配置，点击修改可更新 Token" if token else "尚未配置 Canvas API Token"

    def _refresh_canvas_summaries(self) -> None:
        self._canvas_url_card.setContent(self._canvas_url_summary())
        self._canvas_token_card.setContent(self._canvas_token_summary())
        if hasattr(self, "_canvas_url_label"):
            url = env_util.get_canvas_url()
            self._canvas_url_label.setText(
                f"当前 Canvas 实例：{url}" if url else "尚未配置 Canvas URL"
            )

    def _prompt_canvas_value(
        self,
        title: str,
        placeholder: str,
        initial: str,
        *,
        password: bool = False,
    ) -> str | None:
        dialog = QDialog(self.window())
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.resize(520, 150)

        if password:
            edit = PasswordLineEdit(dialog)
        else:
            edit = LineEdit(dialog)
        edit.setPlaceholderText(placeholder)
        edit.setText(initial)
        edit.setClearButtonEnabled(True)
        edit.setMinimumWidth(460)

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
        layout.addWidget(edit)
        layout.addLayout(btn_row)

        if not dialog.exec():
            return None
        return edit.text().strip()

    def _on_edit_canvas_url(self) -> None:
        url = self._prompt_canvas_value(
            "修改 Canvas 实例地址",
            "Canvas URL，如 https://oc.sjtu.edu.cn",
            env_util.get_canvas_url(),
        )
        if url is None:
            return
        if not url:
            InfoBar.warning(
                title="未填写",
                content="请先填写 Canvas URL。",
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=self.window(),
                duration=3000,
            )
            return
        try:
            env_util.set_canvas_url(url)
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
        self._refresh_canvas_summaries()
        InfoBar.success(
            title="已保存",
            content="Canvas 实例地址已保存。",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=3000,
        )

    def _on_edit_api_token(self) -> None:
        token = self._prompt_canvas_value(
            "修改 Canvas API Token",
            "登录 Canvas → 账户设置 → 新建访问令牌",
            env_util.get_api_token(),
            password=True,
        )
        if token is None:
            return
        if not token:
            InfoBar.warning(
                title="未填写",
                content="请先粘贴 Canvas API Token。",
                orient=Qt.Orientation.Horizontal,
                position=InfoBarPosition.TOP,
                parent=self.window(),
                duration=3000,
            )
            return
        try:
            env_util.set_api_token(token)
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
        self._refresh_canvas_summaries()
        InfoBar.success(
            title="已保存",
            content="Canvas API Token 已保存。",
            orient=Qt.Orientation.Horizontal,
            position=InfoBarPosition.TOP,
            parent=self.window(),
            duration=3000,
        )

    # ─── 关于 ───
    def _build_about_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("关于")

        title = StrongBodyLabel("Canvas 课件下载器", card)
        version = BodyLabel(f"版本：{_APP_VERSION}", card)

        url = env_util.get_canvas_url()
        self._canvas_url_label = CaptionLabel(
            f"当前 Canvas 实例：{url}" if url else "尚未配置 Canvas URL", card
        )
        self._canvas_url_label.setTextColor("#777", "#aaa")
        self._canvas_url_label.setWordWrap(True)

        link = HyperlinkButton(FIF.LINK, _REPO_URL, "项目主页", card)

        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        wrap.addWidget(title)
        wrap.addWidget(version)
        wrap.addWidget(self._canvas_url_label)
        wrap.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card
