import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.adapters.files import confined, decode_data
from app.adapters.hwpx import Document
from app.adapters.pdf import ConversionError, html_to_pdf
from app.web.routes import Routes


def test_sibling_and_symlink_traversal_are_blocked(tmp_path):
    base = tmp_path / "ui"
    base.mkdir()
    sibling = tmp_path / "ui-other"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret")
    (base / "link").symlink_to(sibling, target_is_directory=True)
    for path in (
        "../ui-other/secret.txt",
        "link/secret.txt",
        str(sibling / "secret.txt"),
    ):
        with pytest.raises(FileNotFoundError):
            confined(base, path)


@pytest.mark.parametrize("data", ["invalid%%%", "a", [], None])
def test_invalid_base64(data):
    with pytest.raises(ValueError):
        decode_data(data)


def test_missing_browser_raises_normal_exception(monkeypatch):
    monkeypatch.setattr("app.adapters.pdf.os.environ.get", lambda *_: None)
    monkeypatch.setattr(
        "app.adapters.pdf.rules", lambda: {"runtime": {"pdf_browsers": []}}
    )
    with pytest.raises(ConversionError):
        html_to_pdf("unused.html", "unused.pdf")


def test_concurrent_pdf_conversions_have_separate_profiles(tmp_path, monkeypatch):
    profiles = []

    def run(command, **kwargs):
        profile = next(
            x.removeprefix("--user-data-dir=")
            for x in command
            if x.startswith("--user-data-dir=")
        )
        profiles.append(profile)
        target = next(
            x.removeprefix("--print-to-pdf=")
            for x in command
            if x.startswith("--print-to-pdf=")
        )
        Path(target).write_bytes(b"pdf")

    monkeypatch.setattr("app.adapters.pdf.subprocess.run", run)
    monkeypatch.setattr(
        "app.adapters.pdf.os.environ.get",
        lambda key, default=None: str(tmp_path) if key == "PDF_BROWSER" else default,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda i: html_to_pdf(tmp_path / "input.html", tmp_path / f"{i}.pdf"),
                range(2),
            )
        )
    assert len(set(profiles)) == 2
    assert all(not Path(p).exists() for p in profiles)


def test_form_paths_and_registered_source_bounds(app):
    with pytest.raises(FileNotFoundError):
        app.form_path("../outside.hwpx")
    app.state["g"]["g0:expense"]["source"] = str(app.root / "config/rules.json")
    with pytest.raises(FileNotFoundError):
        app.source_path("g0:expense")


def test_uploaded_forms_are_unique_and_preview_tracks_content(app):
    api = Routes(app)
    source = next((app.root / "forms/web").glob("**/*.hwpx"))
    body = dict(name="same.hwpx", data=base64.b64encode(source.read_bytes()).decode())
    first, second = [api.post("/api/upload", body)["path"] for _ in range(2)]
    assert first != second
    before = app.form_preview(first)
    doc = Document(app.root / first)
    root = next(iter(doc.sections.values()))
    root.set("test-version", "changed")
    doc.save(app.root / first)
    assert app.form_preview(first) != before


def fake_preview(path, pdf=False):
    path = Path(path)
    html = path.with_suffix(".preview.html")
    html.write_text("<p>preview</p>")
    target = path.with_suffix(".pdf") if pdf else html
    if pdf:
        target.write_bytes(b"test pdf")
    return target


def test_real_hwpx_fill_handoff_and_restore_links(app, make_app, monkeypatch):
    monkeypatch.setattr("app.services.forms.preview", fake_preview)
    form = "forms/web/cne/품의서양식.hwpx"
    report = app.do_fill(form, "0:q01")
    assert report["ok"] and report["handoff"]["has_pdf"]
    filled = app.out / report["report"]["files"]["hwpx"].removeprefix("/out/")
    doc = Document(filled)
    assert doc.sections
    text = "".join("".join(section.itertext()) for section in doc.sections.values())
    assert "990,000" in text and "진주인쇄" in text
    restored = make_app()
    assert restored.summary("0:q01")["fill"]["files"] == report["report"]["files"]
    assert restored.state["handoff"]["files"]["pdf"].endswith(".pdf")


def test_form_generation_detects_change_while_unlocked(app, monkeypatch):
    def preview(path, pdf=False):
        app.execute(lambda: app.state["case"].update(drafter="changed during render"))
        return fake_preview(path, pdf)

    monkeypatch.setattr("app.services.forms.preview", preview)
    form = (
        next((app.root / "forms/web").glob("**/*.hwpx"))
        .relative_to(app.root)
        .as_posix()
    )
    from app.services.workspace import ConflictError

    with pytest.raises(ConflictError):
        app.do_fill(form, "0:q01")
    assert app.summary("0:q01")["fill"] is None


def test_hold_blocks_form_generation(app):
    qid = "0:q04"
    q = app.summary(qid)["questions"][0]
    Routes(app).post("/api/answer", dict(id=qid, key=q["key"], value="__HOLD__"))
    form = (
        next((app.root / "forms/web").glob("**/*.hwpx"))
        .relative_to(app.root)
        .as_posix()
    )
    assert app.do_fill(form, qid)["ok"] is False
