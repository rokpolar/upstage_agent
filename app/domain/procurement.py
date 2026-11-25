"""견적서 여러 장(한 세트)을 보고 품의 판정을 한 번 내린다. 정답지(answers_final/<입력파일명>.json)와 같은 키로 돌려준다.

판정(config/rules.json set_pipeline, 위에서부터 먼저 맞는 것 하나):
  작성 중지      견적서가 min_quotes건 미만
  담당자 확인 필요  확인할 수 없는 견적, 최저가 동률, 대표자 동일
  재견적 필요    최저가 견적서에 금액 계산 불일치(단가×수량, 품목 합계와 공급가액, 공급가액+부가세와 합계)
Studio 판정 워크플로(tools/make_set_workflow.py)의 '품의 판정' 노드와 같은 기준이다.
  작성 가능      위에 해당 없음 → 최저가 업체 선정
"""

import math

from app.config import fields, rules
from app.domain.grouping import group
from app.domain.select_vendor import same_representatives


def cfg():
    return rules()["set_pipeline"]


def won(n):
    return f"{n:,}"


def quote_flags(q):
    """견적서 한 장의 금액 계산을 원 단위로 다시 맞춘다."""
    m, flags = cfg()["messages"], []
    for n, it in enumerate(q["items"], 1):
        qty, price, amt = (
            it.get("quantity"),
            it.get("unit_price"),
            it.get("estimated_amount"),
        )
        if None not in (qty, price, amt) and qty * price != amt:
            flags.append(
                m["item_mismatch"].format(n=n, calc=won(qty * price), doc=won(amt))
            )
    s, v, t = q.get("supply_amount"), q.get("vat_amount"), q.get("total_amount")
    amounts = [it.get("estimated_amount") for it in q["items"]]
    if s is not None and amounts and None not in amounts and sum(amounts) != s:
        flags.append(m["supply_mismatch"].format(calc=won(sum(amounts)), doc=won(s)))
    if None not in (s, v, t) and s + v != t:
        flags.append(m["total_mismatch"].format(calc=won(s + v), doc=won(t)))
    return flags


def has_vendor(name):
    return isinstance(name, str) and name.strip() not in (
        "",
        "검토 필요",
        cfg()["unknown_vendor"],
    )


