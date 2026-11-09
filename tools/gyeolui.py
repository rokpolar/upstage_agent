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
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

from common import ROOT, config, fields, rules, won
from harness import HOLD, INPUT, SPECIAL, evidence_line, resolve

KEY_STRIP = re.compile(r"[\s:：·.\-()\[\]※*〔〕「」]")


def key(s):
    return KEY_STRIP.sub("", s or "").lower()


def gfields():
    return config("gyeolui_fields")


def grules():
    return rules()["gyeolui"]


def num(v):
    """'₩ 2,380,401', '2,380,400원', '120 개' → 정수. 숫자가 없으면 None."""
    if isinstance(v, (int, float)):
        return int(round(v))
    m = re.search(r"-?\d[\d,]*", str(v or ""))
    return int(m[0].replace(",", "")) if m else None


def date_of(v):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(v or ""))
    return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}" if m else ""


def text_of(v):
    return str(v or "").strip()


# ---------------------------------------------------------------- Studio 단계별 결과 → 결과 JSON

def step_text(o):
    return "".join(c.get("text", "") for c in o.get("content") or [] if c.get("type") == "output_text")


def unquote(s):
    """Instruct 결과는 JSON 문자열로 감싸여 올 때가 있다."""
    try:
        v = json.loads(s)
        return v if isinstance(v, str) else s
    except json.JSONDecodeError:
        return s


def extra_of(o):
    extra = (o.get("content") or [{}])[0].get("additional_values")
    if isinstance(extra, str) and extra.startswith("{"):
        try:
            return json.loads(extra)
        except json.JSONDecodeError:
            return {}
    return extra if isinstance(extra, dict) else {}


def parse_text(raw):
    """문서 인식(Document Parse) 결과(HTML이 든 JSON)를 줄 단위 글로 바꾼다."""
    try:
        c = json.loads(raw).get("content") or {}
        body = c.get("html") or c.get("markdown") or c.get("text") or ""
    except (json.JSONDecodeError, AttributeError):
        body = raw
    body = re.sub(r"<br\s*/?>|</(tr|p|h\d|footer|header|li|div)>", "\n", body)
    body = re.sub(r"</t[dh]>", " | ", body)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip(" |") for line in body.splitlines() if line.strip(" |\t"))


def extract_role(name, data, st):
    G = gfields()
    role = (st.get("extract_roles") or {}).get(name)
    if role:
        return role
    if isinstance(data, dict):
        if G["receipt"]["list"] in data:
            return "receipt"
        if G["request"]["amount"] in data or G["request"]["number"] in data:
            return "request"
        if G["template"]["fields"] in data:
            return "template"
    return None


def from_steps(steps, st, job_id):
    """결의 워크플로의 단계별 결과 → 결과 JSON(위 docstring 형식)."""
    types, R = st["step_types"], gfields()["receipt"]
    cite = re.compile(st["citation_pattern"])
    studio = {"categories": [], "reports": {}, "decisions": {}, "draft": "", "others": [], "job_id": job_id}
    out = {"kind": "gyeolui", "request": None, "requests": [], "receipts": [], "template": None, "extracts": [],
           "studio": studio, "source_text": ""}
    texts, classify = [], ""
    for o in steps:
        t, text, name = o.get("step_type"), step_text(o), o.get("model") or ""
        if t == types["parse"]:
            texts.append(parse_text(text))
        elif t == types["classify"]:
            classify = classify or text
        elif t == types["merge"]:
            try:  # 묶음을 어떻게 나눴는지: branch_keys = ["0:영수증", "1:결제 품의서", ...]
                studio["categories"] = [k.split(":", 1)[-1] for k in json.loads(text).get("branch_keys") or []]
            except (json.JSONDecodeError, AttributeError):
                pass
        elif t == types["extract"]:
            short = name.removeprefix(st["extract_name_prefix"])
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                out["extracts"].append({"name": short, "text": text})
                continue
            role = extract_role(short, data, st)
            if role == "receipt":
                lines = data.get(R["lines"]) or []
                rcs = data.get(R["list"]) or []
                for rc in rcs:
                    idx = rc.get(R["index"])
                    mine = [ln for ln in lines if len(rcs) == 1 or ln.get(R["line"]["index"]) == idx]
                    out["receipts"].append({**rc, "_lines": mine, "_step": short})
            elif role == "request":
                out["requests"].append(data)
            elif role == "template":
                out["template"] = data
            else:
                out["extracts"].append({"name": short, "data": data})
        elif t == types["instruct"]:
            short = name.removeprefix(st["instruct_name_prefix"])
            body = cite.sub("", unquote(text)).strip()
            role = (st.get("instruct_roles") or {}).get(short)
            if role == "draft":
                studio["draft"] = body
            elif role:
                studio["reports"][role] = body
            else:
                studio["others"].append({"name": short, "text": body})
            enum = extra_of(o).get("enum")
            if enum:
                studio["decisions"][role or short] = enum
    if not studio["categories"] and classify:
        studio["categories"] = [classify]
    out["request"] = out["requests"][0] if out["requests"] else None
    out["source_text"] = "\n".join(texts)
    return out


