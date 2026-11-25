import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

from app.adapters.files import write_json
from app.adapters.n8n import WorkflowError
from app.adapters.studio_errors import InputError, NotQuote, StudioError
from app.config import ROOT
from app.domain.procurement import evaluate

sys.path.insert(0, str(ROOT / "tools"))
import run_set


def page(
    number, vendor, total, *, rep="", supply=None, vat=None, flags=(), file="견적.pdf"
):
    return {
        "file": file,
        "page": number,
        "category": None,
        "quote": {
            "vendor_name": vendor,
            "vendor_representative": rep,
            "total_amount": total,
            "supply_amount": supply,
            "vat_amount": vat,
            "items": [{"description": "복사용지"}],
            "review_flags": list(flags),
        },
    }


def test_case_form_uses_the_selected_page(tmp_path, monkeypatch):
    rows = [page(1, "가", 5_000), page(2, "가", 2_000), page(3, "나", 3_000)]
    case = evaluate(rows)["cases"][0]
    assert case["result"]["selected_quote"] == {"file": "견적.pdf", "page": 2}
    monkeypatch.setattr(run_set, "ROOT", tmp_path)
    monkeypatch.setattr(
        run_set, "cfg", lambda: {"out_dir": ".", "decisions": {"ok": "작성 가능"}}
    )

    def capture(case_id, raw, result, out_dir):
        assert raw == rows[1]["quote"]
        assert raw["total_amount"] == result["selected_total"] == 2_000
        return out_dir / "test.docx", out_dir / "test.pdf", "mock", {"missing": []}

    monkeypatch.setattr(run_set, "write_form", capture)
    run_set.run_case("case_A", (case["result"], case["rows"]))
    assert (tmp_path / "case_A.json").is_file()


@pytest.mark.parametrize("name", [None, "", "   ", " 검토 필요 ", "확인 불가"])
def test_missing_lowest_vendor_cannot_be_selected(name):
    result = evaluate([page(1, name, 1100), page(2, "나", 2200)])["cases"][0]["result"]
    assert result["decision"] == "담당자 확인 필요"
    assert result["selected_quote"] is None
    assert result["selected_total"] is None
    assert result["selected_vendor"] == "확인 불가"
    assert result["needs_review"] == ["업체명 확인 불가"]


@pytest.mark.parametrize(
    ("other_vendor", "rep", "decision"),
    [
        ("(주) 가", "홍길동", "작성 가능"),
        ("나", "홍길동(인)", "담당자 확인 필요"),
        ("나", "홍길동(印)", "담당자 확인 필요"),
    ],
)
def test_representative_matches_only_across_distinct_vendors(
    other_vendor, rep, decision
):
    result = evaluate(
        [page(1, "가", 1100, rep="홍길동"), page(2, other_vendor, 2200, rep=rep)]
    )["cases"][0]["result"]
    assert result["decision"] == decision
    assert ("대표자 동일" in result["needs_review"]) == (decision == "담당자 확인 필요")


def test_only_verified_arithmetic_can_trigger_requote():
    note = "품목 금액 합계와 공급가액 불일치"
    first = page(1, "가", 16, supply=15, vat=1, flags=[note])
    second = page(2, "나", 200)
    result = evaluate([first, second])["cases"][0]["result"]
    assert result["decision"] == "작성 가능"
    assert result["quotes"][0]["review_flags"] == [note]
    assert result["needs_review"] == []

    first["quote"]["total_amount"] = 17
    result = evaluate([first, second])["cases"][0]["result"]
    assert result["decision"] == "재견적 필요"
    assert result["needs_review"] == ["계산 불일치"]


def make_pdf(path, width):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    writer.write(path)


def test_split_cache_tracks_source_bytes_and_fresh_rebuilds(tmp_path):
    pdf = tmp_path / "quote.pdf"
    folder = tmp_path / "split"
    make_pdf(pdf, 100)
    first = run_set.split(pdf, folder)[0][2]
    stat = pdf.stat()
    make_pdf(pdf, 200)
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    second = run_set.split(pdf, folder)[0][2]
    assert first != second
    assert PdfReader(second).pages[0].mediabox.width == 200

    make_pdf(second, 300)
    rebuilt = run_set.split(pdf, folder, fresh=True)[0][2]
    assert rebuilt == second
    assert PdfReader(rebuilt).pages[0].mediabox.width == 200


@pytest.mark.parametrize(
    "error",
    [
        NotQuote("기타"),
        WorkflowError("모델 JSON 오류"),
        InputError("입력 제한"),
        StudioError(503, "", "unavailable"),
    ],
)
def test_failed_refresh_never_reuses_old_quote(tmp_path, error):
    pdf = tmp_path / "quote.pdf"
    make_pdf(pdf, 100)
    source = run_set.split(pdf, tmp_path / "split")[0]
    out = tmp_path / "results"
    old = out / source[2].parent.name / "page_1.json"
    stale = page(1, "기존", 1000)["quote"]
    write_json(old, stale)

    def reject(path, pending):
        assert pending != old.parent
        write_json(Path(pending) / "page_1.json", stale)
        raise error

    row = run_set.read_page(source, out, True, SimpleNamespace(run_file=reject))
    assert row["quote"] is None
    assert ("error" in row) == (not isinstance(error, NotQuote))
    assert not old.exists()

    replacement = page(1, "신규", 800)["quote"]
    row = run_set.read_page(
        source, out, False, SimpleNamespace(run_file=lambda *_: (replacement, "job"))
    )
    assert row["quote"] == replacement
    assert old.is_file()


def test_failed_page_is_reported_and_holds_the_affected_case():
    first = [page(1, "가", 1100), page(2, "나", 2200)]
    second = [page(1, "다", 3300, file="PC.pdf"), page(2, "라", 4400, file="PC.pdf")]
    for row in second:
        row["quote"]["items"] = [{"description": "노트북"}]
    failure = {"file": first[0]["file"], "page": 3, "error": "모델 JSON 오류"}
    rows = [*first, *second, {**failure, "quote": None, "category": "처리 실패"}]
    output = evaluate(rows)
    by_file = {case["result"]["file"]: case["result"] for case in output["cases"]}
    assert by_file["견적.pdf"]["decision"] == "담당자 확인 필요"
    assert by_file["견적.pdf"]["selected_quote"] is None
    assert by_file["견적.pdf"]["failed_pages"] == [failure]
    assert by_file["PC.pdf"]["decision"] == "작성 가능"
    assert output["failed_pages"] == [failure]
