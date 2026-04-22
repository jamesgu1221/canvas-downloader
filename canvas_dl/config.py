import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


@dataclass
class AppConfig:
    api_token: str
    canvas_url: str
    download_dir: Path
    request_delay: float = 0.3
    skip_courses: list = field(default_factory=list)
    only_courses: list = field(default_factory=list)
    dry_run: bool = False


def load_config(args=None) -> AppConfig:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    token = os.getenv("CANVAS_API_TOKEN", "")
    url = os.getenv("CANVAS_URL", "")
    download_dir = os.getenv("CANVAS_DOWNLOAD_DIR", r"D:\OneDrive\Desktop\课程材料")

    if args:
        if getattr(args, "token", None):
            token = args.token
        if getattr(args, "url", None):
            url = args.url
        if getattr(args, "dir", None):
            download_dir = args.dir

    # #30: use sys.exit() consistently (raises SystemExit with message → printed to stderr)
    if not token:
        sys.exit(
            "错误：未找到 CANVAS_API_TOKEN。\n"
            "请在项目目录创建 .env 文件并填写：\n"
            "  CANVAS_API_TOKEN=your_token_here\n\n"
            "Token 获取方式：登录 Canvas → 账户设置 → 新建访问令牌"
        )
    if not url:
        sys.exit(
            "错误：未找到 CANVAS_URL。\n"
            "请在 .env 中填写：\n"
            "  CANVAS_URL=https://oc.sjtu.edu.cn"
        )

    only_courses = []
    skip_courses = []
    if args:
        only_courses = getattr(args, "only_course", None) or []
        skip_courses = getattr(args, "skip_course", None) or []

    def _parse_course_ids(ids: list, flag: str) -> list[int]:
        result = []
        for c in ids:
            try:
                result.append(int(c))
            except ValueError:
                sys.exit(
                    f"错误：{flag} 的参数 {c!r} 不是有效的课程 ID（应为纯数字）。"
                )
        return result

    return AppConfig(
        api_token=token,
        canvas_url=url.rstrip("/"),
        # expanduser 让 `~/...` 写法在 Windows 与 POSIX 都展开为真实家目录
        download_dir=Path(download_dir).expanduser(),
        only_courses=_parse_course_ids(only_courses, "--only-course"),
        skip_courses=_parse_course_ids(skip_courses, "--skip-course"),
        dry_run=getattr(args, "dry_run", False) if args else False,
    )
