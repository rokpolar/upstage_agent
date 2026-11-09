import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.files import write_json
from app.adapters.store import StateStore
from app.adapters.studio import StudioClient
from app.config import Settings
from app.services.workspace import Application, Dependencies


class FakeStudio(StudioClient):
    def settings(self, kind="pumui"):
        return {
            "key": "test",
            "agent_id": "test",
            "config_id": "1",
            "project_id": "test",
        }, []

    def run_file(self, path, out_dir, progress=None, kind="pumui", on_job=None):
        if on_job:
            on_job("job-test")
        if progress:
            progress(["upload"], {}, False)
        fixture = (
            ROOT / "results/q01.json"
            if kind == "pumui"
            else next((ROOT / "examples/gyeolui").glob("*.json"))
        )
        raw = json.loads(fixture.read_text())
        write_json(Path(out_dir) / f"{Path(path).stem}.json", raw)
        if progress:
            progress(["upload", "draft"], {}, True)
        return raw, "job-test"


@pytest.fixture
def app_root(tmp_path):
    for name in ("config", "examples", "forms", "ui", "templates"):
        shutil.copytree(ROOT / name, tmp_path / name)
    for name in ("data_upload", "results_upload", "results_gyeolui_upload", "out"):
        (tmp_path / name).mkdir(exist_ok=True)
    shutil.copy(ROOT / "results/q01.json", tmp_path / "results_upload/q01.json")
    shutil.copy(ROOT / "results/q04.json", tmp_path / "results_upload/q04.json")
    shutil.copy(
        next((ROOT / "examples/gyeolui").glob("*.json")),
        tmp_path / "results_gyeolui_upload/expense.json",
    )
    return tmp_path


@pytest.fixture
def make_app(app_root):
    def create():
        settings = Settings.load(app_root)
        return Application(
            settings,
            Dependencies(
                FakeStudio(app_root, settings.runtime), StateStore(settings.database)
            ),
        )

    return create


@pytest.fixture
def app(make_app):
    instance = make_app()
    yield instance
    instance.close()
