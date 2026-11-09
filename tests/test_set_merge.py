"""원격의 세트 판정·Word 품의서 기능과 새 패키지의 호환성을 확인한다."""

import copy
import json
import subprocess
import sys

import pytest

from app.adapters.docx_form import Document
from app.config import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from run_set import write_form
from set_decision import decide


def quote(vendor, total, rep="", items=None, supply=None, vat=None):
    items = items or [{"quantity": 1, "unit_price": supply or total, "estimated_amount": supply or total, "calculation_basis": ""}]
    return {"file": "x.pdf", "page": 1, "vendor_name": vendor, "vendor_representative": rep, "quote_date": "",
            "items": items, "supply_amount": supply, "vat_amount": vat, "total_amount": total, "review_flags": []}


@pytest.mark.parametrize(
    "quotes, expected",
    [
        ([quote("가", 1100, "A", supply=1000, vat=100), quote("나", 2200, "B", supply=2000, vat=200)],
         {"decision": "작성 가능", "selected_vendor": "가", "selected_total": 1100, "needs_review": []}),
        ([quote("가", 1200, "A", supply=1000, vat=100), quote("나", 2200, "B", supply=2000, vat=200)],
         {"decision": "재견적 필요", "selected_vendor": "가", "selected_total": 1200, "needs_review": ["계산 불일치"]}),
        ([quote("가", 1100, "A"), quote("나", 1100, "B"), quote("다", 2200, "C")],
         {"decision": "담당자 확인 필요", "selected_vendor": "확인 불가", "selected_total": None, "needs_review": ["최저가 동률"]}),
        ([quote("가", None, "A"), quote("나", 1100, "B")],
         {"decision": "담당자 확인 필요", "selected_vendor": "확인 불가", "selected_total": None, "needs_review": ["합계 확인 불가"]}),
        ([quote("가", 1100, "송"), quote("나", 1200, "송")],
         {"decision": "담당자 확인 필요", "selected_vendor": "가", "selected_total": 1100, "needs_review": ["대표자 동일"]}),
        ([quote("가", 1100, "A")], {"decision": "작성 중지"}),
    ],
)
def test_set_decision_rules(quotes, expected):
    result = decide("x.pdf", copy.deepcopy(quotes))
    for k, v in expected.items():
        assert result[k] == v, k


def test_answer_file_follows_submission_template():
    answers = sorted((ROOT / "answers_final").glob("*_answer.json"))
    assert answers
    for path in answers:
        a = json.loads(path.read_text(encoding="utf-8"))
        assert list(a) == ["task", "unit", "output_keys", "decision_values", "cases"]
        for c in a["cases"]:
            assert set(c) == {"case_id", "file", "documents", "answer"}
            assert list(c["answer"]) == a["output_keys"]
            assert c["answer"]["decision"] in a["decision_values"]
            names = c["file"] if isinstance(c["file"], list) else [c["file"]]
            for n in names:
                assert (ROOT / "data_team/견적서예시" / n).is_file(), n


def test_set_pipeline_writes_word_form_with_shared_engine(tmp_path):
    raw = json.loads((ROOT / "results/q01.json").read_text())
    from app.adapters.response import unwrap

    result = unwrap(raw)
    dst, _pdf, _way, report = write_form(
        "set-test",
        result,
        {"quote_count": 2, "selected_vendor": "(주)진주인쇄", "reason": "최저가"},
        tmp_path,
    )
    doc = Document(dst)
    text = "".join("".join(section.itertext()) for section in doc.sections.values())
    assert "진주인쇄" in text and "990,000" in text
    assert _pdf.is_file()
    assert isinstance(report["missing"], list)


def test_run_set_cli_help():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/run_set.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT.parent,
    )
    assert result.returncode == 0, result.stderr
    assert "--no-form" in result.stdout
