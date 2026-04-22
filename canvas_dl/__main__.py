import argparse
import json
import sys
import threading
from pathlib import Path

# pythonw.exe（Task Scheduler 无窗口模式）下 sys.stdout/stderr 为 None，
# tqdm 会直接 AttributeError 崩溃。检测到无控制台时重定向到日志文件。
if sys.stdout is None or sys.stderr is None:
    import atexit
    _log_path = Path(__file__).resolve().parent.parent / "canvas_dl.log"
    # #25: errors="replace" prevents UnicodeEncodeError on unusual filenames
    _log_file = open(_log_path, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
    atexit.register(_log_file.close)
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file

import requests.exceptions
from tqdm import tqdm

from .config import load_config
from .client import CanvasClient, safe_course_name
from .state import SyncState
from .traversal import get_course_root_folder, walk_folder, sanitize_name
from .progress import make_course_bar, make_file_bar, report_empty_course
from . import courses_config as cc

# #28: single lock serialises all tqdm.write calls to prevent progress-bar corruption
_write_lock = threading.Lock()


def _log(msg: str) -> None:
    with _write_lock:
        tqdm.write(msg)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Canvas 课件自动下载器 — oc.sjtu.edu.cn"
    )
    parser.add_argument("--token", help="Canvas API Token（覆盖 .env）")
    parser.add_argument("--url", help="Canvas URL（覆盖 .env）")
    parser.add_argument("--dir", help="下载目录（覆盖 .env）")
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


def filter_courses(courses, config):
    if config.only_courses:
        courses = [c for c in courses if c.id in config.only_courses]
    if config.skip_courses:
        courses = [c for c in courses if c.id not in config.skip_courses]
    return courses


def get_course_name(course) -> str:
    name = safe_course_name(course) or f"course_{course.id}"
    return sanitize_name(name)


def process_course(client, state, config, course, course_bar) -> int:
    """返回本门课成功下载的文件数。"""
    course_name = get_course_name(course)
    course_dir = config.download_dir / course_name

    try:
        root = get_course_root_folder(client, course)
    except Exception as e:
        _log(f"  [{course_name}] 获取文件夹失败（可能是权限不足）：{e}")
        course_bar.update(1)
        return 0
    if root is None:
        _log(f"  [{course_name}] 无文件")
        report_empty_course(course_name)
        course_bar.update(1)
        return 0

    # Collect all files first for accurate progress bar
    try:
        all_files = list(walk_folder(client, root, course_dir))
    except Exception as e:
        _log(f"  [{course_name}] 遍历文件夹失败：{e}")
        course_bar.update(1)
        return 0

    if not all_files:
        _log(f"  [{course_name}] 无文件")
        report_empty_course(course_name)
        course_bar.update(1)
        return 0

    downloaded = 0
    skipped = 0
    failed = 0

    with make_file_bar(course_name, len(all_files)) as file_bar:
        for canvas_file, local_path in all_files:
            file_bar.set_postfix_str((canvas_file.display_name or f"file_{canvas_file.id}")[:30])

            if state.is_current(canvas_file, local_path):
                skipped += 1
                file_bar.update(1)
                continue

            url = getattr(canvas_file, "url", "") or ""
            if not url or not url.startswith(("http://", "https://")):
                locked = getattr(canvas_file, "locked_for_user", False)
                reason = "被锁定" if locked else "无下载 URL"
                _log(f"  [跳过] {canvas_file.display_name}: {reason}")
                skipped += 1
                file_bar.update(1)
                continue

            if config.dry_run:
                _log(f"  [DRY] {local_path}")
                file_bar.update(1)
                continue

            try:
                client.download_file(url, local_path)
                state.record(canvas_file, local_path)
                downloaded += 1
            except OSError as e:
                if "No space left" in str(e) or "磁盘空间不足" in str(e):
                    _log(f"\n磁盘空间不足，已终止。最后尝试的文件：{local_path}")
                    raise SystemExit(1)
                _log(f"  [错误] {canvas_file.display_name}: {e}")
                failed += 1
            except (RuntimeError, requests.exceptions.RequestException) as e:
                # #21: narrow from bare Exception — RuntimeError covers HTML-login-page
                # detection; RequestException covers network errors after retry exhaustion.
                _log(f"  [错误] {canvas_file.display_name}: {e}")
                failed += 1

            file_bar.update(1)

    summary = f"  [{course_name}] 下载 {downloaded}，跳过 {skipped}"
    if failed:
        summary += f"，失败 {failed}"
    _log(summary)
    course_bar.update(1)
    return downloaded


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _migrate_legacy_state(legacy_dir: Path, new_path: Path) -> None:
    """迁移早期版本遗留在下载目录里的 sync_state.{json,lock}。

    只搬运，不合并：若新位置已有 sync_state.json，则旧的会被直接删除
    （假定新位置更权威，因为本次启动会从新位置读写）。遗留的 .lock 文件
    一律清理，避免下次在目录里残留。
    """
    legacy_state = legacy_dir / "sync_state.json"
    legacy_lock = legacy_dir / "sync_state.lock"
    legacy_tmp = legacy_dir / "sync_state.tmp"

    if legacy_state.exists():
        try:
            if new_path.exists():
                legacy_state.unlink()
            else:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_state.replace(new_path)
        except OSError as e:
            print(f"警告：迁移旧 sync_state.json 失败（{e}），下次启动会重试。")

    for stray in (legacy_lock, legacy_tmp):
        if stray.exists():
            try:
                stray.unlink()
            except OSError:
                pass


