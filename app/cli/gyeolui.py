"""결의 agent: 증빙 묶음의 Studio 결과를 검사하고, 사람이 정할 것만 물은 뒤, 지출결의서에 넣을 값을 만든다.

    python tools/gyeolui.py examples/gyeolui/09_맛누리케터링_결의묶음.json           # 확인 항목만 보기
    python tools/gyeolui.py <결과.json> --ask                                        # 터미널에서 답하기
    python tools/gyeolui.py <결과.json> --link out/ui/<시각>/<건>.handoff.json       # 앱에서 쓴 품의서와 대조

증빙 묶음 = 결제 품의서 + 영수증·거래명세서·세금계산서 + (회사 결제 규정) + (지출결의서 양식)을 한 파일로 묶은 것.
Studio 결의 워크플로가 묶음을 쪼개 분류·추출하고 비교 보고서·판정·기입 데이터 초안을 만든다.

품의(harness.py)와 같은 원칙
- 모델은 적힌 값을 읽기만 한다. 금액 비교·지급처 대조·날짜 순서·증빙 산술은 여기서 코드로 한다.
- 금액을 조용히 고르지 않는다. 품의서·결제 증빙·거래명세서 금액이 다르면 후보를 보여 주고 담당자가 고른다.
- 답을 반영할 때마다 처음부터 다시 검사한다(harness.resolve 그대로 사용). 질문이 남아 있으면 결의서를 만들지 않는다.
- 규칙은 config/rules.json gyeolui, Studio 키 이름은 config/gyeolui_fields.json, 문구는 config/questions.json(g_*).

from_steps가 만드는 결과 JSON (results_gyeolui_upload/<이름>.json)
  request      결제 품의서 추출(없으면 null)
  receipts     영수증·거래명세서 추출 목록(품목 줄은 _lines)
  template     지출결의서 양식 입력 항목 추출(없으면 null)
  studio       {categories, reports{역할: 글}, decisions{역할: 판정}, draft(기입 데이터 글), others[], job_id}
  source_text  문서 인식 원문(근거 줄 찾기용)
"""

import argparse
import json
import sys
from pathlib import Path

from app.config import ROOT, config
from app.domain.gyeolui import answer, new_state, pay_amount, payee, recheck
from app.domain.harness import INPUT
from app.domain.money import won


def ask_cli(state, who):
    cli = config("questions")["cli"]
    while recheck(state):
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
            value = input(cli["input"])
        answer(state, qn["key"], value, who)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "result",
        help="결의 워크플로 결과 JSON (run_api.py --kind gyeolui) 또는 이전 _gyeolui.json",
    )
    ap.add_argument("--link", help="앱에서 작성한 품의서 인계 JSON(<건>.handoff.json)")
    ap.add_argument("--ask", action="store_true", help="터미널에서 질문에 답한다")
    ap.add_argument("--who", default="담당자")
    ap.add_argument("--out", default=str(ROOT / "out"))
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    data = json.loads(Path(a.result).read_text(encoding="utf-8"))
    link = json.loads(Path(a.link).read_text(encoding="utf-8")) if a.link else None
    state = data if "resolution_log" in data else new_state(data, link)
    if a.ask:
        ask_cli(state, a.who)
    r = state["result"]
    print(
        f"지급금액: {won(pay_amount(r)) if pay_amount(r) is not None else '미정'}  지급처: {payee(r) or '미정'}"
    )
    for rc in r["receipts"]:
        print(
            f"  증빙 {rc['no']}. {rc['label']}({rc['kind']}) {rc['vendor']} {won(rc['total'] or 0)}원 {rc['date']}"
        )
    for role, v in r["studio"].get("decisions", {}).items():
        print(f"  Studio 판정 {role}: {v}")
    print(
        f"확인 필요 {len(state['questions'])}건"
        + (" (보류)" if state.get("hold") else "")
    )
    for x in state["questions"]:
        print(f"  - {x['message']}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{Path(a.result).stem.replace('_gyeolui', '')}_gyeolui.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path}")
