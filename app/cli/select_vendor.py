"""견적서가 여러 개일 때 비교해 업체를 추천하고, 담당자가 고르거나 직접 사유를 적게 한다.

    python tools/select_vendor.py results/a.json results/b.json results/c.json [--source a.pdf b.pdf c.pdf] [--case 구매건.json]
    python tools/select_vendor.py ... --no-input        # 추천만 저장(웹·n8n이 화면에 띄울 용도)
    python tools/select_vendor.py ... --pick 2 --reason "납기 준수 실적"   # 화면에서 고른 결과를 저장

- 각 견적을 하네스(harness.review)로 검사해 문제(유효기간 경과 등)를 함께 보여준다.
- 추천 규칙(제외 조건·정렬 순서)과 사유 문구는 config/rules.json vendor_selection,
  화면 문구는 config/questions.json vendor_select에 있다.
- 추천 업체를 고르면 자동 사유를 쓰고, 다른 업체를 고르면 사유를 꼭 입력받는다(감사 대비).

출력: <out>/selection.json  {vendor, result_file, reason, recommended, by_user, quote_count, comparison[]}
fill_form.py --selection 으로 넘기면 선정 업체·선정 사유가 품의서에 들어간다.
"""

import argparse
import json
import sys
from pathlib import Path

from app.adapters.pdf import source_text
from app.config import ROOT, config, fields
from app.domain.harness import normalize, review
from app.domain.money import won
from app.domain.select_vendor import auto_reason, finalize, rank


def load_quote(path, src, case):
    from app.adapters.response import unwrap

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    r = data["result"] if "resolution_log" in data else normalize(unwrap(data) or data)
    qs = review(
        r,
        source_text(src),
        case,
        data.get("acknowledged", []) if "resolution_log" in data else [],
    )
    F = fields()
    return {
        "file": str(path),
        "vendor": r.get(F["vendor_name"]),
        "rep": r.get(F["vendor_representative"]) or "",
        "total": r.get(F["total"]) or 0,
        "quote_date": r.get(F["quote_date"]) or "",
        "issues": [q["kind"] for q in qs],
        "issue_messages": [q["message"] for q in qs],
    }


def choose(quotes, order, excluded, pick=None, reason=None, interactive=True):
    t = config("questions")["vendor_select"]
    rec = order[0] if order else None
    auto = auto_reason(order, quotes) if rec else ""
    if interactive:
        print(t["title"].format(count=len(quotes)))
        for n, q in enumerate(quotes, 1):
            status = (
                t["status_issues"].format(
                    n=len(q["issues"]), issues=", ".join(q["issue_messages"])[:80]
                )
                if q["issues"]
                else t["status_ok"]
            )
            if q in excluded:
                status += " " + t["excluded"].format(why=", ".join(q["issues"]))
            print(
                "  "
                + t["row"].format(
                    no=n,
                    vendor=q["vendor"] or "?",
                    total=won(q["total"]),
                    quote_date=q["quote_date"],
                    status=status,
                    mark=t["recommended_mark"] if q is rec else "",
                )
            )
        if rec:
            print(t["recommendation"].format(vendor=rec["vendor"], reason=auto))
        raw = input(t["choose"]).strip()
        pick = int(raw) if raw.isdigit() and 1 <= int(raw) <= len(quotes) else None
    chosen = quotes[pick - 1] if pick else rec
    if chosen is None:
        return None
    if chosen is rec and interactive:
        reason = input(t["reason_prompt"].format(auto=auto)).strip() or auto
    while chosen is not rec and interactive and not reason:
        reason = input(t["reason_required"]).strip()
    try:
        return finalize(
            quotes, rec, auto, chosen, reason, interactive or pick is not None
        )
    except ValueError as e:
        raise SystemExit(f"{e} (--reason)")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("results", nargs="+", help="견적별 품의 agent 결과 JSON")
    ap.add_argument(
        "--source",
        nargs="*",
        default=[],
        help="견적서 PDF (results와 같은 순서, 유효기간·원문 검사용)",
    )
    ap.add_argument("--case", help="구매건 JSON (품의일 → 유효기간 판단)")
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--no-input", action="store_true")
    ap.add_argument("--pick", type=int, help="고른 견적 번호(1부터)")
    ap.add_argument("--reason", help="선정 사유(추천이 아닌 업체를 고를 때 필수)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    case = json.loads(Path(a.case).read_text(encoding="utf-8")) if a.case else {}
    srcs = a.source + [None] * (len(a.results) - len(a.source))
    quotes = [load_quote(p, s, case) for p, s in zip(a.results, srcs)]
    order, excluded = rank(quotes)
    sel = choose(
        quotes,
        order,
        excluded,
        a.pick,
        a.reason,
        interactive=not (a.no_input or a.pick),
    )
    if sel is None:
        sys.exit(
            "추천할 수 있는 견적이 없어요(모두 제외 조건에 걸림). 견적을 확인해 주세요."
        )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "selection.json"
    path.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n선정: {sel['vendor']} · 사유: {sel['reason']}\n저장: {path}")
