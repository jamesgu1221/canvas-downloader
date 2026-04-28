from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

import requests.exceptions
from canvasapi.exceptions import ResourceDoesNotExist

from . import courses_config as cc
from .client import CanvasClient, safe_course_name
from .config import AppConfig
from .events import (
    CourseFinished,
    CourseProgressStarted,
    CourseProgressTick,
    CoursesSynced,
    FilePostfix,
    FileProgressEnded,
    FileProgressStarted,
    FileProgressTick,
    LogEvent,
    NullReporter,
    Reporter,
    RunFinished,
    RunStarted,
)
from .paths import AppPaths, get_app_paths
from .state import LockHeldError, SyncState
from .traversal import get_course_root_folder, sanitize_name, walk_folder


class SyncError(RuntimeError):
    pass


@dataclass
class RunOptions:
    dry_run: bool = False
    only_courses: list[int] = field(default_factory=list)
    skip_courses: list[int] = field(default_factory=list)
    download_dir: Path | None = None


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def get_course_name(course) -> str:
    name = safe_course_name(course) or f"course_{course.id}"
    return sanitize_name(name)


def filter_courses(courses, only_courses: list[int], skip_courses: list[int]):
    if only_courses:
        courses = [c for c in courses if c.id in only_courses]
    if skip_courses:
        courses = [c for c in courses if c.id not in skip_courses]
    return courses


