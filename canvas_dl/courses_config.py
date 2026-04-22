import json
import os
from pathlib import Path

from .client import safe_course_name


HELP_TEXT = "将某门课的 enabled 改为 false 即可跳过该课程；新课程会自动添加并默认启用。"


def load_or_init(path: Path) -> dict:
    if not path.exists():
        return {"_help": HELP_TEXT, "courses": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise json.JSONDecodeError(
            f"courses.json 顶层应为对象（dict），实际是 {type(data).__name__}",
            doc="",
            pos=0,
        )
    # #20: raise instead of silently clearing non-list courses field
    if "courses" not in data:
        data["courses"] = []
    elif not isinstance(data["courses"], list):
        raise json.JSONDecodeError(
            f"courses.json 中 'courses' 字段应为列表，实际为 {type(data['courses']).__name__}",
            doc="",
            pos=0,
        )
    return data


def sync_with_canvas(data: dict, canvas_courses: list) -> tuple:
    """
    合并 Canvas 最新课程列表到 data 中。
    返回 (enabled_ids, newly_added_names, newly_inactive_names)
    """
    # #12: detect duplicate IDs, warn, and remove stale duplicates.
    # Iterate once to build existing_by_id (last entry wins, matching old behaviour).
    existing_by_id: dict = {}
    has_duplicates = False
    for c in data["courses"]:
        cid = c.get("id")
        if cid is None:
            continue
        if cid in existing_by_id:
            print(f"警告：courses.json 中课程 ID {cid} 出现重复，保留最后一个条目", flush=True)
            has_duplicates = True
        existing_by_id[cid] = c

    # #31: purge stale duplicate entries so they don't re-appear after save.
    # Keep the last occurrence of each id (consistent with existing_by_id).
    if has_duplicates:
        seen_ids: set = set()
        deduped = []
        for c in reversed(data["courses"]):
            cid = c.get("id")
            if cid is None or cid not in seen_ids:
                deduped.append(c)
                if cid is not None:
                    seen_ids.add(cid)
        data["courses"] = list(reversed(deduped))

    canvas_ids = set()
    newly_added = []

    for course in canvas_courses:
        cid = course.id
        cname = safe_course_name(course) or f"course_{cid}"
        canvas_ids.add(cid)

        if cid in existing_by_id:
            entry = existing_by_id[cid]
            entry["name"] = cname
            entry["active"] = True
            entry.setdefault("enabled", True)
        else:
            entry = {"id": cid, "name": cname, "enabled": True, "active": True}
            data["courses"].append(entry)
            newly_added.append(cname)

    newly_inactive = []
    for entry in data["courses"]:
        if entry.get("id") not in canvas_ids:
            if entry.get("active") is not False:
                entry["active"] = False
                newly_inactive.append(entry.get("name", str(entry.get("id"))))

    data.setdefault("_help", HELP_TEXT)

    enabled_ids = [
        e["id"]
        for e in data["courses"]
        if e.get("enabled", True) and e.get("active", True) and "id" in e
    ]
    return enabled_ids, newly_added, newly_inactive


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        # json.dump / os.replace 失败时把孤儿 tmp 清掉
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
