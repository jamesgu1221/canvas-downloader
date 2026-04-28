from __future__ import annotations

import threading

from tqdm import tqdm

from .events import (
    CourseProgressStarted,
    CourseProgressTick,
    FilePostfix,
    FileProgressEnded,
    FileProgressStarted,
    FileProgressTick,
    LogEvent,
    Reporter,
    RunFinished,
    RunStarted,
    SyncEvent,
)


class TqdmReporter(Reporter):
    def __init__(self) -> None:
        self._write_lock = threading.Lock()
        self._course_bar = None
        self._file_bar = None

    def emit(self, event: SyncEvent) -> None:
        if isinstance(event, RunStarted):
            self._write(f"Canvas: {event.canvas_url}")
            self._write(f"下载到: {event.download_dir}")
            if event.dry_run:
                self._write("[DRY RUN 模式 — 不实际下载]\n")
            return
        if isinstance(event, LogEvent):
            self._write(event.message)
            return
        if isinstance(event, CourseProgressStarted):
            self._course_bar = tqdm(total=event.total, unit="门课", desc="总进度", position=0)
            return
        if isinstance(event, CourseProgressTick):
            if self._course_bar is not None:
                self._course_bar.update(event.step)
            return
        if isinstance(event, FileProgressStarted):
            label = event.course_name[:28] if len(event.course_name) > 28 else event.course_name
            self._file_bar = tqdm(
                total=event.total,
                unit="个文件",
                desc=f"  {label}",
                position=1,
                leave=False,
            )
            return
        if isinstance(event, FilePostfix):
            if self._file_bar is not None:
                self._file_bar.set_postfix_str(event.text)
            return
        if isinstance(event, FileProgressTick):
            if self._file_bar is not None:
                self._file_bar.update(event.step)
            return
        if isinstance(event, FileProgressEnded):
            if self._file_bar is not None:
                self._file_bar.close()
                self._file_bar = None
            return
        if isinstance(event, RunFinished):
            self.close()
            if event.cancelled:
                self._write(f"\n已取消，已下载 {event.downloaded} 个文件")
            else:
                self._write(f"\n完成！本次共下载 {event.downloaded} 个文件")

    def _write(self, message: str) -> None:
        with self._write_lock:
            tqdm.write(message)

    def close(self) -> None:
        if self._file_bar is not None:
            self._file_bar.close()
            self._file_bar = None
        if self._course_bar is not None:
            self._course_bar.close()
            self._course_bar = None
