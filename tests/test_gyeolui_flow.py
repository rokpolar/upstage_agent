"""부분 저장과 품의 생성부터 결의 DOCX까지의 연결을 검증한다."""

import base64
import copy
import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app.adapters.docx import NS, DocxForm
from app.adapters.files import write_json
from app.domain.gyeolui import gfields
from app.web.routes import Routes


def pdf_bytes(width):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


@pytest.fixture
def generated(app, monkeypatch):
    def preview(path, pdf=False):
        path = Path(path)
        page = path.with_suffix(".preview.html")
        page.write_text("<p>품의서</p>")
        if pdf:
            out = path.with_suffix(".pdf")
            out.write_bytes(pdf_bytes(100))
            return out
        return page

    monkeypatch.setattr("app.services.forms.preview", preview)
    Routes(app).post("/api/select", {"ids": ["0:q01"], "pick": "0:q01"})
    result = app.do_fill("forms/web/cne/품의서양식.hwpx", "0:q01")
    assert result["ok"]
    return copy.deepcopy(app.state["handoff"])


def test_partial_info_update_preserves_hidden_values_and_clears_blanks(app, make_app):
    api = Routes(app)
    gid = "g0:expense"
    hidden = dict(
        department="총무부",
        doc_no="EXP-1",
        representative="담당자",
        bank_account="테스트 계좌",
        funding_source="자부담",
        request_number="REQ-1",
        request_date="2026-11-01",
    )
    api.post(
        "/api/gyeolui/info",
        {
            "id": gid,
            "info": {**hidden, "account": "이전 계정", "description": "이전 적요"},
        },
    )
    api.post(
        "/api/gyeolui/info",
        {
            "id": gid,
            "info": {
                "account": "",
                "description": "새 적요",
                "payment_method": "계좌이체",
                "attachments": "영수증",
                "resolution_date": "2026-11-10",
                "drafter": "담당자",
            },
        },
    )
    restored = make_app().g_summary(gid)
    assert restored["info_saved"] and "account" not in restored["info"]
    assert restored["info"]["description"] == "새 적요"
    assert all(restored["info"][k] == v for k, v in hidden.items())


@pytest.mark.parametrize("change", ["answer", "case", "select"])
def test_pumui_changes_invalidate_only_current_handoff(
    app, generated, make_app, change
):
    gid = "g0:expense"
    app.execute(lambda: app.state["g"][gid].update(link=copy.deepcopy(generated)))
    files = [Path(generated["files"][k]) for k in ("pdf", "hwpx")]
    api = Routes(app)
    if change == "answer":
        question = app.summary("0:q04")["questions"][0]
        api.post(
            "/api/answer", {"id": "0:q04", "key": question["key"], "value": "__HOLD__"}
        )
    elif change == "case":
        api.post("/api/case", {"case": {**app.state["case"], "purpose": "수정한 용도"}})
    else:
        api.post(
            "/api/select",
            {"ids": ["0:q01"], "pick": "0:q01", "reason": "수정한 선정 사유"},
        )
    assert app.state["handoff"] is None
    assert all(q.get("fill") is None for q in app.state["quotes"].values())
    assert all(p.is_file() for p in files)
    restored = make_app()
    assert Routes(restored).init()["gyeolui"]["handoff"] is None
    assert restored.state["g"][gid]["link"] == generated


def test_failed_pumui_update_preserves_handoff(app, generated):
    with pytest.raises(ValueError):
        Routes(app).post("/api/select", {"ids": ["0:q01"], "pick": "missing"})
    assert app.state["handoff"] == generated


def test_regeneration_replaces_handoff_and_leaves_old_files(app, generated):
    Routes(app).post(
        "/api/case", {"case": {**app.state["case"], "title": "새 품의 제목"}}
    )
    assert app.state["handoff"] is None
    app.do_fill("forms/web/cne/품의서양식.hwpx", "0:q01")
    assert app.state["handoff"]["title"] == "새 품의 제목"
    assert app.state["handoff"]["files"]["pdf"] != generated["files"]["pdf"]
    assert Path(generated["files"]["pdf"]).is_file()


@pytest.mark.parametrize(
    "attach,compare,expected_pages,linked",
    [
        (False, False, [200], False),
        (False, True, [200], True),
        (True, False, [100, 200], True),
        (True, True, [100, 200], True),
    ],
)
def test_handoff_pipeline_to_docx(
    app, generated, monkeypatch, make_app, attach, compare, expected_pages, linked
):
    h = generated
    R, C = gfields()["request"], gfields()["receipt"]
    raw = {
        "kind": "gyeolui",
        "request": {
            R["number"]: h["doc_no"],
            R["title"]: h["title"],
            R["amount"]: h["total"],
            R["vendor"]: h["vendor"],
            R["date"]: h["draft_date"],
        },
        "receipts": [
            {
                C["type"]: "신용카드 매출전표",
                C["vendor"]: h["vendor"],
                C["total"]: h["total"],
                C["supply"]: h["supply"],
                C["vat"]: h["vat"],
                C["date"]: "2026-11-10",
                C["method"]: "계좌이체",
            }
        ],
        "studio": {
            "reports": {"comparison_report": "## 검토\n\n- **일치**"},
            "decisions": {},
            "draft": "",
        },
    }
    captured = []

    def run_file(path, out_dir, progress=None, kind="pumui", on_job=None):
        assert kind == "gyeolui"
        captured.extend(int(page.mediabox.width) for page in PdfReader(path).pages)
        if on_job:
            on_job("pipeline-job")
        if progress:
            progress(["upload", "draft"], {}, True)
        write_json(out_dir / f"{path.stem}.json", raw)
        return raw, "pipeline-job"

    monkeypatch.setattr(app.studio, "run_file", run_file)
    api = Routes(app)
    reply = api.post(
        "/api/gyeolui/upload",
        {
            "files": [
                {
                    "name": "receipt.pdf",
                    "data": base64.b64encode(pdf_bytes(200)).decode(),
                }
            ],
            "with_pumui": attach,
            "link": compare,
        },
    )
    app.close()
    gid = reply["item"]["id"]
    item = app.g_summary(gid)
    assert item["status"] == "done" and not item["questions"]
    assert captured == expected_pages and item["linked"] is linked
    assert item["base"]["source"] == ("linked" if linked else "request")
    assert item["amount"] == h["total"] and item["payee"] == h["vendor"]
    api.post(
        "/api/gyeolui/info",
        {
            "id": gid,
            "info": {
                "account": "홍보비",
                "description": "행사 홍보물",
                "payment_method": "계좌이체",
                "attachments": "품의서, 영수증",
                "resolution_date": "2026-11-10",
                "drafter": "담당자",
            },
        },
    )
    result = api.post("/api/gyeolui/fill", {"id": gid})
    path = app.out / result["report"]["files"]["docx"].removeprefix("/out/")
    doc = DocxForm(path)
    text = "".join(doc.doc.xpath(".//w:t/text()", namespaces=NS))
    assert h["vendor"] in text and "990,000" in text and "담당자" in text
    assert make_app().g_summary(gid)["fill"] == result["report"]["files"]
