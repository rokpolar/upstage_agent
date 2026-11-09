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


@pytest.mark.parametrize(
    "answer", sorted((ROOT / "answers_final").glob("*.json")), ids=lambda p: p.stem
)
def test_set_decisions_match_remote_answers(answer):
    expected = json.loads(answer.read_text(encoding="utf-8"))
    result = decide(
        expected.get("file_name", answer.stem + ".pdf"),
        copy.deepcopy(expected["quotes"]),
        expected.get("not_quote_pages", []),
    )
    for key in ("decision", "selected_vendor", "selected_total", "quote_count"):
        assert result[key] == expected[key]


def test_set_pipeline_writes_word_form_with_shared_engine(tmp_path):
    raw = json.loads((ROOT / "results/q01.json").read_text())
    from app.adapters.response import unwrap

    result = unwrap(raw)
    dst, report = write_form(
        "set-test",
        result,
        {"quote_count": 2, "selected_vendor": "(주)진주인쇄", "reason": "최저가"},
        tmp_path,
    )
    doc = Document(dst)
    text = "".join("".join(section.itertext()) for section in doc.sections.values())
    assert "진주인쇄" in text and "990,000" in text
    page = dst.with_suffix(".preview.html")
    assert page.is_file() and "진주인쇄" in page.read_text()
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
