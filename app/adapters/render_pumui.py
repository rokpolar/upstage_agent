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

import datetime as dt
import html
import json
from string import Template

from app.config import ROOT, fields, rules
from app.domain.money import korean_amount, to_int, won


def esc(x):
    return html.escape("" if x is None else str(x))


def fill(tpl, **values):
    return Template(tpl).safe_substitute({k: v for k, v in values.items()})


def build_html(r, case):
    F, I, form = fields(), fields()["item"], rules()["form"]
    tdir = ROOT / form["template_dir"]
    page = (tdir / "page.html").read_text(encoding="utf-8")
    parts = json.loads((tdir / "parts.json").read_text(encoding="utf-8"))

    total = to_int(r.get(F["total"])) or 0
    fund = case.get("funding_source", form["default_funding_source"])
    funding = parts["funding_separator"].join(
        fill(
            parts["funding_checked" if name == fund else "funding_unchecked"],
            name=esc(name),
        )
        for name in case.get("funding_sources", form["funding_sources"])
    )
    approvers = case.get("approvers", form["approvers"])
    rows = "".join(
        fill(
            parts["item_row"],
            no=n,
            category=esc(it.get(I["category"])),
            description=esc(it.get(I["description"])),
            amount=won(to_int(it.get(I["amount"])) or 0),
            basis=esc(it.get(I["basis"])),
            note=esc(it.get(I["note"])),
        )
        for n, it in enumerate(r.get(F["items"]) or [], 1)
    )
    flags = r.get(F["flags"]) or []
    plan = case.get("related_plan")

    return fill(
        page,
        flags=fill(
            parts["flags"],
            messages=parts["flags_separator"].join(esc(f) for f in flags),
        )
        if flags
        else "",
        funding=funding,
        sign_head="".join(
            fill(parts["sign_head_cell"], name=esc(a)) for a in approvers
        ),
        sign_blank=parts["sign_blank_cell"] * len(approvers),
        total_korean=korean_amount(total),
        total=won(total),
        vat=won(to_int(r.get(F["vat"])) or 0),
        vat_rate=f"{rules()['vat_rate'] * 100:g}",
        project_name=esc(case.get("project_name")),
        project_period=esc(case.get("project_period")),
        vendor_name=esc(r.get(F["vendor_name"])),
        vendor_representative=esc(r.get(F["vendor_representative"])),
        quote_date=esc(r.get(F["quote_date"])),
        item_rows=rows,
        related_plan=fill(parts["related_plan"], plan=esc(plan)) if plan else "",
        draft_date=esc(case.get("draft_date", dt.date.today().isoformat())),
        org_name=esc(case.get("org_name")),
        accountant=esc(case.get("accountant")),
        case_id=esc(case.get("case_id")),
        source_quote_file=esc(case.get("source_quote_file")),
    )


def build_snapshot(r, case, approver, state_log=()):
    """결의 agent로 넘기는 인계 JSON. 품의서 키(fields.json)는 그대로 두고 승인 정보를 덧붙인다."""
    F, I = fields(), fields()["item"]
    numeric = (I["quantity"], I["unit_price"], I["amount"])
    snap = {
        "case_id": case["case_id"],
        "approval_version": case.get("approval_version", 1),
        "status": "APPROVED" if approver else "DRAFT",
        "approved_by": approver,
        "approved_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")
        if approver
        else None,
        "funding_source": case.get(
            "funding_source", rules()["form"]["default_funding_source"]
        ),
        "project_name": case.get("project_name"),
        "source_quote_file": case.get("source_quote_file"),
    }
    for role in ("vendor_name", "vendor_representative", "quote_date"):
        snap[F[role]] = r.get(F[role])
    snap[F["items"]] = [
        {**it, **{k: to_int(it.get(k)) for k in numeric}}
        for it in r.get(F["items"]) or []
    ]
    for role in ("supply", "vat", "total"):
        snap[F[role]] = to_int(r.get(F[role]))
    snap[F["flags"]] = r.get(F["flags"]) or []
    snap["resolution_log"] = list(state_log)
    return snap