# ---------------------------------------------------------------- 진행 표시 (Jobs API 조회 결과 → 단계·읽어 낸 값)

STAGES = ["upload", "parse", "classify", "extract", "compare", "policy", "draft"]


def progress(job, st):
    """실행 중인 결의 작업(include all 조회)에서 끝난 단계와 지금까지 읽어 낸 값을 뽑는다(화면 진행 표시용).

    단계: parse(문서 인식) → classify(분류·분할) → extract(품의서·영수증·양식 추출, merge가 끝나면)
          → compare(품의서·영수증 비교 판정) → policy(회사 규정 판정) → draft(결의서 기입 데이터)
    """
    types, roles = st["step_types"], st.get("instruct_roles") or {}
    R, Q = gfields()["request"], gfields()["receipt"]
    done, found, receipts = set(), {}, []
    for o in job.get("output") or []:
        t = o.get("step_type")
        if not t or o.get("status", "completed") != "completed":  # 진행 중인 단계는 아직 비어 있다
            continue
        text = step_text(o)
        if t == types["parse"]:
            done.add("parse")
        elif t == types["classify"]:
            done.add("classify")
        elif t == types["merge"]:
            done.add("extract")
            try:
                found["docs"] = [k.split(":", 1)[-1] for k in json.loads(text).get("branch_keys") or []]
            except (json.JSONDecodeError, AttributeError):
                pass
        elif t == types["extract"]:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            role = extract_role(o.get("model", "").removeprefix(st["extract_name_prefix"]), data, st)
            if role == "request":
                found["request"] = " · ".join(x for x in (text_of(data.get(R["number"])), text_of(data.get(R["vendor"]))) if x)
                found["request_amount"] = num(data.get(R["amount"]))
            elif role == "receipt":
                receipts += [(short_type(text_of(rc.get(Q["type"])), receipt_kind(text_of(rc.get(Q["type"])))), num(rc.get(Q["total"])))
                             for rc in data.get(Q["list"]) or []]
        elif t == types["instruct"]:
            role = roles.get((o.get("model") or "").removeprefix(st["instruct_name_prefix"]))
            enum = extra_of(o).get("enum")
            if role == "comparison_decision":
                done.add("compare")
                found["comparison"] = enum
            elif role == "policy_decision":
                done.add("policy")
                found["policy"] = enum
            elif role == "draft":
                done.add("draft")
            if role:  # 검증 단계가 시작됐으면 추출은 끝난 것이다(merge 노드가 없는 워크플로)
                done.add("extract")
    if receipts:
        found["receipts"] = receipts
    if job.get("status") == "completed":
        done.update(STAGES)
    for i, s_ in enumerate(STAGES):  # 뒤 단계가 끝났으면 앞 단계도 끝난 것이다
        if any(x in done for x in STAGES[i + 1:]):
            done.add(s_)
    # upload는 run_api가 앞에 붙인다
    return [x for x in STAGES[1:] if x in done], {k: v for k, v in found.items() if v not in (None, "", [])}


