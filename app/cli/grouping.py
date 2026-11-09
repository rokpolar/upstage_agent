"""같은 물품의 견적서끼리 묶는다(Solar의 품목 분류 + 품목 이름 글자 유사도).

    python tools/grouping.py results_team          # 묶음 결과 보기
    python tools/grouping.py results_team --check  # 파일 이름 앞 번호(01_, 02_…)와 맞는지 채점

방법: 견적서마다 품목 이름들을 글자 두 개씩(bigram)으로 쪼개 세고, 두 견적서의 코사인 유사도가
config/rules.json grouping.threshold 이상이면 같은 묶음으로 본다(평균 연결 군집).
"랜케이블"과 "LAN 케이블", "도시락(한식 5찬)"과 "정식 도시락 5찬"처럼 띄어쓰기·표기가 달라도 묶이도록
숫자·기호·규격 단위는 빼고 글자만 본다.
Studio 견적 검토가 쓴 '품목 분류'(studio.kind, 예: 사무용품, 사무용 의자)가 같으면 유사도에 kind_bonus를 더한다.
그래서 분류가 같으면 묶이기 쉽고, 분류가 달라도(표기만 다른 경우) 품목 글자가 아주 비슷하면 묶인다.
분류가 같아도 품목이 전혀 다르면(식탁과 의자를 둘 다 '가구'라고 쓴 경우) 따로 둔다.
묶음 이름은 품목 분류가 있으면 그것, 없으면 묶음 안에서 가장 많이 겹치는 첫 품목 이름이다.
"""

import argparse
import json
import sys
from pathlib import Path

from app.domain.grouping import group


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("folder")
    ap.add_argument(
        "--check", action="store_true", help="파일 이름 앞 번호를 정답으로 보고 채점"
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    from app.adapters.response import unwrap

    files = sorted(Path(a.folder).glob("*.json"))
    results = {p.stem: unwrap(json.loads(p.read_text(encoding="utf-8"))) for p in files}
    of, names = group(results)
    for n, name in names.items():
        members = [i for i in results if of[i] == n]
        print(f"[{n}] {name} ({len(members)}건): {', '.join(members)}")
    if a.check:
        truth = {i: i.split("_")[0] for i in results}
        pairs = [(x, y) for x in results for y in results if x < y]
        tp = sum(1 for x, y in pairs if of[x] == of[y] and truth[x] == truth[y])
        fp = sum(1 for x, y in pairs if of[x] == of[y] and truth[x] != truth[y])
        fn = sum(1 for x, y in pairs if of[x] != of[y] and truth[x] == truth[y])
        print(
            f"\n같은 묶음 짝: 맞음 {tp}, 잘못 묶음 {fp}, 놓침 {fn}  (묶음 {len(names)}개, 정답 {len(set(truth.values()))}개)"
        )
