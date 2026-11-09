"""Studio 실행 결과 JSON을 정답 JSON과 항목별로 비교한다.

    python tools/score.py results answers            # 폴더끼리 (파일 이름이 같은 것끼리)
    python tools/score.py results/q01.json answers/q01.json
    python tools/score.py results answers --check    # 하네스(정규화 + 코드 검사)를 거친 뒤 채점 = 실제 파이프라인 정확도

- 키 이름은 config/fields.json에서 읽는다(코드에 키 이름을 두지 않는다)
- 금액·수량: 정수로 바꿔 정확히 같아야 정답
- 업체명·날짜·비목·산출근거: 공백·쉼표·법인 표기((주)/㈜/주식회사)·㎡/m²·날짜 구분자를 맞춘 뒤 비교
- 자유 문장 키(fields.json free_text): 유사도만 참고로 보여주고 점수에서 뺀다
- 검토 사유: '검토 필요 여부'(비었는지)가 같으면 정답
결과는 화면 표와 score_report.csv로 남긴다.
"""
import csv
import difflib
import json
import re
import sys
from pathlib import Path

from common import fields, to_int


def key_groups():
    """채점할 키를 역할별로 묶는다: (문자열, 숫자, 품목 문자열, 품목 숫자, 품목 자유문장)."""
    F, I = fields(), fields()["item"]
    top_str = [F[k] for k in ("vendor_name", "vendor_representative", "quote_date")]
    top_num = [F[k] for k in ("supply", "vat", "total")]
    item_num = [I[k] for k in ("quantity", "unit_price", "amount")]
    item_fuzzy = [I[k] for k in F["free_text"]]
    item_str = [v for v in I.values() if v not in item_num and v not in item_fuzzy]
    return top_str, top_num, item_str, item_num, item_fuzzy


def unwrap(d):
    """Studio/API 응답이 한 겹 감싸져 있으면 품의서 키가 있는 dict를 꺼낸다."""
    if isinstance(d, dict) and fields()["items"] in d:
        return d
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    continue
            found = unwrap(v)
            if found is not None:
                return found
    if isinstance(d, list):
        for v in d:
            found = unwrap(v)
            if found is not None:
                return found
    return None


def norm(v):
    s = "" if v is None else str(v)
    s = s.replace("주식회사", "㈜").replace("(주)", "㈜").replace("㎡", "m²").replace(",", "")
    s = re.sub(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})일?",
               lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}", s)
    return re.sub(r"\s+", "", s)


def compare(result, answer):
    """(필드, 정답값, 결과값, 판정) 목록을 돌려준다. 판정: O / X / 참고(유사도).

    정답 JSON에 없는 키는 채점하지 않는다(정답 초안에서 사람이 아직 정하지 않은 값).
    """
    TOP_STR, TOP_NUM, ITEM_STR, EXACT_NUM, FUZZY = key_groups()
    ITEMS, FLAGS = fields()["items"], fields()["flags"]
    rows = []
    for k in TOP_STR:
        if k in answer:
            rows.append((k, answer.get(k), result.get(k), "O" if norm(result.get(k)) == norm(answer.get(k)) else "X"))
    for k in TOP_NUM:
        if k in answer:
            rows.append((k, answer.get(k), result.get(k), "O" if to_int(result.get(k)) == to_int(answer.get(k)) else "X"))

    a_items, r_items = answer.get(ITEMS) or [], result.get(ITEMS) or []
    rows.append((f"{ITEMS}.count", len(a_items), len(r_items), "O" if len(a_items) == len(r_items) else "X"))
    for i, a in enumerate(a_items):
        r = r_items[i] if i < len(r_items) else {}
        for k in ITEM_STR:
            if k in a:
                rows.append((f"{ITEMS}[{i}].{k}", a.get(k), r.get(k), "O" if norm(r.get(k)) == norm(a.get(k)) else "X"))
        for k in EXACT_NUM:
            if k in a:
                rows.append((f"items[{i}].{k}", a.get(k), r.get(k), "O" if to_int(r.get(k)) == to_int(a.get(k)) else "X"))
        for k in FUZZY:
            if k not in a:
                continue
            ratio = difflib.SequenceMatcher(None, norm(a.get(k)), norm(r.get(k))).ratio()
            rows.append((f"{ITEMS}[{i}].{k}", a.get(k), r.get(k), f"참고 {ratio:.0%}"))

    a_flag, r_flag = bool(answer.get(FLAGS)), bool(result.get(FLAGS))
    rows.append((f"{FLAGS}(검토 필요 여부)", answer.get(FLAGS), result.get(FLAGS), "O" if a_flag == r_flag else "X"))
    return rows


def load(p):
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    found = unwrap(data)
    if found is None:
        sys.exit(f"{p}: '{fields()['items']}' 키가 있는 품의서 결과를 찾지 못했습니다.")
    return found


def pairs(res, ans):
    res, ans = Path(res), Path(ans)
    if res.is_file():
        return [(res.stem, res, ans)]
    out = []
    for a in sorted(ans.glob("*.json")):
        r = res / a.name
        out.append((a.stem, r if r.exists() else None, a))
    return out


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    use_check = "--check" in sys.argv
    if len(args) != 2:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    report = []
    total_o = total_n = 0
    print(f"{'문서':<6}{'정답/채점':>10}{'정확도':>8}   틀린 항목")
    for doc, r_path, a_path in pairs(*args):
        if r_path is None:
            print(f"{doc:<6}{'결과 없음':>10}")
            continue
        result = load(r_path)
        if use_check:
            from harness import normalize, review
            normalize(result)
            result[fields()["flags"]] = [x["message"] for x in review(result)]
        rows = compare(result, load(a_path))
        scored = [x for x in rows if x[3] in ("O", "X")]
        o = sum(1 for x in scored if x[3] == "O")
        total_o += o
        total_n += len(scored)
        wrong = [x[0] for x in scored if x[3] == "X"]
        print(f"{doc:<6}{f'{o}/{len(scored)}':>10}{o / len(scored):>8.0%}   {', '.join(wrong) or '-'}")
        report += [(doc, *x) for x in rows]
    if total_n:
        print(f"\n전체 {total_o}/{total_n} = {total_o / total_n:.1%}  (자유 문장 키는 점수에서 제외, CSV에 유사도 기록)")
    out = Path("score_report.csv")
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["문서", "필드", "정답", "결과", "판정"])
        for row in report:
            w.writerow([json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in row])
    print(f"상세: {out.resolve()}")


if __name__ == "__main__":
    main()
