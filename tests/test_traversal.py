from types import SimpleNamespace

from canvas_dl.traversal import get_course_root_folder, sanitize_name


def test_sanitize_windows_reserved_names():
    assert sanitize_name("CON.txt") == "CON_.txt"
    assert sanitize_name("NUL") == "NUL_"


def test_sanitize_illegal_characters():
    assert sanitize_name('a:b?.txt') == "a_b_.txt"


class _FolderClient:
    def __init__(self, folders) -> None:
        self._folders = folders

    def get_course_folders(self, course):
        return self._folders


def test_get_course_root_uses_external_parent_id_before_min_id():
    child = SimpleNamespace(id=1, parent_folder_id=10, full_name="Assignments", name="Assignments")
    root = SimpleNamespace(id=10, parent_folder_id=999, full_name="Fichiers", name="Fichiers")

    selected = get_course_root_folder(_FolderClient([child, root]), SimpleNamespace())

    assert selected is root


def test_get_course_root_uses_localized_name_when_parent_attr_missing():
    child = SimpleNamespace(id=1, full_name="Assignments", name="Assignments")
    root = SimpleNamespace(id=10, full_name="Fichiers", name="Fichiers")

    selected = get_course_root_folder(_FolderClient([child, root]), SimpleNamespace())

    assert selected is root
