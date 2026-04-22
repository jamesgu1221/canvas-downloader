import os

from tqdm import tqdm


# GUI 模式下，进度条不走终端 tqdm，而是把事件以 @@PROGRESS@@\t... 单行
# ASCII 的形式发到 stdout，由 canvas_dl.gui 的子进程读取管道驱动 ttk.Progressbar。
# 保留与 tqdm 兼容的接口：__enter__ / __exit__ / update / set_postfix_str / write。
# #19: accept common truthy values, not just "1"
GUI_MODE = os.environ.get("CANVAS_DL_GUI_MODE", "").lower() in ("1", "true", "yes", "on")


class _GuiBar:
    def __init__(self, kind: str, total: int, label: str = ""):
        self.kind = kind
        if kind == "course":
            print(f"@@PROGRESS@@\tcourse\tstart\t{total}", flush=True)
        else:
            print(f"@@PROGRESS@@\tfile\tstart\t{label}\t{total}", flush=True)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        print(f"@@PROGRESS@@\t{self.kind}\tend", flush=True)
        return False

    def update(self, n: int = 1):
        print(f"@@PROGRESS@@\t{self.kind}\ttick\t{n}", flush=True)

    def set_postfix_str(self, s: str):
        if self.kind == "file":
            # 制表符会破坏事件分隔，替换为空格
            safe = (s or "").replace("\t", " ").replace("\n", " ")
            print(f"@@PROGRESS@@\tfile\tpostfix\t{safe}", flush=True)

    def write(self, msg: str):
        print(msg, flush=True)

    def close(self):
        pass


def make_course_bar(total: int):
    if GUI_MODE:
        return _GuiBar("course", total)
    return tqdm(total=total, unit="门课", desc="总进度", position=0)


def make_file_bar(course_name: str, total: int):
    label = course_name[:28] if len(course_name) > 28 else course_name
    if GUI_MODE:
        return _GuiBar("file", total, label)
    return tqdm(total=total, unit="个文件", desc=f"  {label}", position=1, leave=False)


def report_empty_course(course_name: str) -> None:
    """刷新 GUI "当前课" 为这门无文件的课，避免停留在上一门课的名字上。

    CLI (tqdm) 模式无需处理 —— `_log` 已经在终端上打出了 "[课程名] 无文件"。
    """
    if not GUI_MODE:
        return
    label = course_name[:28] if len(course_name) > 28 else course_name
    print(f"@@PROGRESS@@\tfile\tstart\t{label}\t0", flush=True)
    print(f"@@PROGRESS@@\tfile\tend", flush=True)
