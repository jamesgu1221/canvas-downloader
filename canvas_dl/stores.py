from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .paths import AppPaths, get_app_paths


DEFAULT_CANVAS_URL = "https://oc.sjtu.edu.cn"
DEFAULT_DOWNLOAD_DIR = r"D:\OneDrive\Desktop\课程材料"
DEFAULT_SETTINGS = {
    "canvas_url": DEFAULT_CANVAS_URL,
    "download_dir": DEFAULT_DOWNLOAD_DIR,
    "request_delay": 0.3,
}


class StoreError(RuntimeError):
    pass


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@dataclass
class AppSettings:
    canvas_url: str = DEFAULT_CANVAS_URL
    download_dir: str = DEFAULT_DOWNLOAD_DIR
    request_delay: float = 0.3


class SettingsStore:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or get_app_paths()

    def load(self) -> AppSettings:
        data = _load_json(self.paths.settings_file, {})
        if not isinstance(data, dict):
            raise StoreError(f"{self.paths.settings_file} 顶层应为对象")
        merged = {**DEFAULT_SETTINGS, **data}
        return AppSettings(
            canvas_url=str(merged.get("canvas_url") or "").strip().rstrip("/"),
            download_dir=str(merged.get("download_dir") or DEFAULT_DOWNLOAD_DIR).strip(),
            request_delay=float(merged.get("request_delay") or 0.3),
        )

    def save(self, settings: AppSettings) -> None:
        _save_json(
            self.paths.settings_file,
            {
                "canvas_url": settings.canvas_url.rstrip("/"),
                "download_dir": settings.download_dir,
                "request_delay": settings.request_delay,
            },
        )


class SecretStore:
    def __init__(self, paths: AppPaths | None = None):
        self.paths = paths or get_app_paths()

    def get_api_token(self) -> str:
        data = _load_json(self.paths.secrets_file, {})
        if not isinstance(data, dict):
            raise StoreError(f"{self.paths.secrets_file} 顶层应为对象")
        return str(data.get("canvas_api_token") or "").strip()

    def set_api_token(self, token: str) -> None:
        _save_json(self.paths.secrets_file, {"canvas_api_token": token.strip()})


def migrate_legacy(paths: AppPaths | None = None) -> bool:
    """Migrate legacy project-root files into the user config directory once.

    Existing new-format values win. Legacy files are intentionally left in place.
    Returns True when the migration marker is created during this call.
    """
    paths = paths or get_app_paths()
    if paths.migration_marker.exists():
        return False

    paths.base_dir.mkdir(parents=True, exist_ok=True)
    legacy_env = paths.project_root / ".env"
    legacy_courses = paths.project_root / "courses.json"
    legacy_state = paths.project_root / "sync_state.json"

    settings_store = SettingsStore(paths)
    secret_store = SecretStore(paths)

    settings = settings_store.load()
    token = secret_store.get_api_token()

    if legacy_env.exists():
        values = dotenv_values(str(legacy_env))
        if settings.canvas_url in ("", DEFAULT_CANVAS_URL) and values.get("CANVAS_URL"):
            settings.canvas_url = str(values["CANVAS_URL"]).strip().rstrip("/")
        if (
            settings.download_dir == DEFAULT_DOWNLOAD_DIR
            and values.get("CANVAS_DOWNLOAD_DIR")
        ):
            settings.download_dir = str(values["CANVAS_DOWNLOAD_DIR"]).strip()
        if not token and values.get("CANVAS_API_TOKEN"):
            secret_store.set_api_token(str(values["CANVAS_API_TOKEN"]).strip())

    settings_store.save(settings)

    if legacy_courses.exists() and not paths.courses_file.exists():
        data = _load_json(legacy_courses, None)
        _save_json(paths.courses_file, data)
    if legacy_state.exists() and not paths.state_file.exists():
        data = _load_json(legacy_state, None)
        _save_json(paths.state_file, data)

    paths.migration_marker.write_text("1\n", encoding="utf-8")
    return True
