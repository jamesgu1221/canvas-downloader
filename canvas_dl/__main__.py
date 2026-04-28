from __future__ import annotations

import argparse
import atexit
import sys

from .config import ConfigError, load_config
from .paths import get_app_paths
from .progress import TqdmReporter
from .service import RunOptions, SyncError, SyncService


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = get_app_paths()

    try:
        config = load_config(args, paths)
        options = RunOptions(
            dry_run=args.dry_run,
            only_courses=list(config.only_courses),
            skip_courses=list(config.skip_courses),
        )
        reporter = TqdmReporter()
        service = SyncService(config, paths)
        try:
            service.run(options, reporter)
        finally:
            reporter.close()
        return 0
    except KeyboardInterrupt:
        print("\n中断，正在保存进度...")
        return 130
    except (ConfigError, SyncError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