# ---------------------------------------------------------------- 결과 JSON → 검사용 데이터

def receipt_kind(rtype):
    kinds = gfields()["receipt_kinds"]
    flat = re.sub(r"\s+", "", rtype or "")  # '거 래 명 세 서'처럼 띄어 읽힌 이름도 같게 본다
    for k in ("statement", "payment"):  # '거래명세서'를 먼저 본다('영수증'이 같이 적혀도 명세서)
        if any(w in flat for w in kinds[k]):
            return k
    return "other"


def spaced(text):
    """'거 래 명 세 서'처럼 한 글자씩 띄운 이름은 붙인다(양식 제목을 그대로 읽은 경우)."""
    parts = (text or "").split()
    return "".join(parts) if len(parts) > 1 and all(len(x) == 1 for x in parts) else (text or "").strip()


def short_type(rtype, kind):
    first = spaced(re.split(r"[,/(]", rtype or "")[0])
    return first or config("questions")["g_source_names"][kind]


def qty_unit(v):
    s = text_of(v)
    return num(s), re.sub(r"[\d,.\s\-]", "", s)


def is_vat_line(desc):
    return bool(re.search(r"부\s*가\s*(가\s*치\s*)?세|VAT", desc or "", re.I))


def normalize(raw, link=None):
    """Studio 결과를 금액은 정수, 날짜는 YYYY-MM-DD로 맞춘다. link는 이 앱에서 작성한 품의서 인계 값."""
    G = gfields()
    R, RI, C, CL = G["request"], G["request"]["item"], G["receipt"], G["receipt"]["line"]
    req = raw.get("request")
    request = None
    if req:
        items = []
        for it in req.get(R["items"]) or []:
            q, unit = qty_unit(it.get(RI["quantity"]))
            desc = text_of(it.get(RI["description"]))
            items.append({"description": desc, "quantity": q, "unit": unit, "unit_price": num(it.get(RI["unit_price"])),
                          "amount": num(it.get(RI["amount"])), "vat": is_vat_line(desc)})
        request = {k: text_of(req.get(R[k])) for k in ("number", "title", "drafter", "department", "purpose",
                                                        "tax_basis", "vendor", "budget", "method")}
        request.update({"date": date_of(req.get(R["date"])), "amount": num(req.get(R["amount"])), "items": items})
    receipts = []
    for i, rc in enumerate(raw.get("receipts") or []):
        rtype = text_of(rc.get(C["type"]))
        kind = receipt_kind(rtype)
        lines = []
        for ln in rc.get("_lines") or []:
            q, unit = qty_unit(ln.get(CL["quantity"]))
            lines.append({"description": text_of(ln.get(CL["description"])), "quantity": q, "unit": unit,
                          "unit_price": num(ln.get(CL["unit_price"])), "amount": num(ln.get(CL["amount"])),
                          "note": text_of(ln.get(CL["note"]))})
        receipts.append({"no": i + 1, "type": rtype, "kind": kind, "label": short_type(rtype, kind),
                         "vendor": text_of(rc.get(C["vendor"])), "biz_no": text_of(rc.get(C["biz_no"])),
                         "date": date_of(rc.get(C["date"])), "number": text_of(rc.get(C["number"])),
                         "supply": num(rc.get(C["supply"])), "vat": num(rc.get(C["vat"])), "total": num(rc.get(C["total"])),
                         "method": text_of(rc.get(C["method"])), "cancel": text_of(rc.get(C["cancel"])), "lines": lines})
    studio = raw.get("studio") or {}
    r = {"kind": "gyeolui", "request": request, "receipts": receipts, "link": link,
         "template": [text_of(f.get(G["template"]["label"])) for f in (raw.get("template") or {}).get(G["template"]["fields"]) or []],
         "draft": parse_draft(studio.get("draft") or ""), "studio": studio,
         "pay_amount": None, "payee": None, "review_flags": []}
    r["base"] = base_of(r)
    return r


