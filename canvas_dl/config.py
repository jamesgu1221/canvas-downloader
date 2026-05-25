from dataclasses import dataclass, field
from pathlib import Path

from .paths import AppPaths, get_app_paths
from .stores import SecretStore, SettingsStore


@dataclass
class AppConfig:
    api_token: str
    canvas_url: str
    download_dir: Path
    request_delay: float = 0.3
    skip_courses: list = field(default_factory=list)
    only_courses: list = field(default_factory=list)
    dry_run: bool = False


class ConfigError(RuntimeError):
    pass


def parse_course_ids(ids: list, flag: str) -> list[int]:
    result = []
    for c in ids:
        try:
            result.append(int(c))
        except ValueError as e:
            raise ConfigError(
                f"错误：{flag} 的参数 {c!r} 不是有效的课程 ID（应为纯数字）。"
            ) from e
    return result


def load_config(args=None, paths: AppPaths | None = None, require_token: bool = True) -> AppConfig:
    paths = paths or get_app_paths()

    settings = SettingsStore(paths).load()
    token = SecretStore(paths).get_api_token()
    url = settings.canvas_url
    download_dir = settings.download_dir

    if args:
        if getattr(args, "token", None):
            token = args.token
        if getattr(args, "url", None):
            url = args.url
        if getattr(args, "dir", None):
            download_dir = args.dir

    if require_token and not token:
        raise ConfigError(
            "错误：未找到 CANVAS_API_TOKEN。\n"
            "请在 GUI 设置页保存 Token，或在新配置目录的 secrets.json 中填写。\n\n"
            "Token 获取方式：登录 Canvas → 账户设置 → 新建访问令牌"
        )
    if not url:
        raise ConfigError(
            "错误：未找到 CANVAS_URL。\n"
            "请在 settings.json 中填写 canvas_url，或通过 --url 临时传入。"
        )

    only_courses = []
    skip_courses = []
    if args:
        only_courses = getattr(args, "only_course", None) or []
        skip_courses = getattr(args, "skip_course", None) or []

    return AppConfig(
        api_token=token,
        canvas_url=url.rstrip("/"),
        download_dir=Path(download_dir).expanduser(),
        request_delay=settings.request_delay,
        only_courses=parse_course_ids(only_courses, "--only-course"),
        skip_courses=parse_course_ids(skip_courses, "--skip-course"),
        dry_run=getattr(args, "dry_run", False) if args else False,
    )
