import re
from pathlib import Path
from typing import Iterator

# Windows device names are reserved regardless of extension (e.g. "NUL.txt" is illegal).
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
})


def sanitize_name(name: str, max_len: int = 150) -> str:
    # Remove characters illegal on Windows
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    # Strip leading/trailing dots and spaces
    name = name.strip(". ")
    if not name:
        name = "_"
    # Preserve extension when truncating
    path = Path(name)
    stem = path.stem
    suffix = path.suffix
    # #31: Windows treats reserved device names (e.g. CON, NUL, COM1) as special
    # even when an extension is present; append "_" to the stem to make them safe.
    if stem.upper() in _WINDOWS_RESERVED:
        stem = stem + "_"
        name = stem + suffix
    if len(name) > max_len:
        # #14: ensure stem keeps at least 16 chars; truncate overlong suffix first
        _min_stem = 16
        if len(suffix) > max_len - _min_stem:
            suffix = suffix[: max(0, max_len - _min_stem)]
        stem = stem[: max_len - len(suffix)]
        name = stem + suffix
    return name


def get_course_root_folder(client, course):
    """Return the root 'course files' folder for a course.

    判断逻辑（优先级从高到低）：
    1. parent_folder_id is None 的文件夹集合 → 取 id 最小者（多根时确定性兜底）
    2. full_name 大小写不敏感匹配 "course files"（本地化 Canvas 可能显示为"课程文件"等）
    3. 以上均无匹配 → 取 id 最小的文件夹
    """
    folders = client.get_course_folders(course)
    if not folders:
        return None

    # #4 fix: 优先取 parent_folder_id is None 的集合，多个时按 id 最小确保确定性
    no_parent = [f for f in folders if getattr(f, "parent_folder_id", None) is None]
    if no_parent:
        return min(no_parent, key=lambda f: f.id)

    # 次选：full_name 匹配（兼容本地化名称，如"课程文件"）
    ROOT_NAMES = {"course files", "课程文件"}
    for f in folders:
        full_name = (getattr(f, "full_name", "") or "").lower()
        if full_name in ROOT_NAMES:
            return f

    # Fallback: smallest id is usually root
    return min(folders, key=lambda f: f.id)


_MAX_DEPTH = 50


def walk_folder(
    client,
    folder,
    local_base: Path,
    _visited: frozenset[int] | None = None,
    _depth: int = 0,
) -> Iterator[tuple]:
    """Recursively yield (canvas_file, local_dest_path) for all files under folder.

    Collisions across file↔file, folder↔folder, and file↔folder within the same
    directory are resolved by appending `_{id}` to the stem. Subfolder names are
    reserved first so a same-name file doesn't shadow a directory we recurse into
    (which would make mkdir fail on Windows).

    Filesystem side-effect-free: download_file creates parents at write time,
    so dry-runs leave the disk untouched.

    _visited: 已访问过的 folder id 集合，用于防止 Canvas 返回子→祖先循环引用导致无限递归。
    外部调用者无需传此参数，由函数自身维护。
    """
    # #29: hard depth cap prevents stack overflow on pathologically deep hierarchies
    if _depth >= _MAX_DEPTH:
        return
    # #9 fix: 初始化祖先访问集合，防止循环引用爆栈
    if _visited is None:
        _visited = frozenset()
    _visited = _visited | {folder.id}

    # Canvas file/folder IDs are monotonic and never reused; sorting by id gives
    # deterministic collision resolution across runs (otherwise API ordering
    # drift causes duplicate-named entries to swap local paths and re-download).
    files = sorted(client.get_folder_files(folder), key=lambda f: f.id)
    subfolders = sorted(client.get_subfolders(folder), key=lambda s: s.id)
    # #9 fix: 过滤自引用及所有已访问的祖先（原来只过滤 self-reference）
    subfolders = [s for s in subfolders if s.id not in _visited]

    seen_names = set()

    resolved_subfolders = []
    for sub in subfolders:
        sub_name = sanitize_name(getattr(sub, "name", None) or f"folder_{sub.id}")
        if sub_name in seen_names:
            path = Path(sub_name)
            sub_name = f"{path.stem}_{sub.id}{path.suffix}"
            if sub_name in seen_names:
                sub_name = f"folder_{sub.id}"
        seen_names.add(sub_name)
        resolved_subfolders.append((sub, sub_name))

    for f in files:
        name = sanitize_name(getattr(f, "display_name", None) or f"file_{f.id}")
        if name in seen_names:
            path = Path(name)
            name = f"{path.stem}_{f.id}{path.suffix}"
            if name in seen_names:
                # stem_{id}.ext still collides (another file already had that exact name);
                # fall back to id-only which is guaranteed unique.
                name = f"file_{f.id}"
        seen_names.add(name)
        yield f, local_base / name

    for sub, sub_name in resolved_subfolders:
        yield from walk_folder(client, sub, local_base / sub_name, _visited, _depth + 1)
