import shutil
import uuid
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from canvas_dl.config import AppConfig
from canvas_dl.events import FileProgressStarted, RunFinished
from canvas_dl.paths import AppPaths
from canvas_dl.service import CancelToken, RunOptions, SyncService
from canvas_dl.state import SyncState


@dataclass
class FakeCourse:
    id: int
    name: str


class EmptyClient:
    def __init__(self):
        self.course = FakeCourse(1, "Empty Course")

    def get_courses(self):
        return [self.course]

    def get_course_folders(self, course):
        return []


@dataclass
class FakeFolder:
    id: int
    name: str = "course files"
    full_name: str = "course files"
    parent_folder_id: int | None = None


@dataclass
class FakeFile:
    id: int
    display_name: str
    url: str
    size: int = 1
    modified_at = None


class DiskFullClient:
    def __init__(self):
        self.folder = FakeFolder(1)
        self.files = [
            FakeFile(101, "ok.txt", "https://canvas.example.edu/files/101"),
            FakeFile(102, "full.txt", "https://canvas.example.edu/files/102"),
        ]
        self.downloads = 0

    def get_course_folders(self, course):
        return [self.folder]

    def get_folder_files(self, folder):
        return self.files

    def get_subfolders(self, folder):
        return []

    def download_file(self, url, local_path):
        self.downloads += 1
        if self.downloads == 2:
            raise OSError("No space left on device")
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_text("ok", encoding="utf-8")


class CollectingReporter:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_sync_service_reports_empty_course():
    root = Path(".test_tmp") / f"service_{uuid.uuid4().hex}"
    try:
        paths = AppPaths(base_dir=root / "config", project_root=root / "project")
        config = AppConfig(
            api_token="tok",
            canvas_url="https://canvas.example.edu",
            download_dir=root / "downloads",
        )
        reporter = CollectingReporter()

        downloaded = SyncService(config, paths, EmptyClient()).run(RunOptions(), reporter)

        assert downloaded == 0
        assert any(isinstance(e, FileProgressStarted) and e.total == 0 for e in reporter.events)
        assert any(isinstance(e, RunFinished) and e.downloaded == 0 for e in reporter.events)
        assert paths.courses_file.exists()
        assert paths.state_file.exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_process_course_saves_progress_before_system_exit():
    root = Path(".test_tmp") / f"service_{uuid.uuid4().hex}"
    try:
        paths = AppPaths(base_dir=root / "config", project_root=root / "project")
        config = AppConfig(
            api_token="tok",
            canvas_url="https://canvas.example.edu",
            download_dir=root / "downloads",
        )
        service = SyncService(config, paths, DiskFullClient())
        state = SyncState(paths.state_file)
        state.load()

        with pytest.raises(SystemExit):
            service._process_course(FakeCourse(1, "Files"), state, CollectingReporter(), CancelToken())

        state.close()
        saved = json.loads(paths.state_file.read_text(encoding="utf-8"))
        assert "101" in saved
        assert "102" not in saved
    finally:
        shutil.rmtree(root, ignore_errors=True)
