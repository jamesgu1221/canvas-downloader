import contextlib
import json
import os
import time
from pathlib import Path


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
    @staticmethod
    def _lock_file(fh) -> None:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_file(fh) -> None:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _acquire_lock(self, wait_seconds: float = 2.0) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + wait_seconds
        while True:
            fh = open(self._lock_path, "a+", encoding="utf-8")
            try:
                self._lock_file(fh)
                self._lock_fh = fh
                self._lock_fh.seek(0)
                self._lock_fh.truncate()
                self._lock_fh.write(str(os.getpid()))
                self._lock_fh.flush()
                return
            except OSError:
                fh.close()
                if time.time() >= deadline:
                    raise LockHeldError(
                        f"另一 canvas_dl 进程正在运行（锁文件：{self._lock_path}）。"
                    )
                time.sleep(0.1)

    def _release_lock(self) -> None:
        if self._lock_fh is not None:
            fh = self._lock_fh
            self._lock_fh = None
            try:
                with contextlib.suppress(OSError):
                    self._lock_path.unlink()
                with contextlib.suppress(OSError):
                    self._unlock_file(fh)
            finally:
                with contextlib.suppress(OSError):
                    fh.close()

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

    def is_video_current(self, key: str, asset, local_path: Path) -> bool:
        if key not in self._data:
            return False
        entry = self._data[key]
        if not local_path.exists():
            return False
        size = getattr(asset, "size", None)
        modified_at = getattr(asset, "modified_at", None)
        url_hash = self._video_url_hash(getattr(asset, "url", ""))
        if size is not None and entry.get("size") != size:
            return False
        if modified_at is not None and entry.get("modified_at") != self._fmt_time(modified_at):
            return False
        if url_hash and entry.get("url_hash") != url_hash:
            return False
        return True

    def record_video(self, key: str, asset) -> None:
        self._data[key] = {
            "size": getattr(asset, "size", None),
            "modified_at": self._fmt_time(getattr(asset, "modified_at", None)),
            "display_name": getattr(asset, "title", ""),
            "kind": getattr(asset, "kind", ""),
            "url_hash": self._video_url_hash(getattr(asset, "url", "")),
        }

    @staticmethod
    def _video_url_hash(url: str) -> str:
        if not url:
            return ""
        import hashlib

        return hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()

    def save(self, release_lock: bool = True) -> None:
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
            if release_lock:
                self._release_lock()

    def close(self) -> None:
        """Release the process lock without writing — use in dry-run mode."""
        self._release_lock()