def base_of(r):
    """비교 기준 품의: 앱에서 작성한 품의서(link)가 있으면 그것, 없으면 묶음 안 품의서."""
    link, req = r.get("link"), r.get("request")
    if link:
        return {"source": "linked", "vendor": link.get("vendor") or "", "amount": num(link.get("total")),
                "date": date_of(link.get("draft_date")), "title": link.get("title") or "", "number": link.get("doc_no") or "",
                "items": link.get("items") or []}
    if req:
        return {"source": "request", "vendor": req["vendor"], "amount": req["amount"], "date": req["date"],
                "title": req["title"], "number": req["number"], "items": req["items"]}
    return None


def parse_draft(md):
    """Studio 기입 데이터 글의 표(양식 항목 | 넣을 값 | 기입 방법 | 출처) → {역할: {value, note, label}}."""
    cfg = gfields()["draft_table"]
    labels = {key(lab): role for role, ls in cfg["labels"].items() for lab in ls}
    out = {}
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|") or re.fullmatch(r"[|\s:\-]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) <= max(cfg["label_col"], cfg["value_col"]):
            continue
        role = labels.get(key(cells[cfg["label_col"]]))
        if not role or role in out:
            continue
        clean = lambda s: re.sub(r"\*\*|`", "", s).strip()
        value = clean(cells[cfg["value_col"]])
        out[role] = {"label": cells[cfg["label_col"]], "value": None if cfg["unknown_value"] in value or not value else value,
                     "note": clean(cells[cfg["note_col"]]) if len(cells) > cfg["note_col"] else ""}
    return out


# ---------------------------------------------------------------- 검사와 질문

def vendor_key(name):
    s = name or ""
    for pat in gfields()["vendor_strip"]:
        s = re.sub(pat, "", s)
    return s.lower()


def amount_candidates(r):
    """[(출처 종류, 이름, 금액)]: 결제 증빙 → 거래명세서 → 기타 → 품의 순."""
    names = config("questions")["g_source_names"]
    out = [(rc["kind"], rc["label"], rc["total"]) for rc in r["receipts"] if rc["total"] is not None]
    base, req = r.get("base"), r.get("request")
    if base and base["amount"] is not None:
        out.append(("request", names[base["source"]], base["amount"]))
    if base and base["source"] == "linked" and req and req["amount"] is not None:
        out.append(("request", names["request"], req["amount"]))
    order = grules()["amount_source_order"]
    return sorted(out, key=lambda x: order.index(x[0]) if x[0] in order else len(order))


def grouped(cands):
    """같은 금액끼리 묶는다: [(금액, [이름…])] (처음 나온 순서)."""
    groups = {}
    for _, name, amount in cands:
        groups.setdefault(amount, [])
        if name not in groups[amount]:
            groups[amount].append(name)
    return list(groups.items())


def vendor_candidates(r):
    names = config("questions")["g_source_names"]
    out = [(rc["label"], rc["vendor"]) for rc in sorted(r["receipts"], key=lambda x: x["kind"] != "payment") if rc["vendor"]]
    base = r.get("base")
    if base and base["vendor"]:
        out.append((names[base["source"]], base["vendor"]))
    groups = {}
    for src, v in out:
        g = groups.setdefault(vendor_key(v), {"vendor": v, "sources": []})
        if src not in g["sources"]:
            g["sources"].append(src)
    return list(groups.values())


def pay_amount(r):
    """결의 금액: 담당자가 고른 값, 아니면 모든 증빙·품의 금액이 같을 때 그 값."""
    if r.get("pay_amount") is not None:
        return r["pay_amount"]
    groups = grouped(amount_candidates(r))
    return groups[0][0] if len(groups) == 1 else None


def payee(r):
    if r.get("payee"):
        return r["payee"]
    groups = vendor_candidates(r)
    return groups[0]["vendor"] if len(groups) == 1 else None


def make_q(kind, key_, field, values, params, evidence="", proposed=None):
    t = config("questions")[kind]
    options = [{"label": t["options"][name].format(**params), "value": SPECIAL.get(name, v) if v is None else v}
               for name, v in values]
    return {"key": key_, "kind": kind, "field": field, "message": t["message"].format(**params),
            "evidence": evidence, "proposed": proposed, "options": options}


