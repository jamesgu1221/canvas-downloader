"""Compatibility wrappers for GUI pages that read/write app configuration."""

from __future__ import annotations

from ..paths import get_app_paths
from ..stores import SecretStore, SettingsStore


def _paths():
    return get_app_paths()


def get_download_dir() -> str:
    return SettingsStore(_paths()).load().download_dir


def set_download_dir(path: str) -> None:
    paths = _paths()
    settings = SettingsStore(paths).load()
    settings.download_dir = path
    SettingsStore(paths).save(settings)


def get_api_token() -> str:
    return SecretStore(_paths()).get_api_token()


def set_api_token(token: str) -> None:
    SecretStore(_paths()).set_api_token(token)


def get_canvas_url() -> str:
    return SettingsStore(_paths()).load().canvas_url


def set_canvas_url(url: str) -> None:
    paths = _paths()
    settings = SettingsStore(paths).load()
    settings.canvas_url = url.strip().rstrip("/")
    SettingsStore(paths).save(settings)


def get_config_dir() -> str:
    return str(_paths().base_dir)
