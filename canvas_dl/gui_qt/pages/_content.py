"""业务页基类：标题 + 垂直可滚动内容。

提供：
- 统一的外边距 / 标题样式；
- 一个 `_content_layout`，子类调 `add(widget)` 即可；
- 展示时下方卡片淡入 + 轻微上滑（标题留在 root 布局里、不挂特效，
  所以保持瞬切，避免标题随之抖动）。

实现要点：
- 淡入沿用 `QGraphicsOpacityEffect`，只挂在 `_scroll` 上；
- 上滑通过给 `_scroll` 外面套一层 holder，并用 QVariantAnimation 动画
  holder 的 top margin（12 → 0）实现。这样不重写绘制管线，避免
  自定义 QGraphicsEffect 引发的兄弟控件失效重绘问题。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import SingleDirectionScrollArea, TitleLabel


_FADE_MS = 360
_SLIDE_PX = 12


class ContentPage(QWidget):
    """供 Phase 2 业务页继承：自带滚动条、淡入+上滑、一致的 Margin / 标题。"""

    title: str = ""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent)
        self.setObjectName(object_name)

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 20)
        root.setSpacing(12)

        if self.title:
            root.addWidget(TitleLabel(self.title, self))

        # holder 专门用来承担上滑：动画期间它的 top margin 从 _SLIDE_PX 收回 0
        self._scroll_holder = QWidget(self)
        self._scroll_holder.setStyleSheet("background: transparent;")
        self._holder_layout = QVBoxLayout(self._scroll_holder)
        self._holder_layout.setContentsMargins(0, 0, 0, 0)
        self._holder_layout.setSpacing(0)

        self._scroll = SingleDirectionScrollArea(
            self._scroll_holder, orient=Qt.Orientation.Vertical
        )
        self._scroll.setFrameShape(SingleDirectionScrollArea.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        # Mica 透明：让背景透出窗口的 Mica 效果；不加这些 StyleSheet 会出现灰底
        self._scroll.setStyleSheet("background: transparent;")
        self._scroll.viewport().setStyleSheet("background: transparent;")

        content_host = QWidget(self._scroll)
        content_host.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(content_host)
        # 右侧留白：qfluentwidgets 的滚动条是悬浮式（overlay），
        # 不预留空间会盖住卡片右边缘的圆角和阴影。
        self._content_layout.setContentsMargins(0, 2, 12, 0)
        self._content_layout.setSpacing(14)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(content_host)
        self._holder_layout.addWidget(self._scroll, 1)
        root.addWidget(self._scroll_holder, 1)

        # OpacityEffect 常驻：每次进入只更新 opacity，不再 attach/detach。
        # 之前在动画结束摘掉 effect 会让 _scroll 从 software pixmap 路径切回原生
        # 绘制，卡片边缘的 1px 高光像素级不一致 → 切换页签时一闪。常驻代价是
        # 滚动条 hover 加粗动画在长列表上可能略掉帧；本应用页面短，可接受。
        self._fx: QGraphicsOpacityEffect | None = None
        self._fade_anim: QPropertyAnimation | None = None
        self._slide_anim: QVariantAnimation | None = None

    # -- helpers for subclasses --
    def add(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)

    def add_stretch(self) -> None:
        self._content_layout.addStretch(1)

    # -- animation --
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._play_enter)

    def _play_enter(self) -> None:
        # 复用同一个 effect：第一次创建，之后只把 opacity 拉回 0 重放动画
        if self._fx is None:
            fx = QGraphicsOpacityEffect(self._scroll)
            self._scroll.setGraphicsEffect(fx)
            self._fx = fx
        else:
            fx = self._fx
        fx.setOpacity(0.0)

        fade = QPropertyAnimation(fx, b"opacity", self)
        fade.setDuration(_FADE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade_anim = fade

        # slide-up：通过收回 holder 的 top margin 实现轻微上滑
        self._holder_layout.setContentsMargins(0, _SLIDE_PX, 0, 0)
        slide = QVariantAnimation(self)
        slide.setDuration(_FADE_MS)
        slide.setStartValue(_SLIDE_PX)
        slide.setEndValue(0)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        slide.valueChanged.connect(self._on_slide_value)
        slide.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
        self._slide_anim = slide

    def _on_slide_value(self, value: object) -> None:
        try:
            top = int(value)  # QVariantAnimation 在中间帧可能给 float
        except (TypeError, ValueError):
            return
        self._holder_layout.setContentsMargins(0, top, 0, 0)