def review(r, text="", acknowledged=()):
    """결과를 검사해 질문 목록을 만든다. 이미 확인(ACK)한 질문은 다시 묻지 않는다."""
    Q, tol = config("questions"), rules()["amount_tolerance_won"]
    qs = []
    base, receipts = r.get("base"), r["receipts"]

    if not base:
        qs.append(make_q("g_no_request", "g_no_request", "base", [("keep", None), ("hold", None)], {}))
    if not receipts:
        qs.append(make_q("g_no_receipt", "g_no_receipt", "receipts", [("hold", None)], {}))

    # 증빙마다 산술(공급가액 + 부가세 = 합계, 품목 합 = 공급가액)과 취소 표시
    for i, rc in enumerate(receipts):
        name = f"{rc['no']}번 {rc['label']}"
        s, v, t = rc["supply"], rc["vat"], rc["total"]
        if None not in (s, v, t) and abs(s + v - t) > tol:
            qs.append(make_q("g_receipt_sum", f"g_receipt_sum:{i}:{t}", f"receipts[{i}].total", [("keep", None), ("hold", None)],
                             {"name": name, "supply": won(s), "vat": won(v), "calc": won(s + v), "total": won(t)},
                             evidence_line(text, won(t))))
        line_sum = sum(ln["amount"] or 0 for ln in rc["lines"])
        if rc["lines"] and s is not None and abs(line_sum - s) > tol:
            qs.append(make_q("g_receipt_lines", f"g_receipt_lines:{i}:{line_sum}", f"receipts[{i}].supply",
                             [("keep", None), ("hold", None)], {"name": name, "line_sum": won(line_sum), "supply": won(s)},
                             evidence_line(text, won(s))))
        if rc["cancel"] and key(rc["cancel"]) not in ("없음", "정상", "해당없음", "아니오", "no", "none"):
            qs.append(make_q("g_cancelled", f"g_cancelled:{i}", f"receipts[{i}].cancel", [("keep", None), ("hold", None)],
                             {"name": name, "status": rc["cancel"]}))

    # 앱에서 쓴 품의서와 묶음 안 품의서가 다른 건인지 (업체·금액이 모두 다르면)
    req, link = r.get("request"), r.get("link")
    if link and req and req["vendor"] and link.get("vendor") and vendor_key(req["vendor"]) != vendor_key(link["vendor"]) \
            and req["amount"] is not None and num(link.get("total")) != req["amount"]:
        qs.append(make_q("g_link_mismatch", f"g_link_mismatch:{req['number']}", "link", [("keep", None), ("hold", None)],
                         {"linked": f"{link['vendor']} {won(num(link['total']))}원", "bundle": f"{req['vendor']} {won(req['amount'])}원"}))

    # 지급금액: 품의·결제 증빙·거래명세서 금액이 하나로 모이지 않으면 담당자가 고른다(1원도 묻는다)
    groups = grouped(amount_candidates(r))
    if len(groups) > 1 and r.get("pay_amount") is None:
        t = Q["g_amount"]
        detail = t["detail_joiner"].join(t["detail_item"].format(source="·".join(names), amount=won(a)) for a, names in groups)
        opts = [{"label": t["option"].format(amount=won(a), sources=", ".join(names)), "value": a} for a, names in groups]
        opts += [{"label": t["options"]["input"], "value": INPUT}, {"label": t["options"]["hold"], "value": HOLD}]
        qs.append({"key": "g_amount:" + ",".join(str(a) for a, _ in groups), "kind": "g_amount", "field": "pay_amount",
                   "message": t["message"].format(detail=detail), "evidence": evidence_line(text, won(groups[0][0])),
                   "proposed": groups[0][0], "options": opts})
    pay = pay_amount(r)
    if pay is not None and base and base["amount"] is not None and pay > base["amount"]:
        qs.append(make_q("g_over_request", f"g_over_request:{pay}:{base['amount']}", "pay_amount",
                         [("keep", None), ("hold", None)],
                         {"amount": won(pay), "request": won(base["amount"]), "gap": won(pay - base["amount"])}))

    # 지급처: 증빙·품의 업체명이 (주)·띄어쓰기를 빼고도 다르면 묻는다
    vgroups = vendor_candidates(r)
    if len(vgroups) > 1 and not r.get("payee"):
        t = Q["g_vendor"]
        detail = Q["g_amount"]["detail_joiner"].join(
            t["detail_item"].format(source="·".join(g["sources"]), vendor=g["vendor"]) for g in vgroups)
        opts = [{"label": t["option"].format(vendor=g["vendor"]), "value": g["vendor"]} for g in vgroups]
        opts += [{"label": t["options"]["input"], "value": INPUT}, {"label": t["options"]["hold"], "value": HOLD}]
        qs.append({"key": "g_vendor:" + ",".join(vendor_key(g["vendor"]) for g in vgroups), "kind": "g_vendor",
                   "field": "payee", "message": t["message"].format(detail=detail), "evidence": "", "proposed": None,
                   "options": opts})

    # 날짜 순서: 품의(발의)보다 먼저 거래했으면 사후 품의
    if grules()["check_date_order"] and base and base["date"]:
        early = sorted(rc["date"] for rc in receipts if rc["date"] and rc["date"] < base["date"])
        if early:
            qs.append(make_q("g_date_order", f"g_date_order:{early[0]}:{base['date']}", "base.date",
                             [("keep", None), ("hold", None)], {"paid": early[0], "requested": base["date"]}))

    return [x for x in qs if x["key"] not in acknowledged]


