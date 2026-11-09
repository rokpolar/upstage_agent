"""품의 agent 결과 + 담당자 입력으로 기관 양식의 지출품의서(PDF)와 결의 인계 JSON을 만든다.

    python tools/render_pumui.py <결과.json 또는 _resolved.json> <구매건.json> [--source 견적서.pdf] [--out out] [--approve 결재자]

- 양식은 config/rules.json form.template_dir 의 템플릿(page.html, parts.json)을 채운다.
  칸 배치·문구를 바꾸려면 코드가 아니라 템플릿을 고친다.
- 결재란·재원구분·단위사업명·사업기간·관련계획서는 견적서에 없으므로 구매건.json(담당자 입력)에서 받고,
  없으면 rules.json form 기본값을 쓴다.
- 인계 JSON(<case_id>_snapshot.json)은 결의 agent가 '품의 승인액'으로 쓰는 데이터다.
  --approve 없이 만들면 status=DRAFT, --approve 이름을 주면 APPROVED.
- 렌더 전에 하네스(harness.review)로 다시 검사한다. 답하지 않은 질문이나 보류가 있으면
  승인하지 않는다. 질문에는 tools/harness.py로 답한 뒤 그 _resolved.json으로 다시 실행한다.
"""

import argparse
import json
import sys
from pathlib import Path
from string import Template

from app.adapters.pdf import html_to_pdf, source_text
from app.adapters.render_pumui import build_html, build_snapshot
from app.adapters.response import unwrap
from app.config import config, fields, rules
from app.domain.harness import normalize, review


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("result")
    ap.add_argument("case")
    ap.add_argument("--source", help="원본 견적서 PDF (원문 대조·유효기간 검사)")
    ap.add_argument("--out", default="out")
    ap.add_argument(
        "--approve", metavar="결재자", help="승인 처리하고 APPROVED 인계 JSON을 만든다"
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    F = fields()

    data = json.loads(Path(a.result).read_text(encoding="utf-8"))
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    state = (
        data
        if "resolution_log" in data
        else {"result": normalize(unwrap(data) or data), "acknowledged": []}
    )
    r = state["result"]
    qs = review(
        r,
        source_text(a.source or state.get("source")),
        case,
        state.get("acknowledged", []),
    )
    hold = [config("questions")["hold_flag"]] if state.get("hold") else []
    r[F["flags"]] = [x["message"] for x in qs] + hold
    if r[F["flags"]]:
        print("확인이 필요한 항목:\n  - " + "\n  - ".join(r[F["flags"]]))
    if a.approve and r[F["flags"]]:
        sys.exit(
            "답하지 않은 질문이나 보류가 있어 승인하지 않습니다. tools/harness.py로 먼저 답해 주세요."
        )

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    name = Template(rules()["form"]["output_name"]).safe_substitute(
        case_id=case["case_id"]
    )
    page = out / f"{name}.html"
    page.write_text(build_html(r, case), encoding="utf-8")
    pdf = out / f"{name}.pdf"
    html_to_pdf(page, pdf)
    snap = out / f"{case['case_id']}_snapshot.json"
    snap.write_text(
        json.dumps(
            build_snapshot(r, case, a.approve, state.get("resolution_log", [])),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"지출품의서: {pdf}\n인계 JSON: {snap}")
