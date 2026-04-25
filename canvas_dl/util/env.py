"""`.env` 读写集中模块。

用 `dotenv_values` + `set_key` 读写 `CANVAS_DOWNLOAD_DIR` / `CANVAS_API_TOKEN` / `CANVAS_URL`。
所有操作针对项目根目录的 `.env`，不会因为当前工作目录变化而打偏。
"""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values, set_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def _values() -> dict[str, str | None]:
    if not ENV_PATH.exists():
        return {}
    return dotenv_values(str(ENV_PATH))


def get_download_dir() -> str:
    return (_values().get("CANVAS_DOWNLOAD_DIR") or "").strip()


def set_download_dir(path: str) -> None:
    _ensure_env_exists()
    set_key(str(ENV_PATH), "CANVAS_DOWNLOAD_DIR", path)


def get_api_token() -> str:
    return (_values().get("CANVAS_API_TOKEN") or "").strip()


def set_api_token(token: str) -> None:
    _ensure_env_exists()
    set_key(str(ENV_PATH), "CANVAS_API_TOKEN", token)


def get_canvas_url() -> str:
    return (_values().get("CANVAS_URL") or "").strip()


def _ensure_env_exists() -> None:
    """`dotenv.set_key` 仅在 .env 存在时追加；不存在就先创建空文件避免静默失败。"""
    if not ENV_PATH.exists():
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENV_PATH.touch()