def recheck(state, text=""):
    r = state["result"]
    qs = review(r, text or r.get("_text", ""), state["acknowledged"])
    hold = [config("questions")["g_hold_flag"]] if state.get("hold") else []
    r["review_flags"] = [x["message"] for x in qs] + hold
    state["questions"] = qs
    return qs


def coerce(field, raw):
    if not isinstance(raw, str):
        return raw
    return num(raw) if field == "pay_amount" else raw.strip()


def new_state(raw, link=None, source=None):
    r = normalize(raw, link)
    r["_text"] = raw.get("source_text") or ""
    state = {"result": r, "questions": [], "resolution_log": [], "acknowledged": [], "hold": False, "source": source,
             "info": suggest_info(r), "hints": {k: v["note"] for k, v in r["draft"].items() if v.get("note")}}
    recheck(state)
    return state


def answer(state, key_, value, who):
    """질문 하나에 답한다(harness.resolve와 같은 기록 방식). 끝나면 처음부터 다시 검사."""
    qs = state["questions"]
    idx = next(i for i, x in enumerate(qs) if x["key"] == key_)
    state["questions"] = [qs[idx]] + qs[:idx] + qs[idx + 1:]  # resolve()는 맨 앞 질문에 답한다
    resolve(state, coerce(qs[idx]["field"], value), who)
    recheck(state)


# ---------------------------------------------------------------- 결의 정보와 양식 입력값

def suggest_info(r):
    """결의 정보 칸의 처음 값: Studio 기입 데이터 중 정해 둔 칸(rules.json gyeolui.info_from_draft), 없으면 증빙에서."""
    info = {}
    for role in grules()["info_from_draft"]:
        v = (r["draft"].get(role) or {}).get("value")
        if v:
            info[role] = v
    base = r.get("base") or {}
    if not info.get("description") and base.get("title"):
        info["description"] = grules()["description_template"].format(title=base["title"])
    pay = next((rc for rc in r["receipts"] if rc["kind"] == "payment" and rc["method"]), None)
    if not info.get("payment_method") and pay:
        info["payment_method"] = pay["method"]
    if not info.get("attachments"):
        docs = ([grules()["attachments_request"]] if base else []) + [rc["label"] for rc in r["receipts"]]
        info["attachments"] = grules()["attachments_joiner"].join(dict.fromkeys(docs))
    if base.get("number"):
        info.setdefault("request_number", base["number"])
    if base.get("date"):
        info.setdefault("request_date", base["date"])
    return info


