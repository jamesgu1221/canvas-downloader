import shutil
import uuid
import json
from pathlib import Path

from canvas_dl.paths import AppPaths
from canvas_dl.stores import SecretStore, SettingsStore, migrate_legacy


def test_migrate_legacy_files():
    root = Path(".test_tmp") / f"migration_{uuid.uuid4().hex}"
    try:
        project_root = root / "project"
        base_dir = root / "config"
        project_root.mkdir(parents=True)
        (project_root / ".env").write_text(
            "CANVAS_API_TOKEN=tok\n"
            "CANVAS_URL=https://canvas.example.edu\n"
            "CANVAS_DOWNLOAD_DIR=C:\\Courses\n",
            encoding="utf-8",
        )
        (project_root / "courses.json").write_text('{"courses": [{"id": 1}]}', encoding="utf-8")
        (project_root / "sync_state.json").write_text('{"10": {"size": 3}}', encoding="utf-8")

        paths = AppPaths(base_dir=base_dir, project_root=project_root)

        assert migrate_legacy(paths) is True
        assert SettingsStore(paths).load().canvas_url == "https://canvas.example.edu"
        assert SettingsStore(paths).load().download_dir == "C:\\Courses"
        assert SecretStore(paths).get_api_token() == "tok"
        assert json.loads(paths.courses_file.read_text(encoding="utf-8")) == {"courses": [{"id": 1}]}
        assert json.loads(paths.state_file.read_text(encoding="utf-8")) == {"10": {"size": 3}}

        assert migrate_legacy(paths) is False
    finally:
        shutil.rmtree(root, ignore_errors=True)
