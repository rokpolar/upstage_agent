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
import datetime as dt
import json
import re
import sys
from pathlib import Path

from common import ROOT, categories, config, fields, find_pattern, rules, to_int, vat_of, won

HOLD = "__HOLD__"    # 보류(재견적 요청 등). 승인 불가 상태로 남긴다
INPUT = "__INPUT__"  # 담당자가 값을 직접 입력
ACK = "__ACK__"      # 값은 그대로 두고 확인만 했다고 기록
SPECIAL = {"hold": HOLD, "input": INPUT, "keep": ACK}


def source_text(path):
    """텍스트가 들어 있는 PDF면 원문을 돌려준다. 스캔본이거나 pypdf가 없으면 ''."""
    if not path:
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(path)).pages)


def normalize(r):
    """모델 출력 형식이 흔들려도 검사·품의서가 같게 나오도록 맞춘다.

    - 금액·수량은 정수, 날짜는 YYYY-MM-DD
    - 대표자 이름 뒤의 도장 표시(rules.json seal_marks)는 뺀다
    - 산출근거는 '단가원*수량단위'(둘 다 천 단위 쉼표)로 다시 쓴다
    - 모델이 쓴 검토 사유는 버린다(model_flags로만 보관). 검토 사유는 review()가 코드로 다시 만든다
      (모델은 금액이 맞아도 '불일치'를 적는 경우가 있었다)
    """
    F, I = fields(), fields()["item"]
    for k in ("supply", "vat", "total"):
        r[F[k]] = to_int(r.get(F[k]))
    for it in r.get(F["items"]) or []:
        for k in ("quantity", "unit_price", "amount"):
            it[I[k]] = to_int(it.get(I[k]))
        basis = str(it.get(I["basis"]) or "")
        unit = re.sub(r"^\s*[\d,]+\s*원?\s*[*×x]\s*[\d,]+\s*", "", basis).replace("㎡", "m²")
        if it[I["unit_price"]] is not None and it[I["quantity"]] is not None:
            it[I["basis"]] = f"{won(it[I['unit_price']])}원*{won(it[I['quantity']])}{unit}"
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(r.get(F["quote_date"]) or ""))
    if m:
        r[F["quote_date"]] = f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    rep = str(r.get(F["vendor_representative"]) or "").strip()
    for mark in rules()["seal_marks"]:
        if rep.endswith(mark) and len(rep) > len(mark):
            rep = rep[: -len(mark)].strip()
            break
    r[F["vendor_representative"]] = rep
    r["model_flags"] = list(r.get(F["flags"]) or [])
    r[F["flags"]] = []
    return r


def make_q(kind, key, field, values, params, evidence="", proposed=None):
    """questions.json[kind] 문구에 params를 채워 질문을 만든다.

    values: 선택지 이름 → 값 (hold/input/keep은 특수값으로 바뀐다). 순서대로 보여준다.
    """
    t = config("questions")[kind]
    options = [{"label": t["options"][name].format(**params), "value": SPECIAL.get(name, v) if v is None else v}
               for name, v in values]
    return {"key": key, "kind": kind, "field": field, "message": t["message"].format(**params),
            "evidence": evidence, "proposed": proposed, "options": options}


def evidence_line(text, *needles):
    """needles(주로 금액 표기)가 모두 들어 있는 원문 한 줄."""
    for line in text.splitlines():
        if needles and all(n in line for n in needles):
            return config("questions")["evidence_prefix"] + line.strip()
    return ""