def blocked(state):
    """결의서를 만들 수 없는 이유 목록(없으면 [])."""
    msgs = [x["message"] for x in state["questions"]]
    if state.get("hold"):
        msgs.append(config("questions")["g_hold_flag"])
    return msgs


def form_input(state):
    """fill_form.fill(kind=gyeolui)에 넘길 (result, case). result는 품의와 같은 키(fields.json)를 쓴다."""
    F, I = fields(), fields()["item"]
    r, info = state["result"], dict(state.get("info") or {})
    pay, who = pay_amount(r), payee(r)
    # 품목 줄: 거래명세서 줄 → 결제 증빙 줄 → 품의 품목(부가세 줄 제외) 순
    src = next((rc for rc in r["receipts"] if rc["kind"] == "statement" and rc["lines"]), None) \
        or next((rc for rc in r["receipts"] if rc["lines"]), None)
    lines = src["lines"] if src else [x for x in (r.get("base") or {}).get("items") or [] if not x.get("vat")]
    holder = src or next((rc for rc in r["receipts"] if rc["total"] == pay), None) or {}
    items = []
    for ln in lines:
        q, p = ln.get("quantity"), ln.get("unit_price")
        items.append({I["category"]: info.get("account", ""), I["description"]: ln.get("description", ""),
                      I["quantity"]: q, I["unit_price"]: p, I["amount"]: ln.get("amount"),
                      I["basis"]: f"{won(p)}원*{won(q)}{ln.get('unit', '')}" if None not in (p, q) else "",
                      I["note"]: ln.get("note", "")})
    if not items:
        items = [{I["category"]: info.get("account", ""), I["description"]: info.get("description", ""), I["quantity"]: None,
                  I["unit_price"]: None, I["amount"]: holder.get("supply") or pay, I["basis"]: "", I["note"]: ""}]
    result = {F["vendor_name"]: who or "", F["vendor_representative"]: info.get("representative", ""), F["items"]: items,
              F["supply"]: holder.get("supply"), F["vat"]: holder.get("vat") if items and lines else None, F["total"]: pay}
    case = {**info, "amount": pay, "payee": who or "", "title": info.get("description", ""),
            "draft_date": info.get("resolution_date", "")}
    return result, case


def handoff(result, case, selection, files=None):
    """품의서를 다 쓴 뒤 결의로 넘길 값(결의 쪽 비교 기준). result는 품의 harness 결과."""
    F, I = fields(), fields()["item"]
    items = [{"description": it.get(I["description"]), "quantity": it.get(I["quantity"]), "unit": "",
              "unit_price": it.get(I["unit_price"]), "amount": it.get(I["amount"]), "vat": False}
             for it in result.get(F["items"]) or []]
    return {"case_id": case.get("case_id"), "doc_no": case.get("doc_no"), "title": case.get("title") or "",
            "draft_date": case.get("draft_date"), "drafter": case.get("drafter"), "department": case.get("department"),
            "vendor": result.get(F["vendor_name"]), "total": result.get(F["total"]), "supply": result.get(F["supply"]),
            "vat": result.get(F["vat"]), "items": items, "selection_reason": (selection or {}).get("reason"),
            "files": files or {}, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}


# ---------------------------------------------------------------- 터미널

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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="결의 워크플로 결과 JSON (run_api.py --kind gyeolui) 또는 이전 _gyeolui.json")
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
    print(f"지급금액: {won(pay_amount(r)) if pay_amount(r) is not None else '미정'}  지급처: {payee(r) or '미정'}")
    for rc in r["receipts"]:
        print(f"  증빙 {rc['no']}. {rc['label']}({rc['kind']}) {rc['vendor']} {won(rc['total'] or 0)}원 {rc['date']}")
    for role, v in r["studio"].get("decisions", {}).items():
        print(f"  Studio 판정 {role}: {v}")
    print(f"확인 필요 {len(state['questions'])}건" + (" (보류)" if state.get("hold") else ""))
    for x in state["questions"]:
        print(f"  - {x['message']}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{Path(a.result).stem.replace('_gyeolui', '')}_gyeolui.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