def main():
    args = parse_args()
    config = load_config(args)

    state_path = _PROJECT_ROOT / "sync_state.json"
    _migrate_legacy_state(config.download_dir, state_path)
    state = SyncState(state_path)

    # 外层 try/finally 覆盖 state.load() 以后的全部路径，确保任意退出分支
    # （Ctrl-C 在 get_courses() / 同步 courses.json 期间、未知异常等）都会
    # 释放 .lock 文件，避免锁泄漏。state.save() 已经会释放锁，所以重复
    # 调用 state.close() 是幂等无害的。
    try:
        state.load()

        print(f"Canvas: {config.canvas_url}")
        print(f"下载到: {config.download_dir}")
        if config.dry_run:
            print("[DRY RUN 模式 — 不实际下载]\n")

        client = CanvasClient(config)
        print("正在获取课程列表...")
        courses = client.get_courses()

        # 读取 / 同步 courses.json
        courses_file = _PROJECT_ROOT / "courses.json"
        try:
            data = cc.load_or_init(courses_file)
        except json.JSONDecodeError as e:
            print(f"错误：courses.json 解析失败（{e}），已放弃写入，请检查文件格式。")
            sys.exit(1)
        enabled_ids, added, newly_inactive = cc.sync_with_canvas(data, courses)
        cc.save(courses_file, data)

        if added:
            print(f"检测到新课程（已加入清单并默认启用）：{', '.join(added)}")
        if newly_inactive:
            print(f"以下课程在 Canvas 上已不可见，已标记为 inactive：{', '.join(newly_inactive)}")

        total_from_canvas = len(courses)
        courses = [c for c in courses if c.id in set(enabled_ids)]
        disabled_count = total_from_canvas - len(courses)

        # CLI 的 --only-course / --skip-course 叠加在 courses.json 过滤之上
        courses = filter_courses(courses, config)

        print(f"已启用 {len(courses)} 门，已禁用 {disabled_count} 门")

        if not courses:
            print("未找到任何课程。")
            return

        print(f"共 {len(courses)} 门课程\n")

        total_downloaded = 0
        try:
            with make_course_bar(len(courses)) as course_bar:
                for course in courses:
                    total_downloaded += process_course(client, state, config, course, course_bar)
        except KeyboardInterrupt:
            print("\n中断，正在保存进度...")
            if config.dry_run:
                # #22: dry-run never records anything; just release the lock
                state.close()
            else:
                state.save()
                print(f"进度已保存到 {state_path}")
            sys.exit(0)
        except SystemExit:
            print("\n已终止，正在保存进度...")
            if config.dry_run:
                state.close()
            else:
                state.save()
                print(f"进度已保存到 {state_path}")
            raise

        if config.dry_run:
            state.close()
        else:
            state.save()
            print(f"\n完成！本次共下载 {total_downloaded} 个文件")
    finally:
        state.close()


if __name__ == "__main__":
    main()
