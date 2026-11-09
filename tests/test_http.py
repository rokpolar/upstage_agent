import http.client
import json
import sqlite3
import threading

import pytest

from app.web.server import create_server


@pytest.fixture
def server(app):
    server = create_server(app, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def request(server, path, body=None, raw=None):
    conn = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        data = (
            raw if raw is not None else json.dumps(body) if body is not None else None
        )
        conn.request(
            "GET" if data is None else "POST",
            path,
            data,
            {"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        return response.status, response.read(), response.getheader("Content-Type")
    finally:
        conn.close()


def test_health_init_and_modules(server):
    assert request(server, "/healthz")[:2] == (200, b'{"ok": true}')
    status, body, _ = request(server, "/api/init")
    init = json.loads(body)
    assert status == 200 and len(init["quotes"]) == 2
    assert "fill" in init["quotes"][0]
    assert init["gyeolui"]["items"][0]["info_saved"] is False
    assert b'type="module"' in request(server, "/")[1]
    for module in (
        "app",
        "state",
        "api",
        "navigation",
        "forms",
        "pumui",
        "gyeolui",
        "progress",
        "shell",
    ):
        assert request(server, f"/ui/{module}.js")[0] == 200


@pytest.mark.parametrize(
    "path,body,raw,expected",
    [
        ("/api/case", None, "{", 400),
        ("/api/case", [], None, 400),
        ("/api/case", {}, None, 400),
        ("/api/compare", {"ids": ["missing"]}, None, 404),
        ("/api/fill", {"id": "missing", "form": "forms/file.hwpx"}, None, 404),
        (
            "/api/answer",
            {"id": "0:q04", "key": "missing", "value": "__ACK__"},
            None,
            404,
        ),
        ("/api/quotes/upload", {"name": "x.png", "data": "%%%"}, None, 400),
        ("/api/gyeolui/upload", {"files": [None]}, None, 400),
        ("/api/not-found", {}, None, 404),
    ],
)
def test_error_statuses(server, path, body, raw, expected):
    status, payload, _ = request(server, path, body, raw)
    assert status == expected
    assert isinstance(json.loads(payload)["error"], str)


def test_file_boundaries_and_source_types(server, app):
    sibling = app.out.parent / "ui-other"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret")
    assert request(server, "/out/../ui-other/secret.txt")[0] == 404
    assert request(server, "/ui/../config/rules.json")[0] == 404
    assert request(server, "/api/form_preview?form=../outside.hwpx")[0] == 404
    assert request(server, "/api/gyeolui/source?id=missing")[0] == 404
    source = app.root / "data_upload/gyeolui"
    source.mkdir()
    for name, data, ctype in [
        ("receipt.png", b"image", "image/png"),
        ("bundle.pdf", b"pdf", "application/pdf"),
    ]:
        p = source / name
        p.write_bytes(data)
        app.state["g"]["g0:expense"]["source"] = str(p)
        status, body, content_type = request(
            server, "/api/gyeolui/source?id=g0:expense"
        )
        assert (status, body, content_type) == (200, data, ctype)
    app.state["g"]["g0:expense"]["source"] = str(app.root / "config/rules.json")
    assert request(server, "/api/gyeolui/source?id=g0:expense")[0] == 404


def test_conflict_and_save_failure_are_not_success(server, app, monkeypatch):
    app.state["g"]["g0:expense"]["status"] = "running"
    assert (
        request(server, "/api/gyeolui/info", {"id": "g0:expense", "info": {}})[0] == 409
    )

    def fail(_):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(app.store, "save", fail)
    before = app.state["case"].copy()
    assert request(server, "/api/case", {"case": {"drafter": "failed"}})[0] == 500
    assert app.state["case"] == before
