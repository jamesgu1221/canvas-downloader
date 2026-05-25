from __future__ import annotations

import argparse
import atexit
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .paths import get_app_paths
from .progress import TqdmReporter
from .service import RunOptions, SyncError, SyncService
from .stores import SettingsStore
from .videos import (
    VideoError,
    VideoRunOptions,
    VideoService,
    parse_lecture_filter,
    parse_video_course_ids,
)


if sys.stdout is None or sys.stderr is None:
    _paths = get_app_paths()
    _paths.base_dir.mkdir(parents=True, exist_ok=True)
    _log_file = open(_paths.cli_log_file, "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    atexit.register(_log_file.close)
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file


def parse_args():
    parser = argparse.ArgumentParser(
        description="Canvas 课件自动下载器 — oc.sjtu.edu.cn"
    )
    subparsers = parser.add_subparsers(dest="command")

    parser.add_argument("--token", help="Canvas API Token（覆盖 secrets.json）")
    parser.add_argument("--url", help="Canvas URL（覆盖 settings.json）")
    parser.add_argument("--dir", help="下载目录（覆盖 settings.json）")
    parser.add_argument(
        "--only-course",
        nargs="+",
        metavar="COURSE_ID",
        help="只下载指定课程 ID（空格分隔多个）",
    )
    parser.add_argument(
        "--skip-course",
        nargs="+",
        metavar="COURSE_ID",
        help="跳过指定课程 ID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出待下载文件，不实际下载",
    )

    videos = subparsers.add_parser("videos", help="下载课堂视频（课堂摄像头 + 教师录屏）")
    videos.add_argument(
        "--token",
        default=argparse.SUPPRESS,
        help="Canvas API Token（覆盖 secrets.json）",
    )
    videos.add_argument(
        "--url",
        default=argparse.SUPPRESS,
        help="Canvas URL（覆盖 settings.json）",
    )
    videos.add_argument(
        "--dir",
        default=argparse.SUPPRESS,
        help="视频下载目录（覆盖 settings.json，不写入配置）",
    )
    videos.add_argument(
        "--only-course",
        nargs="+",
        default=argparse.SUPPRESS,
        metavar="COURSE_ID",
        help="只下载指定课程 ID（空格分隔多个）",
    )
    videos.add_argument(
        "--skip-course",
        nargs="+",
        default=argparse.SUPPRESS,
        metavar="COURSE_ID",
        help="跳过指定课程 ID",
    )
    videos.add_argument(
        "--lecture",
        help="只下载指定节次，如 1-4,7,10；不传则下载全部可见节次",
    )
    videos.add_argument(
        "--video-url",
        action="append",
        default=[],
        help="手动指定课堂视频入口，可传 Canvas external_tools 链接或 v.sjtu.edu.cn 链接；可重复使用",
    )
    videos.add_argument(
        "--browser-cookies",
        action="store_true",
        help="从本机浏览器读取 SJTU/Canvas 登录 Cookie，用于解析需要网页登录态的 external_tools 链接",
    )
    videos.add_argument(
        "--cached-cookies",
        action="store_true",
        help="从加密缓存文件读取此前 GUI 扫码登录保存的 Cookie，用于 headless 自动下载",
    )
    videos.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="只列出待下载视频，不实际下载",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = get_app_paths()

    try:
        cached_cookies = getattr(args, "command", None) == "videos" and getattr(args, "cached_cookies", False)
        config = load_config(args, paths, require_token=not cached_cookies)
        reporter = TqdmReporter()
        if getattr(args, "command", None) == "videos":
            settings = SettingsStore(paths).load()
            if getattr(args, "dir", None):
                video_dir = config.download_dir  # already resolved from args.dir in load_config
            elif settings.video_download_dir:
                video_dir = Path(settings.video_download_dir).expanduser()
            else:
                video_dir = config.download_dir
            options = VideoRunOptions(
                dry_run=args.dry_run,
                only_courses=parse_video_course_ids(args.only_course, "--only-course"),
                skip_courses=parse_video_course_ids(args.skip_course, "--skip-course"),
                download_dir=video_dir,
                lecture_filter=parse_lecture_filter(args.lecture),
                video_urls=list(args.video_url or []),
                browser_cookies=args.browser_cookies,
                cached_cookies=args.cached_cookies,
                max_concurrent_videos=settings.video_max_concurrent_videos,
                max_workers_per_video=settings.video_max_workers_per_video,
            )
            service = VideoService(config, paths)
            try:
                service.run(options, reporter)
            finally:
                reporter.close()
        else:
            options = RunOptions(
                dry_run=args.dry_run,
                only_courses=list(config.only_courses),
                skip_courses=list(config.skip_courses),
            )
            service = SyncService(config, paths)
            try:
                service.run(options, reporter)
            finally:
                reporter.close()
        return 0
    except KeyboardInterrupt:
        print("\n中断，正在保存进度...")
        return 130
    except (ConfigError, SyncError, VideoError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
