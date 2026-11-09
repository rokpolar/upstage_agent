import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.adapters.store import StateStore
from app.domain.harness import HOLD
from app.services.workspace import ConflictError
from app.web.routes import Routes


def test_restart_restores_review_selection_case_info_and_outputs(app, make_app):
    api = Routes(app)
    qid = "0:q04"
    question = app.summary(qid)["questions"][0]
    api.post("/api/answer", dict(id=qid, key=question["key"], value=HOLD, who="담당자"))
    api.post("/api/case", {"case": {**app.state["case"], "drafter": "복원 담당자"}})
    api.post("/api/select", dict(ids=["0:q01"], pick="0:q01"))
    gid = "g0:expense"
    api.post(
        "/api/gyeolui/info",
        dict(id=gid, info={"drafter": "복원 담당자", "resolution_date": "2026-11-10"}),
    )

    def save_outputs():
        app.state["quotes"]["0:q01"]["fill"] = {"files": {"hwpx": "/out/saved.hwpx"}}
        app.state["g"][gid]["fill"] = {"pdf": "/out/saved.pdf"}
        app.state["handoff"] = {"vendor": "업체", "total": 100}

    app.execute(save_outputs)
    restored = make_app()
    init = Routes(restored).init()
    assert init["case"]["drafter"] == "복원 담당자"
    assert init["selection"]["result_file"] == "0:q01"
    quote = restored.summary(qid)
    assert quote["hold"] and quote["answered"][0]["by"] == "담당자"
    assert restored.summary("0:q01")["fill"]["files"]["hwpx"] == "/out/saved.hwpx"
    expense = restored.g_summary(gid)
    assert expense["info_saved"] and expense["info"]["drafter"] == "복원 담당자"
    assert expense["fill"]["pdf"] == "/out/saved.pdf"
    assert restored.handoff_summary()["total"] == 100


def test_new_result_import_does_not_replace_answers(app, make_app):
    app.execute(lambda: app.state["quotes"]["0:q01"]["state"].update(hold=True))
    p = app.root / "results_upload/new.json"
    p.write_bytes((app.root / "results_upload/q01.json").read_bytes())
    restored = make_app()
    assert restored.summary("0:q01")["hold"]
    assert restored.summary("0:new")


def test_database_failure_rolls_back_memory_and_no_success(app, monkeypatch):
    before, revision = app.snapshot()

    def fail(_):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(app.store, "save", fail)
    with pytest.raises(sqlite3.OperationalError):
        Routes(app).post("/api/case", {"case": {"drafter": "not saved"}})
    assert app.state == before
    assert app.version == revision


def test_invalid_existing_database_is_preserved(tmp_path):
    path = tmp_path / "state.sqlite3"
    path.write_bytes(b"broken database")
    with pytest.raises(sqlite3.DatabaseError):
        StateStore(path)
    assert path.read_bytes() == b"broken database"


def test_corrupted_snapshot_is_not_silently_reset(app, make_app):
    with app.store.connect() as db:
        db.execute("UPDATE workspace SET payload='{}'")
    with pytest.raises(RuntimeError):
        make_app()
    with app.store.connect() as db:
        assert db.execute("SELECT payload FROM workspace").fetchone()[0] == "{}"


def test_transient_progress_is_not_persisted(app, make_app):
    def change():
        app.state["texts"]["secret text cache"] = "cached"
        app.state["g"]["in-flight"] = dict(status="running", progress={"done": []})
        app.state["g"]["g0:expense"]["progress"] = {"found": {"value": "temporary"}}

    app.execute(change)
    with app.store.connect() as db:
        raw = db.execute("SELECT payload FROM workspace").fetchone()[0]
    assert (
        "secret text cache" not in raw
        and "in-flight" not in raw
        and '"progress"' not in raw
    )
    restored = make_app()
    assert "in-flight" not in restored.state["g"]


def test_concurrent_changes_do_not_lose_updates(app, make_app):
    def update(i):
        app.execute(lambda: app.state["case"].update({f"key{i}": str(i)}))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(16)))
    restored = make_app()
    assert all(restored.state["case"][f"key{i}"] == str(i) for i in range(16))


def test_stale_revision_rejected(app):
    _, version = app.snapshot()
    app.execute(lambda: app.state["case"].update(drafter="changed"))
    with pytest.raises(ConflictError):
        app.execute(lambda: app.state["case"].clear(), expected=version)
    assert app.state["case"]["drafter"] == "changed"


def test_state_paths_rebase_when_runtime_root_changes(app, tmp_path):
    import shutil

    old_source = app.root / "data_upload/quote.pdf"
    old_source.write_bytes(b"pdf")
    app.execute(lambda: app.state["quotes"]["0:q01"].update(source=str(old_source)))
    other = tmp_path / "container-app"
    (other / "out").mkdir(parents=True)
    shutil.copy(app.settings.database, other / "out/state.sqlite3")
    restored = StateStore(other / "out/state.sqlite3").load()
    assert restored["quotes"]["0:q01"]["source"] == str(other / "data_upload/quote.pdf")


def test_duplicate_quote_upload_names_are_unique(app):
    data = base64.b64encode(b"image placeholder").decode()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: app.studio_quote("same.png", data), range(2)))
    assert len({qid for qid, _ in results}) == 2
    quotes = [app.state["quotes"][qid] for qid, _ in results]
    assert len({q["source"] for q in quotes}) == 2
    assert all(q["name"] == "same" for q in quotes)


def test_completed_background_job_preserves_progress_and_restores(app, make_app):
    api = Routes(app)
    response = api.post(
        "/api/gyeolui/upload",
        {
            "files": [
                {"name": "receipt.png", "data": base64.b64encode(b"image").decode()}
            ]
        },
    )
    gid = response["item"]["id"]
    app.close()
    item = api.get("/api/gyeolui/status", {"id": [gid]})["items"][0]
    assert item["status"] == "done" and item["job_id"] == "job-test"
    assert item["progress"]["finished"] and item["source_kind"] == "img"
    assert app.source_path(gid).exists()
    assert make_app().g_summary(gid)["status"] == "done"