def review(r, text="", case=None, acknowledged=()):
    """결과를 검사해 질문 목록을 만든다. 이미 확인(ACK)한 질문은 다시 묻지 않는다."""
    case = case or {}
    F, I, R, Q = fields(), fields()["item"], rules(), config("questions")
    tol = R["amount_tolerance_won"]
    rate_pct = f"{R['vat_rate'] * 100:g}"
    where = lambda f: Q["where"]["edited" if f"edited:{f}" in acknowledged else "source"]
    qs = []
    items = r.get(F["items"]) or []
    supply, vat, total = r.get(F["supply"]), r.get(F["vat"]), r.get(F["total"])

    # 필수 값
    if not r.get(F["vendor_name"]):
        qs.append(make_q("missing_vendor", "missing:vendor", F["vendor_name"], [("input", None)], {}))
    if not items:
        qs.append(make_q("missing_items", "missing:items", F["items"], [("hold", None)], {}))
    if supply is None or total is None:
        qs.append(make_q("missing_amount", "missing:amount", F["total"], [("input", None), ("hold", None)], {}))

    # 품목별 단가 × 수량
    for i, it in enumerate(items):
        qty, price, amt = it.get(I["quantity"]), it.get(I["unit_price"]), it.get(I["amount"])
        if None not in (qty, price, amt) and abs(qty * price - amt) > tol:
            calc = qty * price
            p = {"no": i + 1, "price": won(price), "qty": qty, "calc": won(calc), "amount": won(amt)}
            qs.append(make_q("item_calc", f"item_calc:{i}:{amt}", f"{F['items']}[{i}].{I['amount']}",
                             [("use_calc", calc), ("keep", None), ("hold", None)], p,
                             evidence_line(text, won(price)), calc))

    # 품목 합계 vs 공급가액
    item_sum = sum(it.get(I["amount"]) or 0 for it in items)
    if items and supply is not None and abs(item_sum - supply) > tol:
        p = {"item_sum": won(item_sum), "supply": won(supply), "where": where(F["supply"])}
        qs.append(make_q("supply_sum", f"supply_sum:{supply}", F["supply"],
                         [("use_calc", item_sum), ("keep", None), ("hold", None)], p,
                         evidence_line(text, won(supply)), item_sum))

    # 부가세 (세율을 먼저 확정하고, 그다음 합계를 묻는다)
    if supply and not vat:
        p = {"rate": rate_pct, "vat_calc": won(vat_of(supply))}
        qs.append(make_q("vat_missing", "vat_missing", F["vat"],
                         [("keep", None), ("use_calc", vat_of(supply)), ("hold", None)], p,
                         evidence_line(text, won(total or supply)), vat_of(supply)))
    elif supply and vat and abs(vat - supply * R["vat_rate"]) > tol:
        p = {"ratio": f"{vat / supply:.1%}", "rate": rate_pct, "vat": won(vat), "vat_calc": won(vat_of(supply))}
        qs.append(make_q("vat_rate", f"vat_rate:{vat}", F["vat"],
                         [("keep", None), ("use_calc", vat_of(supply)), ("hold", None)], p,
                         evidence_line(text, won(vat)), vat_of(supply)))

    # 합계
    if supply and vat and total == supply:
        t = Q["vat_excluded_total"]
        cover = find_pattern("cover_total_vat_excluded", text)
        p = {"why": t["why_cover"] if cover else t["why_equal"], "supply": won(supply), "vat": won(vat),
             "total_calc": won(supply + vat)}
        qs.append(make_q("vat_excluded_total", f"vat_excluded_total:{total}", F["total"],
                         [("use_calc", supply + vat), ("input", None)], p,
                         evidence_line(text, cover[0]) if cover else evidence_line(text, won(total)), supply + vat))
    elif None not in (supply, vat, total) and abs(supply + vat - total) > tol:
        p = {"supply": won(supply), "vat": won(vat), "total_calc": won(supply + vat), "total": won(total),
             "where": where(F["total"])}
        qs.append(make_q("total_sum", f"total_sum:{total}", F["total"],
                         [("use_calc", supply + vat), ("keep", None), ("hold", None)], p,
                         evidence_line(text, won(total)), supply + vat))

    # 비목
    cats = categories()
    for i, it in enumerate(items):
        cur = it.get(I["category"])
        if cur not in cats:
            name = it.get(I["description"]) or it.get(I["basis"]) or ""
            t = Q["category"]
            qs.append({"key": f"category:{i}:{cur}", "kind": "category", "field": f"{F['items']}[{i}].{I['category']}",
                       "message": t["message"].format(no=i + 1, name=name, current=cur or t["none"]),
                       "evidence": "", "proposed": None, "options": [{"label": c, "value": c} for c in cats]})

    # 원문 대조: 읽은 숫자가 원문에 실제로 있는지 (지어낸 값 잡기)
    if text:
        flat = re.sub(r"\s+", "", text)
        checks = [(F["supply"], supply), (F["vat"], vat)]
        for i, it in enumerate(items):
            checks += [(f"{F['items']}[{i}].{I[k]}", it.get(I[k])) for k in ("unit_price", "amount")]
        for field, v in checks:
            if f"edited:{field}" in acknowledged:  # 담당자가 직접 정한 값은 원문에 없어도 된다
                continue
            if v and won(v) not in flat and str(v) not in flat:
                qs.append(make_q("evidence", f"evidence:{field}:{v}", field, [("keep", None), ("input", None)],
                                 {"value": won(v), "field": field}))

    # 견적 유효기간
    m = find_pattern("quote_validity_days", text)
    draft = case.get("draft_date")
    if m and r.get(F["quote_date"]) and draft:
        until = dt.date.fromisoformat(r[F["quote_date"]]) + dt.timedelta(days=int(m[1]))
        if until < dt.date.fromisoformat(draft):
            qs.append(make_q("expired", f"expired:{until}", F["quote_date"], [("keep", None), ("hold", None)],
                             {"until": until, "draft": draft}, evidence_line(text, m[0])))

    return [x for x in qs if x["key"] not in acknowledged]


