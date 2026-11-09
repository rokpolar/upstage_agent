"""품의 agent 결과를 검사하고, 이상한 점을 담당자에게 질문으로 물어본 뒤 답을 반영한다.

    python tools/harness.py results/q04.json --source samples/q04.pdf --case examples/case_q01.json
    python tools/harness.py results/q04.json --no-input      # 질문만 JSON으로 저장 (웹·n8n 연동용)

원칙
- 모델은 적힌 값을 읽기만 한다. 계산·규칙 검사는 여기서 코드로 한다.
- 금액을 조용히 고치지 않는다. 바꿀 값이 있으면 근거와 제안값을 보여주고 담당자가 고른다.
- 답을 반영할 때마다 처음부터 다시 검사한다. 질문이 남아 있으면 승인할 수 없다.
- 규칙(세율·오차·패턴)은 config/rules.json, 키 이름은 config/fields.json,
  질문 문구는 config/questions.json, 비목은 config/categories.json에서 읽는다.

출력: <out>/<이름>_resolved.json
  result          답을 반영한 품의서 데이터
  questions       아직 답하지 않은 질문 (비어 있어야 승인 가능)
  resolution_log  누가 언제 어떤 질문에 무엇을 골랐는지
"""

import argparse
import json
import sys
from pathlib import Path

from app.adapters.pdf import source_text
from app.config import ROOT, config
from app.domain.harness import INPUT, coerce, normalize, recheck, resolve


def ask_cli(state, text, case, who):
    cli = config("questions")["cli"]
    while recheck(state, text, case):
        if state.get("hold"):
            break
        qn = state["questions"][0]
        print("\n" + cli["question"].format(message=qn["message"]))
        if qn["evidence"]:
            print(f"   {qn['evidence']}")
        for n, o in enumerate(qn["options"], 1):
            print(f"   {n}) {o['label']}")
        choice = input(cli["choose"]).strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(qn["options"]):
            print("   " + cli["retry"])
            continue
        value = qn["options"][int(choice) - 1]["value"]
        if value == INPUT:
            value = coerce(qn["field"], input(cli["input"]))
        resolve(state, value, who)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("result", help="품의 agent 결과 JSON (또는 이전 _resolved.json)")
    ap.add_argument(
        "--source", help="원본 견적서 PDF (텍스트 PDF면 원문 대조·유효기간 검사)"
    )
    ap.add_argument("--case", help="담당자 입력(구매건) JSON")
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--who", default="담당자")
    ap.add_argument("--no-input", action="store_true", help="묻지 않고 질문만 저장")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    data = json.loads(Path(a.result).read_text(encoding="utf-8"))
    if "resolution_log" in data:  # 이전에 답하던 것을 이어서
        state = data
    else:
        from app.adapters.response import unwrap

        state = {
            "result": normalize(unwrap(data) or data),
            "questions": [],
            "resolution_log": [],
            "acknowledged": [],
            "hold": False,
            "source": a.source,
        }
    case = json.loads(Path(a.case).read_text(encoding="utf-8")) if a.case else {}
    text = source_text(a.source or state.get("source"))

    if a.no_input:
        recheck(state, text, case)
    else:
        ask_cli(state, text, case, a.who)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(a.result).stem.replace("_resolved", "")
    path = out / f"{stem}_resolved.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    left = len(state["questions"])
    cli = config("questions")["cli"]
    status = (
        cli["status_hold"]
        if state.get("hold")
        else (cli["status_ok"] if not left else cli["status_left"].format(n=left))
    )
    print(
        "\n"
        + cli["summary"].format(
            status=status, answered=len(state["resolution_log"]), path=path
        )
    )
