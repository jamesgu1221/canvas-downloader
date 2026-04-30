from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        request_delay = merged.get("request_delay")
        if request_delay is None:
            request_delay = DEFAULT_SETTINGS["request_delay"]
        return AppSettings(
            canvas_url=str(merged.get("canvas_url") or "").strip().rstrip("/"),
            download_dir=str(merged.get("download_dir") or DEFAULT_DOWNLOAD_DIR).strip(),
            request_delay=float(request_delay),
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
