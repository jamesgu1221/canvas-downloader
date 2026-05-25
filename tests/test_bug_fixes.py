from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from canvas_dl import __main__ as cli_main
from canvas_dl.client import CanvasClient
from canvas_dl import cookie_cache
from canvas_dl.events import (
    CourseProgressTick,
    FileProgressEnded,
    FileProgressStarted,
    LogEvent,
)
from canvas_dl.paths import AppPaths
from canvas_dl.service import DiskFullError, SyncService
from canvas_dl.state import LockHeldError, SyncState
from canvas_dl.stores import SettingsStore
from canvas_dl.util import schedule


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


class _DownloadResponse:
    status_code = 200
    url = "https://oc.sjtu.edu.cn/files/1/download"
    headers = {"Content-Type": "application/octet-stream"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        yield b"ok"


def test_settings_store_preserves_zero_request_delay(tmp_path: Path) -> None:
    paths = AppPaths(tmp_path)
    paths.settings_file.write_text(
        json.dumps({"request_delay": 0}),
        encoding="utf-8",
    )

    settings = SettingsStore(paths).load()

    assert settings.request_delay == 0


def test_video_subcommand_preserves_global_args_before_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "canvas_dl",
            "--token",
            "token-value",
            "--dir",
            "D:/Videos",
            "--dry-run",
            "videos",
        ],
    )

    args = cli_main.parse_args()

    assert args.command == "videos"
    assert args.token == "token-value"
    assert args.dir == "D:/Videos"
    assert args.dry_run is True


def test_cookie_cache_save_creates_parent_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cookie_cache, "_dpapi_encrypt", lambda data: b"encrypted:" + data)
    target = tmp_path / "missing" / "video_cookies.dat"

    cookie_cache.save_cookie_cache([{"name": "a", "value": "b"}], target)

    assert target.read_bytes().startswith(b"encrypted:")


def test_sync_state_uses_process_lock_without_stale_unlink_race(tmp_path: Path) -> None:
    state_file = tmp_path / "sync_state.json"
    first = SyncState(state_file)
    second = SyncState(state_file)

    first._acquire_lock(wait_seconds=0)
    try:
        with pytest.raises(LockHeldError):
            second._acquire_lock(wait_seconds=0)
    finally:
        first.close()

    second._acquire_lock(wait_seconds=0)
    second.close()


def test_canvas_download_retries_replace_failure(monkeypatch, tmp_path: Path) -> None:
    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return _DownloadResponse()

    session = _Session()
    client = CanvasClient.__new__(CanvasClient)
    client.config = SimpleNamespace(api_token="token")
    client.session = session
    client._canvas_origin = ("https", "oc.sjtu.edu.cn")
    dest = tmp_path / "file.bin"
    real_replace = os.replace
    replace_calls = {"count": 0}

    def _replace_once_locked(part, target):
        replace_calls["count"] += 1
        if replace_calls["count"] == 1:
            raise PermissionError("locked")
        real_replace(part, target)

    monkeypatch.setattr("canvas_dl.client.os.replace", _replace_once_locked)

    client.download_file("https://oc.sjtu.edu.cn/files/1/download", dest)

    assert dest.read_bytes() == b"ok"
    assert session.calls == 2
    assert replace_calls["count"] == 2


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


def test_startup_schedule_uses_stable_name_and_logon_trigger() -> None:
    task_name, script = schedule.register_startup_script()

    assert task_name == "Canvas课件下载 — 开机登录"
    assert "New-ScheduledTaskTrigger -AtLogOn" in script
    assert "Register-ScheduledTask" in script
    assert "-LogonType Interactive" in script
