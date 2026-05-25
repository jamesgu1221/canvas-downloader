from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def runtime_root() -> Path:
    """Return the user-visible application root.

    In source runs this is the repository root. In a PyInstaller build,
    `__file__` points inside the temporary bundle, so runtime helpers that need
    the exe directory must resolve from `sys.executable` instead.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path

    @property
    def settings_file(self) -> Path:
        return self.base_dir / "settings.json"

    @property
    def secrets_file(self) -> Path:
        return self.base_dir / "secrets.json"

    @property
    def courses_file(self) -> Path:
        return self.base_dir / "courses.json"

    @property
    def state_file(self) -> Path:
        return self.base_dir / "sync_state.json"

    @property
    def cli_log_file(self) -> Path:
        return self.base_dir / "canvas_dl.log"

    @property
    def gui_log_file(self) -> Path:
        return self.base_dir / "canvas_gui_qt.log"

    @property
    def video_cookies_file(self) -> Path:
        return self.base_dir / "video_cookies.dat"

    @property
    def video_auto_courses_file(self) -> Path:
        return self.base_dir / "video_auto_courses.json"

    @property
    def video_lectures_cache_file(self) -> Path:
        return self.base_dir / "video_lectures_cache.json"


def default_config_dir() -> Path:
    override = os.getenv("CANVAS_DL_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "CanvasDownloader"
    return Path.home() / ".canvas-downloader"


def get_app_paths() -> AppPaths:
    return AppPaths(default_config_dir())
