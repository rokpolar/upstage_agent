import html
import json
import re

from app.domain.gyeolui import gfields, num, receipt_kind, short_type, text_of


def step_text(o):
    return "".join(
        c.get("text", "")
        for c in o.get("content") or []
        if c.get("type") == "output_text"
    )


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
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip(" |")
        for line in body.splitlines()
        if line.strip(" |\t")
    )


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
    studio = {
        "categories": [],
        "reports": {},
        "decisions": {},
        "draft": "",
        "others": [],
        "job_id": job_id,
    }
    out = {
        "kind": "gyeolui",
        "request": None,
        "requests": [],
        "receipts": [],
        "template": None,
        "extracts": [],
        "studio": studio,
        "source_text": "",
    }
    texts, classify = [], ""
    for o in steps:
        t, text, name = o.get("step_type"), step_text(o), o.get("model") or ""
        if t == types["parse"]:
            texts.append(parse_text(text))
        elif t == types["classify"]:
            classify = classify or text
        elif t == types["merge"]:
            try:  # 묶음을 어떻게 나눴는지: branch_keys = ["0:영수증", "1:결제 품의서", ...]
                studio["categories"] = [
                    k.split(":", 1)[-1]
                    for k in json.loads(text).get("branch_keys") or []
                ]
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
                    mine = [
                        ln
                        for ln in lines
                        if len(rcs) == 1 or ln.get(R["line"]["index"]) == idx
                    ]
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
        if (
            not t or o.get("status", "completed") != "completed"
        ):  # 진행 중인 단계는 아직 비어 있다
            continue
        text = step_text(o)
        if t == types["parse"]:
            done.add("parse")
        elif t == types["classify"]:
            done.add("classify")
        elif t == types["merge"]:
            done.add("extract")
            try:
                found["docs"] = [
                    k.split(":", 1)[-1]
                    for k in json.loads(text).get("branch_keys") or []
                ]
            except (json.JSONDecodeError, AttributeError):
                pass
        elif t == types["extract"]:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            role = extract_role(
                o.get("model", "").removeprefix(st["extract_name_prefix"]), data, st
            )
            if role == "request":
                found["request"] = " · ".join(
                    x
                    for x in (
                        text_of(data.get(R["number"])),
                        text_of(data.get(R["vendor"])),
                    )
                    if x
                )
                found["request_amount"] = num(data.get(R["amount"]))
            elif role == "receipt":
                receipts += [
                    (
                        short_type(
                            text_of(rc.get(Q["type"])),
                            receipt_kind(text_of(rc.get(Q["type"]))),
                        ),
                        num(rc.get(Q["total"])),
                    )
                    for rc in data.get(Q["list"]) or []
                ]
        elif t == types["instruct"]:
            role = roles.get(
                (o.get("model") or "").removeprefix(st["instruct_name_prefix"])
            )
            enum = extra_of(o).get("enum")
            if role == "comparison_decision":
                done.add("compare")
                found["comparison"] = enum
            elif role == "policy_decision":
                done.add("policy")
                found["policy"] = enum
            elif role == "draft":
                done.add("draft")
            if (
                role
            ):  # 검증 단계가 시작됐으면 추출은 끝난 것이다(merge 노드가 없는 워크플로)
                done.add("extract")
    if receipts:
        found["receipts"] = receipts
    if job.get("status") == "completed":
        done.update(STAGES)
    for i, s_ in enumerate(STAGES):  # 뒤 단계가 끝났으면 앞 단계도 끝난 것이다
        if any(x in done for x in STAGES[i + 1 :]):
            done.add(s_)
    # upload는 run_api가 앞에 붙인다
    return [x for x in STAGES[1:] if x in done], {
        k: v for k, v in found.items() if v not in (None, "", [])
    }
