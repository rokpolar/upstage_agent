"""지출결의서 DOCX: 고정 양식을 결의 결과로 채우고 내려받는다."""

import json
import threading
import zipfile
from urllib.parse import quote

import pytest
from lxml import etree

from app.adapters.docx import NS, DocxForm, cell_text, key, para_text, w
from app.config import ROOT, config, fields
from app.web.routes import Routes
from app.web.server import create_server
from tests.test_http import request

TEMPLATE = ROOT / "forms/expense/물품구매_지출결의서_양식.docx"
FULL = {
    "account": "행사운영비",
    "description": "노사협의회 워크숍 행사 케이터링 구매",
    "amount_text": "금 이백삼십팔만사백원정 (￦2,380,400)",
    "payee": "맛누리케터링",
    "payment_method": "국민카드 (법인)",
    "attachments": "품의서, 신용카드 매출전표, 거래명세서",
    "resolution_date_text": "2026년 11월 14일",
    "drafter": "최민호",
    "drafter_line": "최민호 (서명) 기안부서: 총무부",
}


def read(path):
    """저장한 DOCX → ({라벨: 값 칸 글자}, [문단 글자], zip 이름 목록)."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        doc = etree.fromstring(z.read("word/document.xml"))
    cells = {}
    for tr in doc.iter(w("tr")):
        tcs = tr.findall("w:tc", NS)
        if len(tcs) == 2:
            cells[key(cell_text(tcs[0]))] = cell_text(tcs[1])
    body = doc.find("w:body", NS)
    paras = [para_text(p) for p in body.iter(w("p"))]
    return cells, paras, names


def test_template_is_fixed_docx():
    cfg = config("docx_gyeolui")
    assert (ROOT / cfg["template"]) == TEMPLATE and TEMPLATE.is_file()


def test_fill_all_fields(tmp_path):
    doc = DocxForm(TEMPLATE)
    report = doc.fill(FULL, config("docx_gyeolui"))
    out = doc.save(tmp_path / "out.docx", config("docx_gyeolui")["drop_parts"])
    cells, paras, names = read(out)
    assert cells["계정과목"] == "행사운영비"
    assert cells["지급금액"] == "금 이백삼십팔만사백원정 (￦2,380,400)"
    assert (
        cells["지급처"] == "맛누리케터링"
        and "품의서, 신용카드 매출전표, 거래명세서" in paras
    )
    assert "2026년 11월 14일" in paras
    label_table = (
        etree.fromstring(zipfile.ZipFile(out).read("word/document.xml"))
        .find("w:body", NS)
        .findall("w:tbl", NS)[0]
    )
    value_runs = [
        r
        for tr in label_table.findall("w:tr", NS)
        if len(tr.findall("w:tc", NS)) == 2
        for r in tr.findall("w:tc", NS)[1].iter(w("r"))
    ]
    assert value_runs and all(
        r.find("w:rPr/w:b", NS) is None for r in value_runs
    )  # 값은 굵게 하지 않는다
    assert all(
        r.find("w:rPr/w:sz", NS).get(w("val")) == "22"
        for r in value_runs
        if r.find("w:rPr", NS) is not None
    )
    assert any("최민호" in p and "서명" in p for p in paras)
    assert "품의서, 신용카드 매출전표, 거래명세서" in paras
    assert not report["missing"] and not report["notes"]
    # 옛 첫 쪽 그림은 빼고, 관계 목록에도 남기지 않는다. [Content_Types].xml은 맨 앞
    assert "docProps/thumbnail.jpeg" not in names and names[0] == "[Content_Types].xml"
    with zipfile.ZipFile(out) as z:
        assert b"thumbnail" not in z.read("_rels/.rels")
    # 결재란(기안·검토·승인)은 비워 둔다
    approvals = [
        cell_text(tc)
        for tc in etree.fromstring(zipfile.ZipFile(out).read("word/document.xml"))
        .find("w:body/w:tbl", NS)
        .findall("w:tr", NS)[1]
        .findall("w:tc", NS)
    ]
    assert approvals[-6:] == [""] * 6


def test_missing_values_stay_blank_and_reported(tmp_path):
    values = {
        k: v
        for k, v in FULL.items()
        if k not in ("account", "drafter", "drafter_line", "resolution_date_text")
    }
    doc = DocxForm(TEMPLATE)
    report = doc.fill(values, config("docx_gyeolui"))
    cells, paras, _ = read(doc.save(tmp_path / "out.docx"))
    assert cells["계정과목"] == ""
    assert {m["role"] for m in report["missing"]} == {
        "account",
        "drafter",
        "resolution_date",
    }
    assert not any("2026년" in p for p in paras)  # 날짜 줄은 양식 그대로


def test_company_item_rows_expand_without_losing_details(tmp_path):
    doc = DocxForm(TEMPLATE)
    f, i = fields(), fields()["item"]
    items = [
        {
            i["description"]: f"확인 품목 {n}",
            i["quantity"]: n,
            i["unit_price"]: 100,
            i["amount"]: n * 100,
        }
        for n in range(1, 9)
    ]
    doc.fill_items({f["items"]: items, f["total"]: 3960})
    path = doc.save(tmp_path / "items.docx")
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    text = "".join(root.itertext())
    assert all(f"확인 품목 {n}" in text for n in range(1, 9))
    assert "3,960" in text
    assert "재무책임자" in text and "대표이사" in text


def test_fill_from_workflow_result_and_download(app, monkeypatch):
    api = Routes(app)
    gid = "g0:expense"
    blocked = api.post("/api/gyeolui/fill", {"id": gid})
    assert (
        blocked["ok"] is False and blocked["blocked"]
    )  # 지급금액 불일치를 정하기 전에는 만들지 않는다
    question = app.g_summary(gid)["questions"][0]
    api.post(
        "/api/gyeolui/answer", {"id": gid, "key": question["key"], "value": 2380400}
    )
    info = {
        **app.g_summary(gid)["info"],
        "account": "행사운영비",
        "resolution_date": "2026-11-14",
        "drafter": "최민호",
        "drafter_line": "최민호 (서명) 기안부서: 총무부",
    }
    api.post("/api/gyeolui/info", {"id": gid, "info": info})
    r = api.post("/api/gyeolui/fill", {"id": gid})
    assert r["ok"] and r["report"]["files"]["name"].endswith("_지출결의서.docx")
    path = app.out / r["report"]["files"]["docx"].removeprefix("/out/")
    cells, paras, _ = read(path)
    assert (
        cells["지급금액"] == "금 이백삼십팔만사백원정 (￦2,380,400)"
    )  # 검증에서 고른 금액
    assert cells["지급처"] == "맛누리케터링" and cells["계정과목"] == "행사운영비"
    assert "2026년 11월 14일" in paras
    assert not r["report"]["missing"]
    record = json.loads(
        next(path.parent.glob("*.report.json")).read_text(encoding="utf-8")
    )
    assert record["form_input"]["values"]["amount"] == 2380400
    assert app.g_summary(gid)["fill"]["docx"] == r["report"]["files"]["docx"]
    docx_download = api.post("/api/gyeolui/download", {"id": gid, "format": "docx"})
    assert docx_download == {"url": r["report"]["files"]["docx"], "name": path.name}
    with pytest.raises(ValueError):
        api.post("/api/gyeolui/download", {"id": gid, "format": "zip"})

    calls = []

    def convert(source):
        calls.append(source)
        assert read(source)[0]["지급금액"] == cells["지급금액"]
        pdf = source.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-test")
        return pdf, "mock"

    monkeypatch.setattr("app.adapters.docx_pdf.to_pdf", convert)
    pdf_download = api.post("/api/gyeolui/download", {"id": gid, "format": "pdf"})
    assert pdf_download["name"] == path.with_suffix(".pdf").name
    assert (
        app.out / pdf_download["url"].removeprefix("/out/")
    ).read_bytes() == b"%PDF-test"
    assert (
        api.post("/api/gyeolui/download", {"id": gid, "format": "pdf"}) == pdf_download
    )
    assert len(calls) == 1


def test_http_docx_download_and_template_preview(app):
    server = create_server(app, ("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, body, _ = request(server, "/api/gyeolui/template_preview")
        url = json.loads(body)["url"]
        status, page, ctype = request(
            server, quote(url)
        )  # 브라우저처럼 한글 경로를 인코딩해 보낸다
        assert (
            status == 200
            and "계정과목" in page.decode()
            and ctype.startswith("text/html")
        )
        init = json.loads(request(server, "/api/init")[1])
        assert init["gyeolui"]["template"] == "물품구매_지출결의서_양식.docx"
        doc = DocxForm(TEMPLATE)
        doc.fill(FULL, config("docx_gyeolui"))
        out = doc.save(app.out / "dl.docx")
        status, data, ctype = request(server, "/out/dl.docx")
        assert (
            status == 200
            and ctype
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert data == out.read_bytes()
    finally:
        server.shutdown()
        server.server_close()
