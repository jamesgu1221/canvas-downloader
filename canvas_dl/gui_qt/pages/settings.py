"""设置页：主题切换 + API Token + 关于。

主题：Light / Dark / 跟随系统。"跟随系统"会重新绑定 darkdetect 监听；其它两档
则直接 setTheme 并取消跟随。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    PushButton,
    StrongBodyLabel,
    Theme,
)
from ...util import env as env_util
from ..theme import apply_system_theme, apply_theme, install_theme_listener, set_follow_system
from ._content import ContentPage


_APP_VERSION = "v1.0.2"
_REPO_URL = "https://github.com/jamesgu1221/canvas-downloader"


class SettingsPage(ContentPage):
    title = "设置"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(object_name="SettingsPage", parent=parent)

        self.add(self._build_appearance_card())
        self.add(self._build_canvas_card())
        self.add(self._build_about_card())
        self.add_stretch()

    # ─── 外观 ───
    def _build_appearance_card(self) -> QWidget:
        card = HeaderCardWidget(self)
        card.setTitle("外观")

        row = QHBoxLayout()
        row.setSpacing(10)

        label = BodyLabel("主题模式", card)
        row.addWidget(label)
        row.addStretch(1)

        self._theme_combo = ComboBox(card)
        self._theme_combo.addItems(["跟随系统", "浅色", "深色"])
        self._theme_combo.setCurrentIndex(0)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        row.addWidget(self._theme_combo)

        hint = CaptionLabel(
            "「跟随系统」会监听 Windows 设置 → 个性化 → 颜色 中的切换，实时同步窗口。",
            card,
        )
        hint.setTextColor("#777", "#aaa")
        hint.setWordWrap(True)

        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        wrap.addLayout(row)
        wrap.addWidget(hint)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

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
        card = HeaderCardWidget(self)
        card.setTitle("Canvas 连接")

        desc = BodyLabel(
            f"配置保存在 {env_util.get_config_dir()}。Token 获取方式：登录 Canvas → 账户设置 → "
            "新建访问令牌。",
            card,
        )
        desc.setWordWrap(True)

        self._url_edit = LineEdit(card)
        self._url_edit.setPlaceholderText("Canvas URL，如 https://oc.sjtu.edu.cn")
        self._url_edit.setText(env_util.get_canvas_url())
        self._url_edit.setClearButtonEnabled(True)

        self._token_edit = PasswordLineEdit(card)
        self._token_edit.setPlaceholderText("在此粘贴 Token 并点击保存")
        self._token_edit.setText(env_util.get_api_token())
        self._token_edit.setClearButtonEnabled(True)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        self._save_canvas_btn = PushButton(FIF.SAVE, "保存", card)
        self._save_canvas_btn.clicked.connect(self._on_save_canvas)
        btn_row.addWidget(self._save_canvas_btn)

        wrap = QVBoxLayout()
        wrap.setSpacing(8)
        wrap.addWidget(desc)
        wrap.addWidget(self._url_edit)
        wrap.addWidget(self._token_edit)
        wrap.addLayout(btn_row)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card

    def _on_save_canvas(self) -> None:
        url = self._url_edit.text().strip()
        token = self._token_edit.text().strip()
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
            env_util.set_canvas_url(url)
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
        InfoBar.success(
            title="已保存",
            content="Canvas URL 与 Token 已保存。",
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
        canvas_url_label = CaptionLabel(
            f"当前 Canvas 实例：{url}" if url else "尚未配置 Canvas URL", card
        )
        canvas_url_label.setTextColor("#777", "#aaa")
        canvas_url_label.setWordWrap(True)

        link = HyperlinkButton(FIF.LINK, _REPO_URL, "项目主页", card)

        wrap = QVBoxLayout()
        wrap.setSpacing(6)
        wrap.addWidget(title)
        wrap.addWidget(version)
        wrap.addWidget(canvas_url_label)
        wrap.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)

        container = QWidget(card)
        container.setLayout(wrap)
        card.viewLayout.addWidget(container)
        return card