def set_field(r, field, value):
    m = re.fullmatch(r"(\w+)\[(\d+)\]\.(\w+)", field)
    if m:
        r[m[1]][int(m[2])][m[3]] = value
    else:
        r[field] = value


def resolve(state, answer, who):
    """질문 하나에 대한 답(answer = 고른 option의 value 또는 직접 입력값)을 반영한다."""
    qn = state["questions"][0]
    state["resolution_log"].append({
        "key": qn["key"], "question": qn["message"], "answer": answer, "by": who,
        "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
    if answer == HOLD:
        state["hold"] = True
        state["questions"] = state["questions"][1:]
        return
    if answer != ACK:
        set_field(state["result"], qn["field"], answer)
        state["acknowledged"].append(f"edited:{qn['field']}")
    state["acknowledged"].append(qn["key"])


def recheck(state, text, case):
    r = state["result"]
    qs = review(r, text, case, state["acknowledged"])
    hold = [config("questions")["hold_flag"]] if state.get("hold") else []
    r[fields()["flags"]] = [x["message"] for x in qs] + hold
    state["questions"] = qs
    return qs


def coerce(field, raw):
    """담당자가 직접 입력한 값을 칸 종류에 맞게 바꾼다(금액·수량 칸이면 정수)."""
    if not isinstance(raw, str):
        return raw
    F, I = fields(), fields()["item"]
    numeric = {F[k] for k in ("supply", "vat", "total")} | {I[k] for k in ("quantity", "unit_price", "amount")}
    return to_int(raw) if field.split(".")[-1] in numeric else raw.strip()


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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="품의 agent 결과 JSON (또는 이전 _resolved.json)")
    ap.add_argument("--source", help="원본 견적서 PDF (텍스트 PDF면 원문 대조·유효기간 검사)")
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
        from score import unwrap
        state = {"result": normalize(unwrap(data) or data), "questions": [], "resolution_log": [],
                 "acknowledged": [], "hold": False, "source": a.source}
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
    status = cli["status_hold"] if state.get("hold") else (cli["status_ok"] if not left else cli["status_left"].format(n=left))
    print("\n" + cli["summary"].format(status=status, answered=len(state["resolution_log"]), path=path))


if __name__ == "__main__":
    main()