class SyncService:
    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths | None = None,
        client: CanvasClient | None = None,
    ) -> None:
        self.config = config
        self.paths = paths or get_app_paths()
        self.client = client or CanvasClient(config)

    def run(
        self,
        options: RunOptions | None = None,
        reporter: Reporter | None = None,
        cancel_token: CancelToken | None = None,
    ) -> int:
        options = options or RunOptions()
        reporter = reporter or NullReporter()
        cancel_token = cancel_token or CancelToken()

        if options.download_dir is not None:
            self.config.download_dir = options.download_dir
        self.config.dry_run = options.dry_run
        self.config.only_courses = list(options.only_courses)
        self.config.skip_courses = list(options.skip_courses)

        state = SyncState(self.paths.state_file)
        try:
            try:
                state.load()
            except LockHeldError as e:
                reporter.emit(LogEvent(f"跳过本次运行：{e}"))
                return 0

            reporter.emit(
                RunStarted(
                    canvas_url=self.config.canvas_url,
                    download_dir=self.config.download_dir,
                    dry_run=self.config.dry_run,
                )
            )

            courses = self.client.get_courses()
            courses = self._sync_courses(courses, reporter)
            courses = filter_courses(courses, self.config.only_courses, self.config.skip_courses)

            if not courses:
                reporter.emit(LogEvent("未找到任何课程。"))
                return 0

            reporter.emit(LogEvent(f"共 {len(courses)} 门课程"))
            reporter.emit(CourseProgressStarted(len(courses)))

            downloaded = 0
            for course in courses:
                if cancel_token.is_cancelled():
                    break
                downloaded += self._process_course(course, state, reporter, cancel_token)

            if self.config.dry_run:
                state.close()
            else:
                state.save()

            cancelled = cancel_token.is_cancelled()
            reporter.emit(RunFinished(downloaded=downloaded, cancelled=cancelled))
            return downloaded
        except KeyboardInterrupt:
            if self.config.dry_run:
                state.close()
            else:
                state.save()
            raise
        finally:
            state.close()

    def _sync_courses(self, courses: list, reporter: Reporter) -> list:
        try:
            data = cc.load_or_init(self.paths.courses_file)
        except json.JSONDecodeError as e:
            raise SyncError(f"courses.json 解析失败：{e}") from e

        enabled_ids, added, inactive = cc.sync_with_canvas(data, courses)
        cc.save(self.paths.courses_file, data)

        total_from_canvas = len(courses)
        enabled_set = set(enabled_ids)
        enabled_courses = [c for c in courses if c.id in enabled_set]
        disabled_count = total_from_canvas - len(enabled_courses)

        reporter.emit(
            CoursesSynced(
                enabled_count=len(enabled_courses),
                disabled_count=disabled_count,
                added=list(added),
                inactive=list(inactive),
            )
        )
        if added:
            reporter.emit(LogEvent(f"检测到新课程（已加入清单并默认启用）：{', '.join(added)}"))
        if inactive:
            reporter.emit(LogEvent(f"以下课程在 Canvas 上已不可见，已标记为 inactive：{', '.join(inactive)}"))
        reporter.emit(LogEvent(f"已启用 {len(enabled_courses)} 门，已禁用 {disabled_count} 门"))
        return enabled_courses

    def _process_course(
        self,
        course,
        state: SyncState,
        reporter: Reporter,
        cancel_token: CancelToken,
    ) -> int:
        course_name = get_course_name(course)
        course_dir = self.config.download_dir / course_name

        try:
            try:
                root = get_course_root_folder(self.client, course)
            except (ResourceDoesNotExist, requests.exceptions.RequestException) as e:
                reporter.emit(LogEvent(f"  [{course_name}] 获取文件夹失败（可能是权限不足）：{e}"))
                reporter.emit(CourseProgressTick())
                return 0

            if root is None:
                reporter.emit(LogEvent(f"  [{course_name}] 无文件"))
                reporter.emit(FileProgressStarted(course_name, 0))
                reporter.emit(FileProgressEnded())
                reporter.emit(CourseProgressTick())
                return 0

            try:
                all_files = list(walk_folder(self.client, root, course_dir))
            except (ResourceDoesNotExist, requests.exceptions.RequestException) as e:
                reporter.emit(LogEvent(f"  [{course_name}] 遍历文件夹失败：{e}"))
                reporter.emit(CourseProgressTick())
                return 0

            if not all_files:
                reporter.emit(LogEvent(f"  [{course_name}] 无文件"))
                reporter.emit(FileProgressStarted(course_name, 0))
                reporter.emit(FileProgressEnded())
                reporter.emit(CourseProgressTick())
                return 0

            downloaded = skipped = failed = 0
            reporter.emit(FileProgressStarted(course_name, len(all_files)))
            try:
                for canvas_file, local_path in all_files:
                    if cancel_token.is_cancelled():
                        break
                    display_name = canvas_file.display_name or f"file_{canvas_file.id}"
                    reporter.emit(FilePostfix(display_name[:30]))

                    if state.is_current(canvas_file, local_path):
                        skipped += 1
                        reporter.emit(FileProgressTick())
                        continue

                    url = getattr(canvas_file, "url", "") or ""
                    if not url or not url.startswith(("http://", "https://")):
                        locked = getattr(canvas_file, "locked_for_user", False)
                        reason = "被锁定" if locked else "无下载 URL"
                        reporter.emit(LogEvent(f"  [跳过] {display_name}: {reason}"))
                        skipped += 1
                        reporter.emit(FileProgressTick())
                        continue

                    if self.config.dry_run:
                        reporter.emit(LogEvent(f"  [DRY] {local_path}"))
                        reporter.emit(FileProgressTick())
                        continue

                    try:
                        self.client.download_file(url, local_path)
                        state.record(canvas_file)
                        downloaded += 1
                    except OSError as e:
                        if "No space left" in str(e) or "磁盘空间不足" in str(e):
                            reporter.emit(LogEvent(f"\n磁盘空间不足，已终止。最后尝试的文件：{local_path}"))
                            raise SystemExit(1)
                        reporter.emit(LogEvent(f"  [错误] {display_name}: {e}"))
                        failed += 1
                    except (RuntimeError, requests.exceptions.RequestException) as e:
                        reporter.emit(LogEvent(f"  [错误] {display_name}: {e}"))
                        failed += 1

                    reporter.emit(FileProgressTick())
            finally:
                reporter.emit(FileProgressEnded())

            reporter.emit(CourseFinished(course_name, downloaded, skipped, failed))
            summary = f"  [{course_name}] 下载 {downloaded}，跳过 {skipped}"
            if failed:
                summary += f"，失败 {failed}"
            reporter.emit(LogEvent(summary))
            reporter.emit(CourseProgressTick())
            return downloaded
        finally:
            if not self.config.dry_run:
                state.save(release_lock=False)
