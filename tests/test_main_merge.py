"""원격 main 개선이 분리된 모듈에서도 유지되는지 검사한다."""

import base64
import io

from pypdf import PdfReader, PdfWriter

from app.adapters.files import write_json
from app.adapters.studio_errors import NotQuote
from app.config import fields, rules
from app.web.routes import Routes


def test_multi_page_quote_split(app):
    source = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=200, height=200)
    writer.write(source)
    result = Routes(app).post(
        "/api/quotes/split",
        {"name": "quotes.pdf", "data": base64.b64encode(source.getvalue()).decode()},
    )
    assert [p["name"] for p in result["pages"]] == ["quotes_p1.pdf", "quotes_p2.pdf"]
    for page in result["pages"]:
        assert len(PdfReader(io.BytesIO(base64.b64decode(page["data"]))).pages) == 1


def test_non_quote_is_skipped_and_removed(app, monkeypatch):
    def reject(*args, **kwargs):
        raise NotQuote("영수증")

    monkeypatch.setattr(app.studio, "run_file", reject)
    result = Routes(app).post(
        "/api/quotes/upload",
        {"name": "receipt.png", "data": base64.b64encode(b"image").decode()},
    )
    assert result["skipped"] and result["category"] == "영수증"
    assert not list((app.root / "data_upload").glob("*receipt.png"))
    assert len(app.state["quotes"]) == 2


def test_empty_cover_page_is_skipped(app, monkeypatch):
    def cover(path, out_dir, *args, **kwargs):
        raw = {fields()["items"]: [], fields()["total"]: None}
        write_json(out_dir / f"{path.stem}.json", raw)
        return raw, "cover-job"

    monkeypatch.setattr(app.studio, "run_file", cover)
    result = Routes(app).post(
        "/api/quotes/upload",
        {"name": "cover.png", "data": base64.b64encode(b"image").decode()},
    )
    assert result["skipped"] and result["category"] == "표지·안내문"
    assert not list((app.root / "results_upload").glob("*cover.json"))


def test_cross_quote_warnings_reach_compare_api(app):
    F = fields()
    for qid, vendor in [("0:q01", "업체 A"), ("0:q04", "업체 B")]:
        r = app.state["quotes"][qid]["state"]["result"]
        r[F["vendor_name"]] = vendor
        r[F["vendor_representative"]] = "대표이사 홍길동"
        r[F["total"]] = 10000
    comparison = Routes(app).post("/api/compare", {"ids": ["0:q01", "0:q04"]})
    assert {w["kind"] for w in comparison["warnings"]} == {
        "same_representative",
        "same_lowest_total",
    }


def test_preview_uses_form_page_settings_and_print_mode(app):
    from app.adapters.hwpx_preview import to_html

    page = to_html(app.root / "forms/web/cne/품의서양식.hwpx")
    assert "@page" in page and "setTimeout(print, 300)" in page
    assert "preview" in rules() and "font-family" in page
