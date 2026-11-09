"""HWPX를 한글 없이 HTML로 그려 미리보기·PDF를 만든다.

    python tools/hwpx_preview.py 문서.hwpx [--pdf]

양식은 고정이므로 미리보기도 양식 그대로 그린다:
  - 칸 너비: 표 전체의 칸 격자(colAddr, colSpan, cellSz)에서 열마다 너비를 풀어 <colgroup>으로 둔다.
  - 줄 높이, 칸 안 여백(cellMargin), 세로 정렬(subList vertAlign)
  - 문단 정렬(header.xml paraPr align), 글자 크기·굵기·글꼴(charPr height, bold, fontRef → fontface)
  - 쪽 크기와 여백(section pagePr, margin): PDF도 양식의 여백대로 찍는다
한글 자동화를 쓰지 않으므로 보안 확인창이 뜨지 않는다. 양식 글꼴이 이 PC에 없으면
config/rules.json preview.font_fallback의 비슷한 글꼴로 그린다. 선 굵기는 근사치다.
제출본은 .hwpx 원본이고, 이 미리보기는 확인·공유용이다.
"""
import argparse
import html
import re
import sys
from pathlib import Path

from lxml import etree

from common import html_to_pdf, rules
from hwpx import NS, Cell, Document, q

HH = "http://www.hancom.co.kr/hwpml/2011/head"
UNIT = 75  # HWPUNIT(1/7200 inch) → CSS px(1/96 inch)
NUMBER = re.compile(r"[₩\d,.\-\s원]+")
ALIGN = {"LEFT": "left", "RIGHT": "right", "CENTER": "center", "JUSTIFY": "justify",
         "DISTRIBUTE": "justify", "DISTRIBUTE_SPACE": "justify"}
VALIGN = {"TOP": "top", "CENTER": "middle", "BOTTOM": "bottom"}

CSS = """
body { font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; font-size: 10pt; color: #111; }
table { border-collapse: collapse; margin: 0; table-layout: fixed; }
td { border: 1px solid #333; padding: 1px 2px; vertical-align: middle; word-break: keep-all; overflow-wrap: anywhere; }
p { margin: 0; white-space: pre-wrap; line-height: 1.45; }
@media screen { body { width: 700px; margin: 16px auto; } }
"""


class Styles:
    """header.xml의 문단 정렬과 글자 모양."""

    def __init__(self, doc):
        self.para, self.char, self.face = {}, {}, {}
        raw = doc.files.get("Contents/header.xml")
        if not raw:
            return
        head = etree.fromstring(raw)
        faces = {}
        for ff in head.iter(f"{{{HH}}}fontface"):
            if ff.get("lang") == "HANGUL":
                faces = {f.get("id"): f.get("face") for f in ff.iter(f"{{{HH}}}font")}
        for cp in head.iter(f"{{{HH}}}charPr"):
            ref = cp.find(f"{{{HH}}}fontRef")
            self.face[cp.get("id")] = faces.get(ref.get("hangul")) if ref is not None else None
        for pp in head.iter(f"{{{HH}}}paraPr"):
            a = pp.find(f"{{{HH}}}align")
            self.para[pp.get("id")] = ALIGN.get(a.get("horizontal") if a is not None else "", "left")
        for cp in head.iter(f"{{{HH}}}charPr"):
            self.char[cp.get("id")] = (int(cp.get("height", "1000")) / 100, cp.find(f"{{{HH}}}bold") is not None)

    def para_css(self, p):
        css = [f"text-align:{self.para.get(p.get('paraPrIDRef'), 'left')}"]
        for run in p.findall("hp:run", NS):
            if "".join(t.text or "" for t in run.iter(q("t"))).strip():
                size, bold = self.char.get(run.get("charPrIDRef"), (10, False))
                css.append(f"font-size:{size:g}pt")
                family = font_family(self.face.get(run.get("charPrIDRef")))
                if family:
                    css.append(f"font-family:{family}")
                if bold:
                    css.append("font-weight:700")
                break
        return ";".join(css)


def font_family(face):
    """양식 글꼴 이름 → CSS font-family: 양식 글꼴, 비슷한 글꼴(설정), 기본 글꼴 순."""
    if not face:
        return ""
    cfg = rules().get("preview") or {}
    similar = next((v for k, v in cfg.get("font_fallback", {}).items() if k in face), None)
    names = [face] + ([similar] if similar else []) + cfg.get("default_fonts", [])
    return ",".join(f"'{n}'" for n in dict.fromkeys(names)) + ",sans-serif"


