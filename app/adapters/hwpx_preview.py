"""HWPX를 한글 없이 HTML로 그려 미리보기·PDF를 만든다.

    python tools/hwpx_preview.py 문서.hwpx [--pdf]

양식은 고정이므로 미리보기도 양식 그대로 그린다:
  - 칸 너비: 표 전체의 칸 격자(colAddr, colSpan, cellSz)에서 열마다 너비를 풀어 <colgroup>으로 둔다.
  - 줄 높이, 칸 안 여백(cellMargin), 세로 정렬(subList vertAlign)
  - 문단 정렬(header.xml paraPr align), 글자 크기·굵기(charPr height, bold)
한글 자동화를 쓰지 않으므로 보안 확인창이 뜨지 않는다. 글꼴·선 굵기는 근사치다.
제출본은 .hwpx 원본이고, 이 미리보기는 확인·공유용이다.
"""

import html
from pathlib import Path

from lxml import etree

from app.adapters.hwpx import NS, Cell, Document, q
from app.adapters.pdf import html_to_pdf

HH = "http://www.hancom.co.kr/hwpml/2011/head"


UNIT = 75  # HWPUNIT(1/7200 inch) → CSS px(1/96 inch)


ALIGN = {
    "LEFT": "left",
    "RIGHT": "right",
    "CENTER": "center",
    "JUSTIFY": "justify",
    "DISTRIBUTE": "justify",
    "DISTRIBUTE_SPACE": "justify",
}


VALIGN = {"TOP": "top", "CENTER": "middle", "BOTTOM": "bottom"}


CSS = """
@page { size: A4; margin: 12mm; }
body { font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; font-size: 10pt; color: #111; }
table { border-collapse: collapse; margin: 0; table-layout: fixed; }
td { border: 1px solid #333; padding: 1px 2px; vertical-align: middle; word-break: keep-all; overflow-wrap: anywhere; }
p { margin: 0; white-space: pre-wrap; line-height: 1.45; }
@media screen { body { width: 700px; margin: 16px auto; } }
"""


class Styles:
    """header.xml의 문단 정렬과 글자 모양."""

    def __init__(self, doc):
        self.para, self.char = {}, {}
        raw = doc.files.get("Contents/header.xml")
        if not raw:
            return
        head = etree.fromstring(raw)
        for pp in head.iter(f"{{{HH}}}paraPr"):
            a = pp.find(f"{{{HH}}}align")
            self.para[pp.get("id")] = ALIGN.get(
                a.get("horizontal") if a is not None else "", "left"
            )
        for cp in head.iter(f"{{{HH}}}charPr"):
            self.char[cp.get("id")] = (
                int(cp.get("height", "1000")) / 100,
                cp.find(f"{{{HH}}}bold") is not None,
            )

    def para_css(self, p):
        css = [f"text-align:{self.para.get(p.get('paraPrIDRef'), 'left')}"]
        for run in p.findall("hp:run", NS):
            if "".join(t.text or "" for t in run.iter(q("t"))).strip():
                size, bold = self.char.get(run.get("charPrIDRef"), (10, False))
                css.append(f"font-size:{size:g}pt")
                if bold:
                    css.append("font-weight:700")
                break
        return ";".join(css)


def render_cell(tc, st):
    out = [render_para(p, st) for p in tc.findall("hp:subList/hp:p", NS)]
    return "".join(out) or "&nbsp;"


def render_para(p, st):
    parts = []
    text = Cell.para_text(p)
    if text.strip():
        parts.append(f'<p style="{st.para_css(p)}">{html.escape(text)}</p>')
    for tbl in p.iter(q("tbl")):
        if next(tbl.iterancestors(q("p"))) is p:
            parts.append(render_table(tbl, st))
    return "".join(parts)


def column_widths(cells, ncol):
    """칸 격자에서 열 너비를 푼다: 한 칸짜리 칸이 정한 너비 → 병합 칸에서 하나만 모르는 열 → 남은 열은 고르게."""
    widths = [None] * ncol
    for c in cells:
        if c["cs"] == 1 and widths[c["c"]] is None:
            widths[c["c"]] = c["w"]
    changed = True
    while changed:
        changed = False
        for c in cells:
            span = range(c["c"], c["c"] + c["cs"])
            unknown = [i for i in span if widths[i] is None]
            if len(unknown) == 1:
                widths[unknown[0]] = max(
                    0, c["w"] - sum(widths[i] for i in span if widths[i] is not None)
                )
                changed = True
    if None in widths:
        total = max(
            (
                sum(c["w"] for c in cells if c["r"] == r)
                for r in {c["r"] for c in cells}
            ),
            default=0,
        )
        unknown = [i for i, w in enumerate(widths) if w is None]
        share = max(0, total - sum(w for w in widths if w)) / len(unknown)
        for i in unknown:
            widths[i] = share
    return widths


def render_table(tbl, st):
    cells = []
    for tr in tbl.findall("hp:tr", NS):
        for tc in tr.findall("hp:tc", NS):
            addr, span, sz = (
                tc.find("hp:cellAddr", NS),
                tc.find("hp:cellSpan", NS),
                tc.find("hp:cellSz", NS),
            )
            cells.append(
                {
                    "r": int(addr.get("rowAddr")),
                    "c": int(addr.get("colAddr")),
                    "cs": int(span.get("colSpan")),
                    "rs": int(span.get("rowSpan")),
                    "w": int(sz.get("width", 0)) if sz is not None else 0,
                    "h": int(sz.get("height", 0)) if sz is not None else 0,
                    "tc": tc,
                }
            )
    if not cells:
        return ""
    ncol = max(c["c"] + c["cs"] for c in cells)
    widths = column_widths(cells, ncol)
    heights = {}
    for c in cells:
        if c["rs"] == 1 and c["h"]:
            heights[c["r"]] = min(heights.get(c["r"], c["h"]), c["h"])
    out = [f'<table style="width:{sum(widths) / UNIT:.1f}px"><colgroup>']
    out += [f'<col style="width:{w / UNIT:.1f}px">' for w in widths]
    out.append("</colgroup>")
    for r in sorted({c["r"] for c in cells}):
        out.append(
            f'<tr style="height:{heights[r] / UNIT:.1f}px">' if r in heights else "<tr>"
        )
        for c in sorted((c for c in cells if c["r"] == r), key=lambda c: c["c"]):
            tc = c["tc"]
            style = []
            sub = tc.find("hp:subList", NS)
            if sub is not None and sub.get("vertAlign") in VALIGN:
                style.append(f"vertical-align:{VALIGN[sub.get('vertAlign')]}")
            m = tc.find("hp:cellMargin", NS)
            if m is not None:
                pad = [
                    int(m.get(k, 0)) / UNIT for k in ("top", "right", "bottom", "left")
                ]
                style.append("padding:" + " ".join(f"{v:.1f}px" for v in pad))
            out.append(
                f'<td colspan="{c["cs"]}" rowspan="{c["rs"]}" style="{";".join(style)}">{render_cell(tc, st)}</td>'
            )
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def to_html(path):
    doc = Document(path)
    st = Styles(doc)
    body = []
    for root in doc.sections.values():
        for p in root.findall("hp:p", NS):
            body.append(render_para(p, st))
    return f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{"".join(body)}</body></html>'


def preview(path, pdf=False):
    path = Path(path)
    out = path.with_suffix(".preview.html")
    out.write_text(to_html(path), encoding="utf-8")
    if pdf:
        html_to_pdf(out, path.with_suffix(".pdf"))
        return path.with_suffix(".pdf")
    return out
