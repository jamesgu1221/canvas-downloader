from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from canvas_dl.events import (
    CourseProgressTick,
    FileProgressEnded,
    FileProgressStarted,
    LogEvent,
)
from canvas_dl.paths import AppPaths
from canvas_dl.service import DiskFullError, SyncService
from canvas_dl.stores import SettingsStore


class _Reporter:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class _State:
    def is_current(self, canvas_file, local_path: Path) -> bool:
        return False

    def record(self, canvas_file) -> None:
        pass

    def save(self, release_lock: bool = True) -> None:
        pass


class _Client:
    def download_file(self, url: str, dest: Path) -> None:
        raise OSError("No space left on device")


def test_settings_store_preserves_zero_request_delay(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    paths.settings_file.write_text(
        json.dumps({"request_delay": 0}),
        encoding="utf-8",
    )

    settings = SettingsStore(paths).load()

    assert settings.request_delay == 0


def test_disk_full_raises_sync_error_without_extra_log(monkeypatch, tmp_path: Path) -> None:
    config = SimpleNamespace(download_dir=tmp_path, dry_run=False)
    service = SyncService(config, AppPaths(tmp_path), _Client())
    reporter = _Reporter()
    course = SimpleNamespace(id=1, name="Course")
    canvas_file = SimpleNamespace(id=10, display_name="file.pdf", url="https://example.test/file.pdf")

    monkeypatch.setattr("canvas_dl.service.get_course_root_folder", lambda client, course: object())
    monkeypatch.setattr(
        "canvas_dl.service.walk_folder",
        lambda client, root, course_dir: [(canvas_file, course_dir / "file.pdf")],
    )

    with pytest.raises(DiskFullError, match="磁盘空间不足"):
        service._process_course(course, _State(), reporter, SimpleNamespace(is_cancelled=lambda: False))

    assert not any(isinstance(event, LogEvent) and "未处理错误" in event.message for event in reporter.events)
    assert any(isinstance(event, FileProgressEnded) for event in reporter.events)


def test_folder_walk_failure_clears_file_progress(monkeypatch, tmp_path: Path) -> None:
    config = SimpleNamespace(download_dir=tmp_path, dry_run=False)
    service = SyncService(config, AppPaths(tmp_path), object())
    reporter = _Reporter()
    course = SimpleNamespace(id=1, name="Course")

    monkeypatch.setattr("canvas_dl.service.get_course_root_folder", lambda client, course: object())

    def _walk_folder(client, root, course_dir):
        raise requests.exceptions.RequestException("boom")

    monkeypatch.setattr("canvas_dl.service.walk_folder", _walk_folder)

    result = service._process_course(
        course,
        _State(),
        reporter,
        SimpleNamespace(is_cancelled=lambda: False),
    )

    assert result == 0
    assert any(isinstance(event, FileProgressStarted) and event.total == 0 for event in reporter.events)
    assert any(isinstance(event, FileProgressEnded) for event in reporter.events)
    assert any(isinstance(event, CourseProgressTick) for event in reporter.events)
