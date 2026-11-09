import json
import re

from app.adapters.studio_errors import NotQuote
from app.config import fields


def output_text(res):
    if res.get("output_text"):
        return res["output_text"]
    texts = [
        c.get("text", "")
        for o in res.get("output") or []
        for c in o.get("content") or []
        if c.get("type") == "output_text"
    ]
    return "".join(texts)


def stage_of(step_type, st):
    """Studio 단계 종류 → 화면 단계 이름(parse/classify/extract/instruct)."""
    types = st.get("step_types") or {}
    for stage, t in types.items():
        if step_type == t:
            return stage
    return "parse" if "parse" in (step_type or "") else None


def kind_of(text, st):
    """견적 검토 결과에서 '품목 분류:' 줄을 꺼낸다(없으면 빈 문자열)."""
    m = re.search(st.get("kind_pattern") or "$^", text or "", re.M)
    return (
        re.sub(st.get("citation_pattern") or "$^", "", m.group(1)).strip() if m else ""
    )


def partial(job, st):
    """진행 중인 작업에서 지금까지 끝난 단계와 읽어 낸 값을 뽑는다(화면 진행 표시용)."""
    steps = [o for o in job.get("output") or [] if o.get("step_type")]
    done, found = set(), {}
    for o in steps:
        stage = stage_of(o["step_type"], st)
        if (
            not stage or o.get("status", "completed") != "completed"
        ):  # 진행 중인 단계는 아직 비어 있다
            continue
        if (
            stage != "instruct"
        ):  # 검토·판정은 Instruct 노드가 여러 개라 작업이 끝나야 끝난 것이다
            done.add(stage)
        text = step_text(o)
        if stage == "classify" and text:
            found["category"] = text.strip()
        if stage == "extract" and text:
            try:
                r = json.loads(text)
                F = fields()
                found.update(
                    {
                        "vendor": r.get(F["vendor_name"]),
                        "total": r.get(F["total"]),
                        "items": len(r.get(F["items"]) or []),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                pass
        if stage == "instruct":
            if (o.get("model") or "").removeprefix(
                st.get("instruct_name_prefix", "")
            ) == st.get("review_step") and kind_of(text, st):
                found["kind"] = kind_of(text, st)
            extra = (o.get("content") or [{}])[0].get("additional_values")
            extra = (
                json.loads(extra)
                if isinstance(extra, str) and extra.startswith("{")
                else (extra or {})
            )
            if extra.get("enum"):
                found["decision"] = extra["enum"]
    if job.get("status") == "completed":
        done.add("instruct")
    order = [
        "parse",
        "classify",
        "extract",
        "instruct",
    ]  # 뒤 단계가 끝났으면 앞 단계도 끝난 것이다
    for i, s in enumerate(order):
        if any(x in done for x in order[i + 1 :]):
            done.add(s)
    return sorted(done, key=order.index), found


def step_text(o):
    return "".join(
        c.get("text", "")
        for c in o.get("content") or []
        if c.get("type") == "output_text"
    )


def from_steps(steps, st, job_id):
    """노드 워크플로의 단계별 결과 → 추출 JSON + studio 칸."""
    types = st["step_types"]
    clean = lambda s: re.sub(st["citation_pattern"], "", s).strip()

    def unquote(s):  # Instruct 결과는 JSON 문자열로 감싸여 올 때가 있다
        try:
            v = json.loads(s)
            return v if isinstance(v, str) else s
        except json.JSONDecodeError:
            return s

    category = next(
        (step_text(o) for o in steps if o["step_type"] == types["classify"]), ""
    )
    ext = next((o for o in steps if o["step_type"] == types["extract"]), None)
    if ext is None:
        raise NotQuote(category)
    result = json.loads(step_text(ext))
    meta = {
        "category": category,
        "review": "",
        "kind": "",
        "decision": "",
        "branch": "",
        "note": "",
    }
    for o in steps:
        if o["step_type"] != types["instruct"]:
            continue
        name = (o.get("model") or "").removeprefix(st["instruct_name_prefix"])
        text = clean(unquote(step_text(o)))
        extra = (o.get("content") or [{}])[0].get("additional_values")
        extra = (
            json.loads(extra)
            if isinstance(extra, str) and extra.startswith("{")
            else (extra or {})
        )
        if extra.get("enum"):
            meta["decision"] = extra["enum"]
        elif name == st["review_step"]:
            meta["review"] = text
            meta["kind"] = kind_of(text, st)
        else:
            meta["branch"], meta["note"] = name, text
    meta["job_id"] = job_id
    result["studio"] = meta
    return result