def page_css(doc):
    """첫 구역의 쪽 크기·여백(HWPUNIT) → @page. 머리말·꼬리말 높이는 본문 여백에 더한다(한글과 같게)."""
    mm = lambda v: int(v) / 7200 * 25.4
    for root in doc.sections.values():
        pp = root.find(".//hp:pagePr", NS)
        mg = root.find(".//hp:pagePr/hp:margin", NS)
        if pp is None or mg is None:
            continue
        w, h = mm(pp.get("width", 59528)), mm(pp.get("height", 84188))
        if pp.get("landscape") == "NARROWLY":  # 가로 방향
            w, h = h, w
        top = mm(mg.get("top", 0)) + mm(mg.get("header", 0))
        bottom = mm(mg.get("bottom", 0)) + mm(mg.get("footer", 0))
        return (f"@page {{ size: {w:.1f}mm {h:.1f}mm; margin: {top:.1f}mm {mm(mg.get('right', 0)):.1f}mm "
                f"{bottom:.1f}mm {mm(mg.get('left', 0)):.1f}mm; }}")
    return "@page { size: A4; margin: 15mm 20mm; }"


def render_cell(tc, st):
    out = [render_para(p, st) for p in tc.findall("hp:subList/hp:p", NS)]
    return "".join(out) or "&nbsp;"


def render_para(p, st):
    parts = []
    text = Cell.para_text(p)
    if text.strip():
        css = st.para_css(p)
        if NUMBER.fullmatch(text.strip()):  # 금액·수량은 줄을 나누지 않는다(한글은 칸에 맞춰 글자를 줄여 둔다)
            css += ";white-space:nowrap"
        parts.append(f'<p style="{css}">{html.escape(text)}</p>')
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
                widths[unknown[0]] = max(0, c["w"] - sum(widths[i] for i in span if widths[i] is not None))
                changed = True
    if None in widths:
        total = max((sum(c["w"] for c in cells if c["r"] == r) for r in {c["r"] for c in cells}), default=0)
        unknown = [i for i, w in enumerate(widths) if w is None]
        share = max(0, total - sum(w for w in widths if w)) / len(unknown)
        for i in unknown:
            widths[i] = share
    return widths


def render_table(tbl, st):
    cells = []
    for tr in tbl.findall("hp:tr", NS):
        for tc in tr.findall("hp:tc", NS):
            addr, span, sz = tc.find("hp:cellAddr", NS), tc.find("hp:cellSpan", NS), tc.find("hp:cellSz", NS)
            cells.append({"r": int(addr.get("rowAddr")), "c": int(addr.get("colAddr")),
                          "cs": int(span.get("colSpan")), "rs": int(span.get("rowSpan")),
                          "w": int(sz.get("width", 0)) if sz is not None else 0,
                          "h": int(sz.get("height", 0)) if sz is not None else 0, "tc": tc})
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
        out.append(f'<tr style="height:{heights[r] / UNIT:.1f}px">' if r in heights else "<tr>")
        for c in sorted((c for c in cells if c["r"] == r), key=lambda c: c["c"]):
            tc = c["tc"]
            style = []
            sub = tc.find("hp:subList", NS)
            if sub is not None and sub.get("vertAlign") in VALIGN:
                style.append(f"vertical-align:{VALIGN[sub.get('vertAlign')]}")
            m = tc.find("hp:cellMargin", NS)
            if m is not None:
                pad = [int(m.get(k, 0)) / UNIT for k in ("top", "right", "bottom", "left")]
                style.append("padding:" + " ".join(f"{v:.1f}px" for v in pad))
            out.append(f'<td colspan="{c["cs"]}" rowspan="{c["rs"]}" style="{";".join(style)}">{render_cell(tc, st)}</td>')
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
    return f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{page_css(doc)}{CSS}</style><script>if (location.search.indexOf("print") >= 0) addEventListener("load", () => setTimeout(print, 300));</script></head><body>{"".join(body)}</body></html>'


def preview(path, pdf=False):
    path = Path(path)
    out = path.with_suffix(".preview.html")
    out.write_text(to_html(path), encoding="utf-8")
    if pdf:
        html_to_pdf(out, path.with_suffix(".pdf"))
        return path.with_suffix(".pdf")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hwpx")
    ap.add_argument("--pdf", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(preview(a.hwpx, a.pdf))
