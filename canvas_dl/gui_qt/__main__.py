import sys
from pathlib import Path

# pythonw.exe 下 sys.stdout / sys.stderr 为 None。PySide6 / qfluentwidgets 导入失败
# 或后续启动期异常都会被无声吞掉（VBS 双击没反应通常就是这个原因）。
# 统一重定向到项目根目录下的日志文件，让启动失败可诊断。必须在 `from .app import main`
# 之前完成，否则 app.py 的 import 链路里第一条异常就已经丢了。
if sys.stdout is None or sys.stderr is None:
    import atexit
    import traceback

    _log_path = Path(__file__).resolve().parent.parent.parent / "canvas_gui_qt.log"
    # buffering=1：行缓冲。默认块缓冲下，若 Python 在 import 阶段异常退出，
    # 尚未刷盘的 Traceback 会丢失，表现为 log 为 0 字节"双击无反应"。
    _log_file = open(_log_path, "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    atexit.register(_log_file.close)
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file

    # 兜底：即使 stderr 因为某些原因没刷盘，未捕获异常也强制写到日志并立即 flush。
    def _excepthook(exc_type, exc, tb, _f=_log_file):
        traceback.print_exception(exc_type, exc, tb, file=_f)
        _f.flush()
    sys.excepthook = _excepthook

if __name__ == "__main__":
    # 延后到日志重定向生效之后再 import app；否则 PySide6 / qfluentwidgets 缺失
    # 时的 ImportError 仍然会在 pythonw.exe 下被静默丢弃。
    from .app import main
    main()
