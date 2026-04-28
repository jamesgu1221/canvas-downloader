import shutil
import uuid
from pathlib import Path

import pytest

from canvas_dl.config import ConfigError, parse_course_ids
from canvas_dl.paths import AppPaths
from canvas_dl.stores import SettingsStore


def test_parse_course_ids_rejects_non_numeric():
    with pytest.raises(ConfigError):
        parse_course_ids(["abc"], "--only-course")


def test_parse_course_ids_accepts_numbers():
    assert parse_course_ids(["1", "23"], "--skip-course") == [1, 23]


def test_default_canvas_url_is_sjtu():
    root = Path(".test_tmp") / f"default_url_{uuid.uuid4().hex}"
    try:
        paths = AppPaths(base_dir=root / "config", project_root=root / "project")

        assert SettingsStore(paths).load().canvas_url == "https://oc.sjtu.edu.cn"
    finally:
        shutil.rmtree(root, ignore_errors=True)
