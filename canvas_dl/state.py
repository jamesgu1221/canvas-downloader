import contextlib
import json
import os
import time
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    """Best-effort check whether a PID still belongs to a running process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(h, ctypes.byref(code)) == 0:
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class LockHeldError(RuntimeError):
    """锁已被其他 canvas_dl 进程持有，用于让上层做"跳过本次运行"的决策。"""


class SyncState:
    def __init__(self, state_file: Path):
        self._path = state_file
        self._data: dict = {}
        self._lock_path = state_file.with_suffix(".lock")
        self._lock_fh = None

    # ── #11 / #32: process-level file lock ───────────────────────────────
    # #32: 不再基于 mtime 的 60s 启发式抢锁——真实下载常超过 60s，抢锁会
    # 导致两进程并发写 sync_state.json，正是 #11 要防止的场景。改为：
    # 检测到锁被占用，直接抛 LockHeldError 让调用方决定（CLI 入口会退出，
    # 等于"当前有任务在运行时禁止定时任务运行"）。
    def _acquire_lock(self, wait_seconds: float = 2.0) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + wait_seconds
        stale_checked = False
        while True:
            try:
                self._lock_fh = open(self._lock_path, "x")  # O_CREAT|O_EXCL, atomic
                self._lock_fh.write(str(os.getpid()))
                self._lock_fh.flush()
                return
            except FileExistsError:
                # 首次失败时读锁里的 PID，若该进程已不存在则视为孤儿锁，清理重试。
                # 只尝试一次，避免和另一个活进程的 acquire 循环交错互删。
                if not stale_checked:
                    stale_checked = True
                    if self._try_clear_stale_lock():
                        continue
                if time.time() >= deadline:
                    raise LockHeldError(
                        f"另一 canvas_dl 进程正在运行（锁文件：{self._lock_path}）。"
                        f"如果确认无活动进程，可手动删除该文件后重试。"
                    )
                time.sleep(0.1)

    def _try_clear_stale_lock(self) -> bool:
        """读锁文件里的 PID，若对应进程已死，则删除锁文件。返回是否删除成功。"""
        try:
            with open(self._lock_path, encoding="utf-8") as f:
                content = f.read().strip()
        except OSError:
            return False
        # 空内容 / 非整数：来源不明，保守不清理
        try:
            pid = int(content)
        except ValueError:
            return False
        if _pid_alive(pid):
            return False
        with contextlib.suppress(OSError):
            self._lock_path.unlink()
            return True
        return False

    def _release_lock(self) -> None:
        if self._lock_fh is not None:
            try:
                self._lock_fh.close()
            except OSError:
                pass
            self._lock_fh = None
            with contextlib.suppress(OSError):
                self._lock_path.unlink()

    @staticmethod
    def _fmt_time(dt) -> str:
        if dt is None:
            return ""
        return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)

    def load(self) -> None:
        self._acquire_lock()
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        # 防御：顶层若被外部改成 list / null / str，`key in self._data`
        # 的语义会跑偏（list 成员判定、或 TypeError），全部当成空状态处理。
        if not isinstance(self._data, dict):
            self._data = {}

    def is_current(self, file, local_path: Path) -> bool:
        key = str(file.id)
        if key not in self._data:
            return False
        entry = self._data[key]
        if not local_path.exists():
            return False
        # #13: skip comparison when Canvas returns None for size/mtime —
        # None vs stored-number would always mismatch and force re-download.
        if file.size is not None and entry.get("size") != file.size:
            return False
        if file.modified_at is not None and entry.get("modified_at") != self._fmt_time(file.modified_at):
            return False
        # Canvas file id is the primary identifier; id + exists + size + mtime is
        # sufficient to prove the on-disk file matches. Omitting a strict absolute-
        # path comparison tolerates the user relocating the download directory
        # (或手动挪动已下载文件夹) without triggering全量重下载。
        return True

    def record(self, file) -> None:
        self._data[str(file.id)] = {
            "size": file.size,
            "modified_at": self._fmt_time(file.modified_at),
            "display_name": file.display_name,
        }

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except Exception:
                # json.dump / os.replace 失败时把孤儿 tmp 文件清掉，避免遗留 .tmp
                with contextlib.suppress(OSError):
                    tmp.unlink()
                raise
        finally:
            self._release_lock()

    def close(self) -> None:
        """Release the process lock without writing — use in dry-run mode."""
        self._release_lock()