def decide(file_name, quotes, not_quote_pages=(), failed_pages=()):
    """Keep extracted notes and use verified amounts for decisions."""
    c, m, d = cfg(), cfg()["messages"], cfg()["decisions"]
    codes = c["review_codes"]
    checked_flags = [quote_flags(q) for q in quotes]
    quotes = [
        {
            **q,
            "review_flags": list(
                dict.fromkeys([*(q.get("review_flags") or []), *flags])
            ),
        }
        for q, flags in zip(quotes, checked_flags, strict=True)
    ]
    totals = [q.get("total_amount") for q in quotes]
    known = sorted(total for total in totals if total is not None)
    low = known[0] if known else None
    lows = [i for i, total in enumerate(totals) if low is not None and total == low]
    winner = lows[0] if len(lows) == 1 and len(known) == len(quotes) else None
    best = quotes[winner] if winner is not None else None
    blocking = checked_flags[winner] if winner is not None else []
    set_flags = [
        m["unreadable_total"].format(vendor=q["vendor_name"])
        for q in quotes
        if q.get("total_amount") is None
    ]
    if len(lows) > 1:
        vendors = ", ".join(quotes[i]["vendor_name"] for i in lows)
        set_flags.append(m["tie"].format(vendors=vendors, total=won(low)))
    representatives = same_representatives(
        [
            {
                "vendor": q["vendor_name"],
                "rep": q.get("vendor_representative"),
                "file": q.get("file"),
            }
            for q in quotes
            if has_vendor(q.get("vendor_name"))
        ]
    )
    set_flags.extend(
        m["same_rep"].format(vendors=", ".join(match["vendors"]), rep=match["rep"])
        for match in representatives
    )
    missing_vendor = best is not None and not has_vendor(best.get("vendor_name"))
    if missing_vendor:
        set_flags.insert(0, m["missing_vendor"])
    if failed_pages:
        set_flags.insert(0, m["processing_failed"])
    needs = (
        [codes["unreadable"]] * (len(known) < len(quotes))
        + [codes["tie"]] * (len(lows) > 1)
        + [codes["same_rep"]] * bool(representatives)
        + [codes["calc"]] * bool(blocking)
        + [codes["missing_vendor"]] * missing_vendor
        + [codes["processing_failed"]] * bool(failed_pages)
    )

    if failed_pages:
        best = None
        decision, reason = d["check"], m["processing_failed"]
    elif len(quotes) < c["min_quotes"]:
        best = None
        decision, reason = (
            d["stop"],
            m["too_few"].format(n=len(quotes), min=c["min_quotes"]),
        )
    elif set_flags:
        if missing_vendor:
            best = None
        decision, reason = d["check"], set_flags[0]
    elif blocking:
        decision, reason = (
            d["requote"],
            m["requote"].format(vendor=best["vendor_name"], flag=blocking[0]),
        )
    else:
        decision = d["ok"]
        reason = m["ok"].format(
            n=len(quotes),
            vendor=best["vendor_name"],
            total=won(low),
            gap=won(known[1] - low),
        )
    out = {
        "file": file_name,
        "decision": decision,
        "selected_vendor": best["vendor_name"] if best else c["unknown_vendor"],
        "selected_total": best["total_amount"] if best else None,
        "selected_quote": {"file": best.get("file"), "page": best.get("page")}
        if best
        else None,
        "quote_count": len(quotes),
        "needs_review": needs,
        "reason": reason,
        "review_flags": set_flags
        + [
            m["vendor_flag"].format(vendor=best["vendor_name"], flag=flag)
            for flag in (best["review_flags"] if best else [])
        ],
        "quotes": quotes,
    }
    if not_quote_pages:
        out["not_quote_pages"] = list(not_quote_pages)
    if failed_pages:
        out["failed_pages"] = list(failed_pages)
    return out


def to_quote(name, n, r):
    F, keys = fields(), cfg()["item_keys"]
    return {
        "file": name,
        "page": n,
        "vendor_name": r[F["vendor_name"]].strip()
        if has_vendor(r.get(F["vendor_name"]))
        else "검토 필요",
        "vendor_representative": r.get(F["vendor_representative"]) or "",
        "quote_date": r.get("quote_date") or "",
        "items": [{k: it.get(k) for k in keys} for it in r.get(F["items"]) or []],
        "supply_amount": r.get("supply_amount"),
        "vat_amount": r.get("vat_amount"),
        "total_amount": r.get(F["total"]),
        "review_flags": list(r.get("review_flags") or []),
    }


def cases_of(reads):
    """파일 이름은 보지 않고 견적서 내용으로 구매 건을 나눈다(tools/grouping.py: Solar 품목 분류 + 품목 이름 유사도).
    견적서가 아닌 쪽(표지 등)은 같은 파일의 견적서가 든 구매 건에 붙이고, 견적서가 하나도 없는 파일은 따로 돌려준다."""
    quotes = {f"{name}#{n}": r for name, n, r, _ in reads if r is not None}
    of = group(quotes)[0] if quotes else {}
    groups, file_group, loose = {}, {}, []
    for name, n, r, cat in reads:
        if r is not None:
            groups.setdefault(of[f"{name}#{n}"], []).append((name, n, r, cat))
            file_group.setdefault(name, of[f"{name}#{n}"])
    for name, n, r, cat in reads:
        if r is None:
            (groups[file_group[name]] if name in file_group else loose).append(
                (name, n, r, cat)
            )
    ordered = sorted(groups.values(), key=lambda rs: min((x[0], x[1]) for x in rs))
    return [(f"case_{chr(ord('A') + i)}", rs) for i, rs in enumerate(ordered)], loose


