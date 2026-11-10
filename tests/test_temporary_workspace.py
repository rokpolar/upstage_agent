import base64
import http.client
import json
import threading
from contextlib import closing

import pytest
from conftest import FakeStudio

from app.adapters.memory_store import MemoryStore
from app.config import Settings
from app.services.workspace import Application, Dependencies
from app.web.server import create_server
from app.web.workspaces import Workspaces


@pytest.fixture
def temporary_factory(app_root):
    def create():
        settings = Settings.load(app_root)
        return Application(
            settings,
            Dependencies(FakeStudio(app_root, settings.runtime), MemoryStore()),
        )

    return create


def test_web_workspace_ignores_old_inputs_and_database(app_root, temporary_factory):
    db = app_root / "out/state.sqlite3"
    db.write_bytes(b"historical database must not be loaded")
    app = temporary_factory()
    path = app.root
    try:
        assert app.state["quotes"] == app.state["g"] == {}
        assert app.root != app_root and not app.settings.database.exists()
        assert db.read_bytes() == b"historical database must not be loaded"
        app.execute(lambda: app.state["case"].update(drafter="temporary"))
        assert not app.settings.database.exists()
    finally:
        app.close()
    assert not path.exists()


@pytest.fixture
def temporary_server(temporary_factory):
    app = temporary_factory()
    server = create_server(app, ("127.0.0.1", 0), session_factory=temporary_factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, path, body=None):
    with closing(http.client.HTTPConnection(*server.server_address, timeout=10)) as conn:
        conn.request(
            "GET" if body is None else "POST",
            path,
            None if body is None else json.dumps(body),
            {"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        data = response.read()
        return response.status, json.loads(data) if response.getheader(
            "Content-Type", ""
        ).startswith("application/json") else data


def test_fresh_pages_are_empty_and_close_removes_sources_results_and_downloads(
    temporary_server, app_root
):
    server = temporary_server
    _, first = request(server, "/api/workspace/new", {})
    prefix = first["prefix"]
    app = server.workspaces.items[prefix.rsplit("/", 1)[1]].app
    root = app.root
    upload = {"name": "same.png", "data": base64.b64encode(b"image").decode()}
    status, uploaded = request(server, prefix + "/api/quotes/upload", upload)
    assert status == 200, uploaded
    assert len(request(server, prefix + "/api/init")[1]["quotes"]) == 1
    assert list(
        root.rglob("*.json")
    )  # Studio result exists only in this temporary root.
    app.out.mkdir(parents=True, exist_ok=True)
    output = app.out / "result.pdf"
    output.write_bytes(b"temporary document")
    url = app.out_url(output)
    assert url == prefix + "/out/result.pdf"
    assert request(server, url) == (200, b"temporary document")
    assert request(server, "/out/result.pdf")[0] == 404

    _, second = request(server, "/api/workspace/new", {})
    other = second["prefix"]
    assert other != prefix
    init = request(server, other + "/api/init")[1]
    assert init["quotes"] == init["gyeolui"]["items"] == []
    assert init["selection"] is None and init["gyeolui"]["handoff"] is None
    assert request(server, other + "/out/result.pdf")[0] == 404
    assert request(server, "/api/quotes/upload", upload)[0] == 404
    assert request(server, prefix + "/api/workspace/close", {})[0] == 200
    assert not root.exists()
    assert request(server, url)[0] == 404
    assert request(server, prefix + "/api/init")[0] == 404
    assert request(server, other + "/api/init")[0] == 200
    assert not (app_root / "out/state.sqlite3").exists()


def test_expense_source_and_progress_stay_in_current_temporary_page(temporary_server):
    server = temporary_server
    prefix = request(server, "/api/workspace/new", {})[1]["prefix"]
    body = {
        "files": [
            {"name": "receipt.png", "data": base64.b64encode(b"receipt").decode()}
        ]
    }
    status, response = request(server, prefix + "/api/gyeolui/upload", body)
    assert status == 200
    gid = response["item"]["id"]
    app = server.workspaces.items[prefix.rsplit("/", 1)[1]].app
    for thread in app.threads:
        thread.join(timeout=5)
    item = request(server, prefix + "/api/gyeolui/status?id=" + gid)[1]["items"][0]
    assert item["status"] == "done" and item["progress"]["finished"]
    assert request(server, prefix + "/api/gyeolui/source?id=" + gid) == (
        200,
        b"receipt",
    )
    assert request(server, prefix + "/api/workspace/ping", {}) == (200, {"ok": True})
    root = app.root
    request(server, prefix + "/api/workspace/close", {})
    assert not root.exists()


def test_disconnect_expiry_waits_for_requests_and_background_work(temporary_factory):
    pool = Workspaces(temporary_factory, ttl=120)
    try:
        token = pool.new().rsplit("/", 1)[1]
        with pool.use(token) as app:
            root = app.root
            pool.release(token)
            assert root.exists()
        assert not root.exists()
        token = pool.new().rsplit("/", 1)[1]
        app = pool.items[token].app
        root = app.root
        finish = threading.Event()
        job = threading.Thread(target=lambda: finish.wait(5))
        app.threads.append(job)
        job.start()
        pool.items[token].touched -= 121
        pool.sweep()
        assert root.exists()
        with pytest.raises(FileNotFoundError):
            with pool.use(token):
                pass
        finish.set()
        job.join()
        pool.sweep()
        assert not root.exists()
    finally:
        pool.close()
