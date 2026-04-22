"""Tkinter 图形化入口。

启动：`python -m canvas_dl.gui`（或双击 canvas_gui.vbs）。
"""
import base64
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# pythonw.exe 下 sys.stdout / sys.stderr 为 None。课程模块可能在 import
# 期间打印，统一重定向到项目根目录下的日志文件，避免 AttributeError。
if sys.stdout is None or sys.stderr is None:
    import atexit
    _log_path = Path(__file__).resolve().parent.parent / "canvas_gui.log"
    # #25: errors="replace" prevents UnicodeEncodeError on unusual filenames in log
    _log_file = open(_log_path, "a", encoding="utf-8", errors="replace")  # noqa: SIM115
    atexit.register(_log_file.close)
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file

from dotenv import dotenv_values, set_key as _dotenv_set_key

from . import courses_config as cc

# 常量
PYTHON = sys.executable
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
WORK_DIR = Path(__file__).resolve().parent.parent
TASK_PREFIX = "Canvas课件下载"
COURSES_JSON = WORK_DIR / "courses.json"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_STATE_MAP = {0: "未知", 1: "已禁用", 2: "排队中", 3: "就绪", 4: "运行中"}


def _task_name(time_str: str) -> str:
    # Task Scheduler 不允许任务名含冒号，将 HH:MM 改为 HH-MM
    return f"{TASK_PREFIX} \u2014 {time_str.replace(':', '-')}"