def evaluate(rows):
    """Group extracted pages and apply the same rules for the CLI and n8n."""
    if not isinstance(rows, list):
        raise ValueError("견적 쪽 목록이 필요합니다.")
    reads, seen, failures = [], set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("견적 쪽 형식이 올바르지 않습니다.")
        file, page = row.get("file"), row.get("page")
        result, category = row.get("quote"), row.get("category")
        error = row.get("error")
        if (
            not isinstance(file, str)
            or not file.strip()
            or type(page) is not int
            or page < 1
            or (file, page) in seen
        ):
            raise ValueError("파일과 쪽 번호는 각 견적 쪽을 고유하게 식별해야 합니다.")
        if error is not None:
            if result is not None or not isinstance(error, str) or not error.strip():
                raise ValueError("처리 실패 항목의 형식이 올바르지 않습니다.")
            failures.append({"file": file, "page": page, "error": error})
        if result is None:
            if not isinstance(category, str) or not category:
                raise ValueError("건너뛴 쪽의 분류가 필요합니다.")
        else:
            check_quote(result)
        seen.add((file, page))
        reads.append((file, page, result, category))
    cases, loose = cases_of(reads)
    evaluated = []
    failed_ids = {(error["file"], error["page"]) for error in failures}
    files_with_quotes = {file for file, _, result, _ in reads if result is not None}
    for _, batch in cases:
        docs = sorted(batch, key=lambda row: (row[0], row[1]))
        quotes = [
            to_quote(name, page, result)
            for name, page, result, _ in batch
            if result is not None
        ]
        skipped = [
            {"file": name, "page": page, "category": category}
            for name, page, result, category in batch
            if result is None and (name, page) not in failed_ids
        ]
        files = sorted({name for name, *_ in batch})
        # A failed page with no recognized quote from its file cannot be assigned to a case.
        # It could affect any of the comparisons in this batch.
        related_failures = [
            error
            for error in failures
            if error["file"] in files or error["file"] not in files_with_quotes
        ]
        result = decide(
            files[0] if len(files) == 1 else files, quotes, skipped, related_failures
        )
        result["documents"] = [
            "견적서" if row[2] is not None else row[3] for row in docs
        ]
        evaluated.append({"rows": batch, "result": result})
    return {"cases": evaluated, "loose": loose, "failed_pages": failures}


def check_quote(result):
    """Validate fields used by grouping, sorting and arithmetic."""
    if not isinstance(result, dict):
        raise ValueError("견적 결과 형식이 올바르지 않습니다.")
    f = fields()
    items = result.get(f["items"])
    flags = result.get(f["flags"]) or []
    meta = result.get("studio") or {}
    numbers = (result.get(f[key]) for key in ("total", "supply", "vat"))
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("kind", ""), str)
        or not isinstance(items, list)
        or not all(isinstance(item, dict) for item in items)
        or not isinstance(flags, list)
        or not all(isinstance(flag, str) for flag in flags)
        or not all(
            value is None or isinstance(value, str)
            for value in (
                result.get(f[k]) for k in ("vendor_name", "vendor_representative")
            )
        )
        or not all(is_amount(value) for value in numbers)
        or not all(
            is_amount(item.get(f["item"][key]))
            for item in items
            for key in ("quantity", "unit_price", "amount")
        )
    ):
        raise ValueError("견적 결과 형식이 올바르지 않습니다.")


def is_amount(value):
    return (
        value is None
        or type(value) is int
        or (type(value) is float and math.isfinite(value))
    )


def selected_result(result, rows):
    """Resolve the winning file and page to the original quote used by the form."""
    selected = result.get("selected_quote")
    if isinstance(selected, dict) and type(selected.get("page")) is int:
        for file, page, quote, _ in rows:
            if quote is None or selected != {"file": file, "page": page}:
                continue
            vendor = quote.get(fields()["vendor_name"])
            if (
                has_vendor(vendor)
                and vendor.strip() == result.get("selected_vendor")
                and quote.get(fields()["total"]) == result.get("selected_total")
            ):
                return quote
    raise ValueError("선정된 견적 원본이 구매 건 판정 결과와 일치하지 않습니다.")
