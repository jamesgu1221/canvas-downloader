"""Windows Task Scheduler（schtasks / PowerShell）集成。

只暴露「同步」API；GUI 线程的非阻塞调用应各自用 QThread / threading 包一层
（见 `gui_qt/pages/schedule.py` 的 `_PSBridge`），避免冻结窗口。
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from ..paths import runtime_root

TASK_PREFIX = "Canvas课件下载"


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _resolve_runner() -> tuple[Path, str]:
    """Return the executable and arguments used by the scheduled task.

    Source runs use pythonw.exe + `-m canvas_dl`. Frozen GUI builds reuse the
    current exe with a private CLI switch handled by `canvas_dl.gui_qt.__main__`.
    That keeps scheduled sync working even when there is no Python installation
    on the target machine.

    源码运行时优先用 pythonw.exe（无黑窗），不存在时回退到 python.exe。
    embedded / 精简 Python 发行版可能不带 pythonw.exe；这种情况下若仍写
    pythonw.exe 路径到任务里，触发执行时 Task Scheduler 找不到 EXE，
    LastTaskResult 返回非零错误码，但用户在 GUI 里只看到神秘的 0x... —
    因此注册前在这里完成回退。
    """
    if _is_frozen():
        return Path(sys.executable).resolve(), "--canvas-dl-cli"

    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    if pythonw.exists():
        return pythonw, "-m canvas_dl"
    return python, "-m canvas_dl"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

STATE_MAP: dict[int, str] = {
    0: "未知",
    1: "已禁用",
    2: "排队中",
    3: "就绪",
    4: "运行中",
}


def task_name(time_str: str) -> str:
    """Task Scheduler 不允许任务名含冒号，把 `HH:MM` 转为 `HH-MM`。"""
    return f"{TASK_PREFIX} — {time_str.replace(':', '-')}"


def run_ps(script: str) -> tuple[int, str, str]:
    """用 -EncodedCommand 传 PS 脚本，规避命令行引号/中文转义坑。"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    # "从未运行"的判定统一交给 Python 侧的 lastResult == 0x41303 —— Windows 对
    # 没跑过的任务返回 1899-11-30 或 1999-11-30 的 sentinel，Year 比较没有
    # 可靠阈值。这里只要是非 null 的 DateTime 都原样传出。
    if ($i -and $i.LastRunTime) {{ $lastRun = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') }}
    if ($i -and $i.NextRunTime) {{ $nextRun = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss') }}
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
    rc, out, _err = run_ps(_QUERY_ALL_SCRIPT)
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict):  # PS 5.1 单条目安全兜底
            data = [data]
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and isinstance(d.get("taskName"), str)]
    except json.JSONDecodeError:
        return []


def _ps_escape(s: str) -> str:
    """在 PowerShell 单引号字符串中转义单引号（重复一次）。"""
    return s.replace("'", "''")


def register_script(time_str: str) -> tuple[str, str]:
    """返回 (task_name, ps_script)，用于注册每天 `time_str` 的 daily 任务。"""
    tn = task_name(time_str)
    runner_path, runner_args = _resolve_runner()
    runner = _ps_escape(str(runner_path))
    arguments = _ps_escape(runner_args)
    work_dir = _ps_escape(str(runtime_root()))
    tn_escaped = _ps_escape(tn)
    script = fr"""
$act = New-ScheduledTaskAction -Execute '{runner}' -Argument '{arguments}' -WorkingDirectory '{work_dir}'
$trg = New-ScheduledTaskTrigger -Daily -At '{time_str}'
$set = New-ScheduledTaskSettingsSet {_TASK_SETTINGS_FRAGMENT}
$prin = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{tn_escaped}' -Action $act -Trigger $trg -Settings $set -Principal $prin -Force | Out-Null
"""
    return tn, script


def modify_script(old_task_name: str, new_time: str) -> str:
    """原子合并：一次 PS 调用里先 Unregister 再 Register，省掉一次冷启动。"""
    old_tn = _ps_escape(old_task_name)
    _, reg = register_script(new_time)
    return fr"""
$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName '{old_tn}' -Confirm:$false
{reg}
"""


def delete_script(task_name: str) -> str:
    tn = _ps_escape(task_name)
    return f"Unregister-ScheduledTask -TaskName '{tn}' -Confirm:$false"


def compute_next_run(time_str: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    hh, mm = map(int, time_str.split(":"))
    next_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if next_dt <= now:
        next_dt += timedelta(days=1)
    return next_dt.strftime("%Y-%m-%d %H:%M:%S")


def format_last_run(entry: dict) -> str:
    """把 PS 查询出的条目转成「上次运行」列的显示文字。

    0x41303 = SCHED_S_TASK_HAS_NOT_RUN：刚注册、从未触发过的任务，
    不是错误，直接显示「从未运行过」避免误报红色错误码。
    """
    rc_code = entry.get("lastResult", 0) or 0
    last_run = entry.get("lastRun") or ""
    if not last_run or rc_code == 0x41303:
        return "从未运行过"
    if rc_code != 0:
        return f"{last_run} (失败 0x{rc_code:X})"
    return last_run