def _ps(script: str) -> tuple[int, str, str]:
    """用 -EncodedCommand 传 PS 脚本，规避命令行引号/中文转义坑。"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


_QUERY_ALL_SCRIPT = fr"""
$tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ $_.TaskName -like '{TASK_PREFIX}*' }})
if ($tasks.Count -eq 0) {{ Write-Output '[]'; exit 0 }}
$results = $tasks | ForEach-Object {{
    $t = $_
    $i = Get-ScheduledTaskInfo -TaskName $t.TaskName -ErrorAction SilentlyContinue
    $time = $null
    $trg = $t.Triggers | Select-Object -First 1
    if ($trg -and $trg.StartBoundary -match 'T(\d{{2}}:\d{{2}})') {{ $time = $Matches[1] }}
    $lastRun = $null; $nextRun = $null
    if ($i -and $i.LastRunTime -and $i.LastRunTime.Year -gt 1970) {{ $lastRun = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') }}
    if ($i -and $i.NextRunTime -and $i.NextRunTime.Year -gt 1970) {{ $nextRun = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') }}
    $lastResult = if ($i) {{ [int]$i.LastTaskResult }} else {{ 0 }}
    [PSCustomObject]@{{
        taskName   = $t.TaskName
        time       = $time
        state      = [int]$t.State
        lastRun    = $lastRun
        lastResult = $lastResult
        nextRun    = $nextRun
    }}
}}
ConvertTo-Json -InputObject @($results) -Compress
"""

_TASK_SETTINGS_FRAGMENT = (
    "-StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
    "-WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2)"
)


def query_all_schedules() -> list[dict]:
    rc, out, err = _ps(_QUERY_ALL_SCRIPT)
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict):  # PS 5.1 单条目安全兜底
            data = [data]
        if not isinstance(data, list):
            return []
        # #18: validate required field so malformed entries don't reach Treeview
        return [d for d in data if isinstance(d, dict) and isinstance(d.get("taskName"), str)]
    except json.JSONDecodeError:
        return []


def _ps_escape(s: str) -> str:
    """在 PowerShell 单引号字符串中转义单引号（重复一次）。"""
    return s.replace("'", "''")


def _register_script(time_str: str) -> tuple[str, str]:
    """返回 (task_name, ps_script)，用于注册每天 `time_str` 的 daily 任务。"""
    task_name = _task_name(time_str)
    pythonw = _ps_escape(str(PYTHONW))
    work_dir = _ps_escape(str(WORK_DIR))
    tn = _ps_escape(task_name)
    script = fr"""
$act = New-ScheduledTaskAction -Execute '{pythonw}' -Argument '-m canvas_dl' -WorkingDirectory '{work_dir}'
$trg = New-ScheduledTaskTrigger -Daily -At '{time_str}'
$set = New-ScheduledTaskSettingsSet {_TASK_SETTINGS_FRAGMENT}
$prin = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{tn}' -Action $act -Trigger $trg -Settings $set -Principal $prin -Force | Out-Null
"""
    return task_name, script


def register_schedule(time_str: str) -> tuple[bool, str]:
    _, script = _register_script(time_str)
    rc, out, err = _ps(script)
    return rc == 0, err or out


def modify_script(old_task_name: str, new_time: str) -> str:
    """原子合并：一次 PS 调用里先 Unregister 再 Register，省掉一次冷启动。"""
    old_tn = _ps_escape(old_task_name)
    _, reg_script = _register_script(new_time)
    return fr"""
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName '{old_tn}' -Confirm:$false
{reg_script}
"""


def modify_schedule(old_task_name: str, new_time: str) -> tuple[bool, str]:
    rc, out, err = _ps(modify_script(old_task_name, new_time))
    return rc == 0, err or out


def delete_script(task_name: str) -> str:
    tn = _ps_escape(task_name)
    return f"Unregister-ScheduledTask -TaskName '{tn}' -Confirm:$false"


def delete_schedule(task_name: str) -> tuple[bool, str]:
    rc, out, err = _ps(delete_script(task_name))
    return rc == 0, err or out


class _BlueBar(tk.Canvas):
    """Canvas-based progress bar — fills its full height, works on all Windows themes."""
    _BAR = "#1976D2"
    _TROUGH = "#e0e0e0"
    _BORDER = "#cccccc"

    def __init__(self, parent, **kw):
        kw.setdefault("background", self._TROUGH)
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", self._BORDER)
        kw.setdefault("bd", 0)
        super().__init__(parent, **kw)
        self._value = 0
        self._maximum = 100
        self.bind("<Configure>", lambda _e: self._draw())

    def configure(self, **kw):
        if "value" in kw:
            self._value = kw.pop("value")
        if "maximum" in kw:
            self._maximum = kw.pop("maximum")
        super().configure(**kw)
        self._draw()

    def __getitem__(self, key):
        if key == "maximum":
            return self._maximum
        if key == "value":
            return self._value
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def _draw(self):
        self.delete("bar")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or self._maximum <= 0:
            return
        fill_w = max(0, min(w, round(w * self._value / self._maximum)))
        if fill_w:
            self.create_rectangle(0, 0, fill_w, h, fill=self._BAR, outline="", tags="bar")


class _Card(tk.Frame):
    """Win11 风格圆角卡片，替代 ttk.LabelFrame。子控件挂到 self.inner。

    外层用 tk.Frame（非 ttk，避免 vista 主题原生渲染覆盖 Canvas）。
    内置 tk.Canvas 通过 place(relwidth=1,relheight=1) 覆盖整个区域，
    绘制白色填充的圆角矩形 + 灰色边框，形成明显的 Win11 卡片感。
    self.inner 用 pack 布局驱动外层高度，子控件直接挂到 self.inner。
    """
    _BG     = "#ffffff"   # 卡片白色填充
    _BORDER = "#c0c0c0"   # 边框颜色
    _TITLE  = "#5c5c5c"   # 标题文字色
    _PAD_SIDE   = 10
    _PAD_BOTTOM = 8

    def __init__(self, parent, title: str = "", radius: int = 8, **kw):
        _s = ttk.Style()
        win_bg = _s.lookup("TFrame", "background") or "SystemButtonFace"
        # Card.TFrame 样式：让内层 ttk.Frame 也呈现白色背景
        _s.configure("Card.TFrame", background=self._BG)

        kw.setdefault("bg", win_bg)
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 0)
        super().__init__(parent, **kw)

        self._title_str = title
        self._r = radius
        pad_top = 26 if title else 6
        self._title_y = pad_top // 2

        # Canvas 先于 inner 创建 → z 序在下方，place 覆盖全区域
        self._cv = tk.Canvas(self, bd=0, highlightthickness=0, bg=win_bg)
        self._cv.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        # 内容框（白色背景）：pack 驱动外层 Frame 高度
        self.inner = ttk.Frame(self, style="Card.TFrame")
        self.inner.pack(
            fill="both", expand=True,
            padx=self._PAD_SIDE,
            pady=(pad_top, self._PAD_BOTTOM),
        )

        self.bind("<Configure>", self._redraw)

    def _redraw(self, e=None):
        self._cv.delete("card_deco")
        w = e.width  if e else self.winfo_width()
        h = e.height if e else self.winfo_height()
        if w < 4 or h < 4:
            return
        # 白色圆角矩形（在 padding 区域与 inner 内部均可见）
        self._rr(1, 1, w - 2, h - 2, self._r,
                 fill=self._BG, outline=self._BORDER, width=1, tags="card_deco")
        if self._title_str:
            self._cv.create_text(
                self._r + 8, self._title_y,
                text=self._title_str, anchor="w",
                fill=self._TITLE, tags="card_deco",
            )

    def _rr(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1,   x2 - r, y1,
               x2,     y1,   x2,     y1 + r,
               x2,     y2 - r, x2,   y2,
               x2 - r, y2,   x1 + r, y2,
               x1,     y2,   x1,     y2 - r,
               x1,     y1 + r, x1,   y1]
        self._cv.create_polygon(pts, smooth=True, **kw)


class CanvasDownloaderGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Canvas 课件下载器")
        # Scale the initial window size to match the actual DPI so the window
        # occupies the same logical area on every display density.
        _dpi = root.winfo_fpixels("1i")
        _scale = _dpi / 96.0
        root.geometry(f"{int(760 * _scale)}x{int(860 * _scale)}")
        root.minsize(int(640 * _scale), int(640 * _scale))

        self.proc: subprocess.Popen | None = None
        self.course_vars: list[tuple[tk.BooleanVar, dict]] = []
        self.courses_data: dict | None = None
        self._current_course_name: str = ""
        self._course_prog_value: int = 0
        self._file_prog_value: int = 0
        self._courses_json_mtime: float | None = None
        # save_courses() 写盘后 _poll_courses_json 可能在 stat 之前就发现 mtime 变化
        # 从而多跑一次 load_courses()；置此旗标让轮询把这次变化当成自写，只同步 mtime。
        self._skip_next_self_save_poll: bool = False

        self._build_ui()
        self.refresh_status()
        self.load_courses()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(2000, self._poll_courses_json)

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", **pad)
        self.run_btn = ttk.Button(action_frame, text="▶  立即运行", command=self.run_once, width=16)
        self.run_btn.pack(side="left", padx=(10, 12))
        self.dry_run_var = tk.BooleanVar(value=False)  # kept for CLI passthrough; checkbox hidden

        _s = ttk.Style()
        # Treeview row height defaults to font ascent only and doesn't scale with DPI.
        _line_h = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        _s.configure("Treeview", rowheight=_line_h + 10)

        # Progress bar height: match one line of text plus a little padding
        _bar_h = _line_h + 6

        prog_frame = ttk.LabelFrame(self.root, text="进度")
        prog_frame.pack(fill="x", **pad)
        self.course_prog_label = ttk.Label(prog_frame, text="总进度  0 / 0 门")
        self.course_prog_label.pack(anchor="w", padx=10, pady=(8, 2))
        self.course_prog = _BlueBar(prog_frame, height=_bar_h)
        self.course_prog.pack(fill="x", padx=10, pady=(0, 6))
        self.file_prog_label = ttk.Label(prog_frame, text="当前课  0 / 0 文件")
        self.file_prog_label.pack(anchor="w", padx=10, pady=(2, 2))
        self.file_prog = _BlueBar(prog_frame, height=_bar_h)
        self.file_prog.pack(fill="x", padx=10, pady=(0, 4))
        self.file_postfix_label = ttk.Label(prog_frame, text="", foreground="#666")
        self.file_postfix_label.pack(anchor="w", padx=10, pady=(0, 8))

        sched_lf = ttk.LabelFrame(self.root, text="定时任务")
        sched_lf.pack(fill="x", **pad)
        sched_frame = _Card(sched_lf, radius=14)
        sched_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        top_row = ttk.Frame(sched_frame.inner, style="Card.TFrame")
        top_row.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(top_row, text="时间").pack(side="left")
        self.hh_var = tk.StringVar(value="22")
        self.mm_var = tk.StringVar(value="00")
        # #27: restrict Spinbox input to digits only so rounding is never ambiguous
        _vcmd = (self.root.register(lambda v: v == "" or v.isdigit()), "%P")
        tk.Spinbox(top_row, from_=0, to=23, width=4, format="%02.0f",
                   textvariable=self.hh_var, wrap=True,
                   validate="key", validatecommand=_vcmd,
                   bg="white", relief="flat",
                   highlightthickness=1, highlightbackground="#c0c0c0",
                   highlightcolor="#0078d4").pack(side="left", padx=(6, 2))
        ttk.Label(top_row, text=":").pack(side="left")
        tk.Spinbox(top_row, from_=0, to=59, width=4, format="%02.0f",
                   textvariable=self.mm_var, wrap=True,
                   validate="key", validatecommand=_vcmd,
                   bg="white", relief="flat",
                   highlightthickness=1, highlightbackground="#c0c0c0",
                   highlightcolor="#0078d4").pack(side="left", padx=(2, 14))
        ttk.Button(top_row, text="新增", command=self.add_schedule).pack(side="left", padx=4)

        cols = ("time", "state", "nextRun", "lastRun")
        self.sched_tree = ttk.Treeview(sched_frame.inner, columns=cols, show="headings", height=4)
        self.sched_tree.heading("time",    text="时间")
        self.sched_tree.heading("state",   text="状态")
        self.sched_tree.heading("nextRun", text="下次运行")
        self.sched_tree.heading("lastRun", text="上次运行")
        self.sched_tree.column("time",    width=60,  stretch=False)
        self.sched_tree.column("state",   width=70,  stretch=False)
        self.sched_tree.column("nextRun", width=160, stretch=True)
        self.sched_tree.column("lastRun", width=160, stretch=True)
        self.sched_tree.pack(fill="x", padx=10, pady=2)
        self.sched_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        bot_row = ttk.Frame(sched_frame.inner, style="Card.TFrame")
        bot_row.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Button(bot_row, text="修改时间", command=self.modify_schedule).pack(side="left", padx=4)
        ttk.Button(bot_row, text="删除选中", command=self.remove_schedule).pack(side="left", padx=4)

        path_lf = ttk.LabelFrame(self.root, text="下载路径")
        path_lf.pack(fill="x", **pad)
        path_frame = _Card(path_lf, radius=14)
        path_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        path_row = ttk.Frame(path_frame.inner, style="Card.TFrame")
        path_row.pack(fill="x", padx=10, pady=8)
        self._dl_dir_var = tk.StringVar(value=self._load_download_dir())
        self._dl_dir_entry = tk.Entry(path_row, textvariable=self._dl_dir_var,
                                      state="readonly", readonlybackground="white",
                                      relief="flat", highlightthickness=1,
                                      highlightbackground="#c0c0c0",
                                      highlightcolor="#0078d4")
        self._dl_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(path_row, text="修改路径", command=self._browse_download_dir).pack(side="left")

        log_lf = ttk.LabelFrame(self.root, text="日志")
        log_lf.pack(fill="both", expand=True, **pad)
        log_frame = _Card(log_lf, radius=14)
        log_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        self.log = ScrolledText(log_frame.inner, height=10, wrap="word",
                                font=("Microsoft YaHei UI", 10),
                                bg="#ffffff", relief="flat", highlightthickness=0)
        self.log.pack(fill="both", expand=True, padx=10, pady=8)

        courses_frame = ttk.LabelFrame(self.root, text="课程启用")
        courses_frame.pack(fill="both", expand=True, **pad)
        list_holder = ttk.Frame(courses_frame)
        list_holder.pack(fill="both", expand=True, padx=10, pady=(8, 4))

        self.courses_canvas = tk.Canvas(list_holder, highlightthickness=0, height=180, borderwidth=0)
        scrollbar = ttk.Scrollbar(list_holder, orient="vertical", command=self.courses_canvas.yview)
        self.courses_canvas.configure(yscrollcommand=scrollbar.set)
        self.courses_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.courses_inner = ttk.Frame(self.courses_canvas)
        self.courses_window = self.courses_canvas.create_window((0, 0), window=self.courses_inner, anchor="nw")
        self.courses_inner.bind(
            "<Configure>",
            lambda e: self.courses_canvas.configure(scrollregion=self.courses_canvas.bbox("all")),
        )
        self.courses_canvas.bind(
            "<Configure>",
            lambda e: self.courses_canvas.itemconfig(self.courses_window, width=e.width),
        )
        # 鼠标滚轮：全局绑定，处理函数里按指针位置判断是否滚课程列表
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)


    # ─── 定时任务异步执行 & 列表就地更新 ───
    def _ps_async(self, script: str, on_done):
        """后台线程跑 PS，完成后回到 UI 线程调 on_done(rc, out, err)。

        每次 PS 调用的冷启动成本 ~300–500ms，同步执行会阻塞整个 Tk 主循环。
        放到线程里后 UI 始终可响应，用户点完按钮立刻能继续操作，
        PS 完成后 after() 把结果投回主线程做列表更新。
        """
        def worker():
            result = _ps(script)
            try:
                self.root.after(0, on_done, *result)
            except tk.TclError:
                pass  # 主窗口已销毁

        threading.Thread(target=worker, daemon=True).start()

    def _compute_next_run(self, time_str: str) -> str:
        now = datetime.now()
        hh, mm = map(int, time_str.split(":"))
        next_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if next_dt <= now:
            next_dt += timedelta(days=1)
        return next_dt.strftime("%Y-%m-%d %H:%M:%S")

    def _insert_task_row_sorted(self, task_name: str, time_str: str) -> None:
        """按 time 升序插入新行，新建任务一律显示"从未运行过"。"""
        next_run = self._compute_next_run(time_str)
        insert_index: int | str = "end"
        for i, iid in enumerate(self.sched_tree.get_children()):
            if time_str < self.sched_tree.item(iid, "values")[0]:
                insert_index = i
                break
        self.sched_tree.insert(
            "", insert_index, iid=task_name,
            values=(time_str, _STATE_MAP[3], next_run, "从未运行过"),
        )
        self.sched_tree.selection_set(task_name)

    # ─── 状态 ───
    def refresh_status(self):
        tasks = query_all_schedules()
        tasks.sort(key=lambda t: t.get("time") or "99:99")  # earliest first; no-time tasks last
        self.sched_tree.delete(*self.sched_tree.get_children())
        for t in tasks:
            state_text = _STATE_MAP.get(t.get("state", 0), "未知")
            next_run   = t.get("nextRun") or "—"
            rc_code    = t.get("lastResult", 0)
            # 0x41303 = SCHED_S_TASK_HAS_NOT_RUN：刚注册、从未触发过的任务，
            # 不是错误，直接显示"从未运行过"，避免误报红色错误码。
            if not t.get("lastRun") or rc_code == 0x41303:
                last_run = "从未运行过"
            elif rc_code != 0:
                last_run = f"{t['lastRun']} (失败 0x{rc_code:X})"
            else:
                last_run = t["lastRun"]
            self.sched_tree.insert("", "end", iid=t["taskName"],
                                   values=(t.get("time", "--:--"), state_text, next_run, last_run))

    # ─── 立即运行 ───
    def run_once(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("提示", "当前已有下载在运行。")
            return
        cmd = [PYTHON, "-u", "-m", "canvas_dl"]
        if self.dry_run_var.get():
            cmd.append("--dry-run")
        env = {**os.environ, "CANVAS_DL_GUI_MODE": "1", "PYTHONIOENCODING": "utf-8"}
        # #17: disable button BEFORE Popen so a rapid second click cannot slip through
        self.run_btn.configure(state="disabled")
        self._reset_progress()
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(WORK_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as e:
            self._log(f"[启动失败] {e}\n")
            self.run_btn.configure(state="normal")  # restore on launch failure
            return
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen):
        rc = -1
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("@@PROGRESS@@\t"):
                    self.root.after(0, self._handle_progress, line.rstrip("\r\n"))
                else:
                    self.root.after(0, self._log, line)
        except Exception as e:
            try:
                self.root.after(0, self._log, f"[读取错误] {e}\n")
            except Exception:
                pass
        finally:
            rc = proc.wait()
        try:
            self.root.after(0, self._on_proc_end, rc)
        except Exception:
            pass

    def _on_proc_end(self, rc: int):
        if rc != 0:
            self._log(f"\n[退出码 {rc}]\n")
        self.run_btn.configure(state="normal")
        self.refresh_status()
        # 运行过程中 sync_with_canvas 可能已改写 courses.json（新课加入、失效课标 inactive）
        self.load_courses()

    def _log(self, msg: str):
        self.log.insert("end", msg)
        self.log.see("end")

    # ─── 进度事件处理 ───
    def _reset_progress(self):
        self.course_prog.configure(maximum=1, value=0)
        self.file_prog.configure(maximum=1, value=0)
        self.course_prog_label.configure(text="总进度  0 / 0 门")
        self.file_prog_label.configure(text="当前课  0 / 0 文件")
        self.file_postfix_label.configure(text="")
        self._current_course_name = ""
        self._course_prog_value = 0
        self._file_prog_value = 0

    def _handle_progress(self, line: str):
        parts = line.split("\t")
        if len(parts) < 3:
            return
        # parts[0] == '@@PROGRESS@@'
        kind, event = parts[1], parts[2]
        rest = parts[3:]
        if kind == "course":
            if event == "start" and rest:
                total = max(int(rest[0]), 1)
                self._course_prog_value = 0
                self.course_prog.configure(maximum=total, value=0)
                self.course_prog_label.configure(text=f"总进度  0 / {rest[0]} 门")
            elif event == "tick" and rest:
                self._course_prog_value += int(rest[0])
                total = int(self.course_prog["maximum"])
                self.course_prog.configure(value=self._course_prog_value)
                self.course_prog_label.configure(text=f"总进度  {self._course_prog_value} / {total} 门")
            # course/end: 保持末态，不清零
        elif kind == "file":
            if event == "start" and len(rest) >= 2:
                course_name, total_s = rest[0], rest[1]
                total = max(int(total_s), 1)
                self._current_course_name = course_name
                self._file_prog_value = 0
                self.file_prog.configure(maximum=total, value=0)
                self.file_prog_label.configure(text=f"当前课  0 / {total_s} 文件 — {course_name}")
                self.file_postfix_label.configure(text="")
            elif event == "tick" and rest:
                self._file_prog_value += int(rest[0])
                total = int(self.file_prog["maximum"])
                self.file_prog.configure(value=self._file_prog_value)
                self.file_prog_label.configure(
                    text=f"当前课  {self._file_prog_value} / {total} 文件 — {self._current_course_name}"
                )
            elif event == "postfix" and rest:
                self.file_postfix_label.configure(text=rest[0])
            elif event == "end":
                self.file_postfix_label.configure(text="")

    # ─── 定时任务 ───
    def _validate_time(self) -> str | None:
        hh, mm = self.hh_var.get().strip(), self.mm_var.get().strip()
        if not (hh.isdigit() and mm.isdigit()):
            messagebox.showerror("错误", "时分必须为数字。")
            return None
        h, m = int(hh), int(mm)
        if not (0 <= h < 24 and 0 <= m < 60):
            messagebox.showerror("错误", "时间范围：时 0-23、分 0-59。")
            return None
        return f"{h:02d}:{m:02d}"

    def add_schedule(self):
        t = self._validate_time()
        if not t:
            return
        # #24: 去重从 PS 查询换成本地 Treeview 迭代，省掉一次冷启动 PS。
        existing_times = {
            self.sched_tree.item(iid, "values")[0]
            for iid in self.sched_tree.get_children()
        }
        if t in existing_times:
            messagebox.showwarning("已存在", f"已存在 {t} 的定时任务。")
            return

        task_name, script = _register_script(t)

        def on_done(rc: int, out: str, err: str):
            if rc == 0:
                self._insert_task_row_sorted(task_name, t)
            else:
                messagebox.showerror("失败", f"注册失败：{err or out}")

        self._ps_async(script, on_done)

    def modify_schedule(self):
        sel = self.sched_tree.selection()
        if not sel:
            messagebox.showwarning("未选择", "请先在列表中选择要修改的任务。")
            return
        t = self._validate_time()
        if not t:
            return
        old_name = sel[0]
        if old_name == _task_name(t):
            messagebox.showinfo("提示", "时间未变，无需修改。")
            return
        other_times = {
            self.sched_tree.item(iid, "values")[0]
            for iid in self.sched_tree.get_children()
            if iid != old_name
        }
        if t in other_times:
            messagebox.showwarning("已存在", f"已存在 {t} 的定时任务，无法修改。")
            return

        new_name = _task_name(t)
        script = modify_script(old_name, t)

        def on_done(rc: int, out: str, err: str):
            if rc == 0:
                try:
                    self.sched_tree.delete(old_name)
                except tk.TclError:
                    pass
                self._insert_task_row_sorted(new_name, t)
            else:
                messagebox.showerror("失败", f"修改失败：{err or out}")

        self._ps_async(script, on_done)

    def remove_schedule(self):
        sel = self.sched_tree.selection()
        if not sel:
            messagebox.showwarning("未选择", "请先在列表中选择要删除的任务。")
            return
        task_name = sel[0]
        time_display = self.sched_tree.item(task_name, "values")[0]
        if not messagebox.askyesno("确认删除", f"删除每天 {time_display} 的定时任务？"):
            return

        script = delete_script(task_name)

        def on_done(rc: int, out: str, err: str):
            if rc == 0:
                try:
                    self.sched_tree.delete(task_name)
                except tk.TclError:
                    pass
            else:
                messagebox.showerror("失败", f"删除失败：{err or out}")

        self._ps_async(script, on_done)

    def _on_task_select(self, _event=None):
        sel = self.sched_tree.selection()
        if not sel:
            return
        time_val = self.sched_tree.item(sel[0], "values")[0]
        # #31: old-format tasks show "--:--"; skip spinbox update for non-numeric times.
        if time_val and ":" in time_val and not time_val.startswith("-"):
            hh, mm = time_val.split(":", 1)
            self.hh_var.set(hh.zfill(2))
            self.mm_var.set(mm.zfill(2))

    # ─── 课程勾选 ───
    def load_courses(self):
        try:
            self._courses_json_mtime = COURSES_JSON.stat().st_mtime if COURSES_JSON.exists() else None
        except OSError:
            self._courses_json_mtime = None
        self._clear_course_widgets()
        if not COURSES_JSON.exists():
            ttk.Label(self.courses_inner, text="courses.json 尚未生成。请先点「立即运行」。",
                      foreground="#888").pack(anchor="w", padx=6, pady=6)
            self.courses_data = None
            return
        try:
            self.courses_data = cc.load_or_init(COURSES_JSON)
        except json.JSONDecodeError as e:
            ttk.Label(self.courses_inner, text=f"解析 courses.json 失败：{e}",
                      foreground="red").pack(anchor="w", padx=6, pady=6)
            self.courses_data = None
            # #15: prevent running when config is known-invalid (CLI would sys.exit(1))
            self.run_btn.configure(state="disabled")
            return
        # 运行期间 _poll_courses_json 会因子进程写回 courses.json 而触发本函数；
        # 此时不得把按钮重新置为 normal，否则用户会看到"可点击但点了弹提示"的误导状态。
        if not (self.proc and self.proc.poll() is None):
            self.run_btn.configure(state="normal")
        self._render_courses()

    def _clear_course_widgets(self):
        for w in self.courses_inner.winfo_children():
            w.destroy()
        self.course_vars = []

    def _render_courses(self):
        courses = self.courses_data.get("courses", []) if self.courses_data else []
        actives = [c for c in courses if c.get("active", True)]
        inactives = [c for c in courses if not c.get("active", True)]
        for entry in actives + inactives:
            var = tk.BooleanVar(value=bool(entry.get("enabled", True)))
            name = entry.get("name", f"course_{entry.get('id')}")
            label = f"{name}    [{entry.get('id')}]"
            is_inactive = not entry.get("active", True)
            if is_inactive:
                # #16: show as inactive but still checkable so users can manually re-activate
                label += "  （Canvas 上已不可见，勾选可强制重新激活）"
            cb = ttk.Checkbutton(self.courses_inner, text=label, variable=var)
            cb.pack(anchor="w", padx=6, pady=1)
            self.course_vars.append((var, entry))
            var.trace_add("write", lambda *_: self.save_courses())

    def save_courses(self):
        if not self.courses_data:
            return
        # #26: collect changes first so we can roll back if disk write fails
        pending: list[tuple] = []
        for var, entry in self.course_vars:
            new_enabled = bool(var.get())
            old_enabled = entry.get("enabled", True)
            old_active = entry.get("active", True)
            new_active = True if (new_enabled and not old_active) else old_active
            pending.append((entry, old_enabled, new_enabled, old_active, new_active))

        for entry, _, new_enabled, _, new_active in pending:
            entry["enabled"] = new_enabled
            entry["active"] = new_active

        self._skip_next_self_save_poll = True
        try:
            cc.save(COURSES_JSON, self.courses_data)
            try:
                self._courses_json_mtime = COURSES_JSON.stat().st_mtime
            except OSError:
                pass
        except OSError as e:
            # Roll back in-memory changes so UI stays consistent with disk
            for entry, old_enabled, _, old_active, _ in pending:
                entry["enabled"] = old_enabled
                entry["active"] = old_active
            # 写盘失败 → 没有新 mtime 产生，撤销旗标避免抑制后续正常的外部改动
            self._skip_next_self_save_poll = False
            messagebox.showerror("失败", f"保存失败：{e}")

    # ─── 下载路径 ───
    def _load_download_dir(self) -> str:
        env_path = WORK_DIR / ".env"
        if env_path.exists():
            return dotenv_values(str(env_path)).get("CANVAS_DOWNLOAD_DIR", "")
        return ""

    def _browse_download_dir(self):
        current = self._dl_dir_var.get()
        initial = current if current and Path(current).exists() else str(Path.home())
        new_dir = filedialog.askdirectory(title="选择下载路径", initialdir=initial)
        if not new_dir:
            return
        new_dir = str(Path(new_dir))
        env_path = WORK_DIR / ".env"
        try:
            _dotenv_set_key(str(env_path), "CANVAS_DOWNLOAD_DIR", new_dir)
            self._dl_dir_var.set(new_dir)
            self._log(f"[配置] 下载路径已更新：{new_dir}\n")
        except OSError as e:
            messagebox.showerror("失败", f"保存失败：{e}")

    # ─── 课程文件轮询 ───
    def _poll_courses_json(self):
        try:
            mtime = COURSES_JSON.stat().st_mtime if COURSES_JSON.exists() else None
        except OSError:
            mtime = None
        if mtime != self._courses_json_mtime:
            if self._skip_next_self_save_poll:
                # 自己的 save_courses 触发的变化——只吃掉本次 mtime 差，不重绘，
                # 避免勾选时列表闪烁。
                self._courses_json_mtime = mtime
                self._skip_next_self_save_poll = False
            else:
                self.load_courses()
        self.root.after(2000, self._poll_courses_json)

    # ─── 退出 ───
    def _on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("确认退出", "下载仍在进行，终止并退出吗？"):
                return
            try:
                self.proc.terminate()
            except OSError:
                pass
        self.root.destroy()

    # ─── 鼠标滚轮 ───
    def _on_mousewheel(self, event):
        cw = self.courses_canvas
        x1, y1 = cw.winfo_rootx(), cw.winfo_rooty()
        x2, y2 = x1 + cw.winfo_width(), y1 + cw.winfo_height()
        if x1 <= event.x_root < x2 and y1 <= event.y_root < y2:
            cw.yview_scroll(int(-event.delta / 120), "units")


def main():
    # Enable per-monitor DPI awareness so Tkinter draws crisp on HiDPI screens.
    # Must be called BEFORE Tk() is created.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2, Win10 1703+
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # fallback for older Windows
        except (AttributeError, OSError):
            pass

    root = tk.Tk()

    # Tell Tk about the true DPI so point-sized fonts render at the right physical size.
    dpi = root.winfo_fpixels("1i")          # actual pixels per inch reported by OS
    root.tk.call("tk", "scaling", dpi / 72.0)

    try:
        for fn in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(fn).configure(family="Microsoft YaHei UI", size=10)
    except tk.TclError:
        pass

    try:
        _icon_path = Path(__file__).resolve().parent / "icon.png"
        if _icon_path.exists():
            _src = tk.PhotoImage(file=str(_icon_path))
            _icon32 = _src.subsample(18, 18)   # 586x593 → 32x32
            _icon16 = _src.subsample(36, 36)   # 586x593 → 16x16
            root.iconphoto(True, _icon32, _icon16)
    except Exception:
        pass

    CanvasDownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
