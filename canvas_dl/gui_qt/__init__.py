"""PySide6 + qfluentwidgets GUI (Win11 Mica 风格).

启动方式：
    python -m canvas_dl.gui_qt
    或双击 canvas_gui.vbs（无黑窗启动）。

功能：Mica 外壳 + NavigationInterface + 五个业务页（课件下载 / 自动任务 / 课堂视频 /
课程管理 / 设置），以及主题切换（跟随系统 / 浅色 / 深色）。

注意：这里故意不做 `from .app import main`。`-m canvas_dl.gui_qt` 启动时会先执行
本文件，若此处急着 import app，而 PySide6 / qfluentwidgets 尚未安装，
ImportError 会在 pythonw.exe 下被无声吞掉，连 `__main__.py` 里的日志重定向
都还没生效。把 import 延后到 `__main__.py` 的重定向之后，才能把 traceback
落进 canvas_gui_qt.log 用于诊断。
"""

__all__ = ["main"]


def main() -> None:  # pragma: no cover — thin lazy wrapper
    from .app import main as _main
    _main()
