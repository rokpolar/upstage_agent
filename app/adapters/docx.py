"""지출결의서 DOCX 양식(고정)을 결의 결과로 채우고, 화면용 HTML 미리보기를 만든다.

DOCX는 zip이고 본문은 word/document.xml에 있다. 표는 w:tbl, 줄은 w:tr, 칸은 w:tc,
글자는 w:p > w:r > w:t 에 있다. 어느 칸에 무엇을 쓰는지는 config/docx_gyeolui.json에 있다.

- 라벨 칸(예: '계 정 과 목')을 공백·기호를 빼고 찾아, 같은 줄 오른쪽 칸에 값을 쓴다(라벨 글자 모양 그대로).
- 정해 둔 문단(날짜 줄, 기안자 줄, 붙임 줄)은 문단 글자를 바꾼다(첫 run의 글자 모양 유지).
- 값이 없는 칸은 비워 두고 보고서의 missing에 올린다. 양식의 다른 부분(결재란 등)은 건드리지 않는다.
"""

import copy
import html
import re
import zipfile
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
KEY_STRIP = re.compile(r"[\s:：·.\-()\[\]※*〔〕「」]")


def w(tag):
    return f"{{{W}}}{tag}"


def key(text):
    return KEY_STRIP.sub("", text or "")


def para_text(p):
    return "".join(t.text or "" for t in p.iter(w("t")))


def cell_text(tc):
    return "\n".join(para_text(p) for p in tc.iter(w("p")))


class SafeDict(dict):
    def __missing__(self, k):
        return ""


class DocxForm:
    """DOCX 양식 한 부. fill()로 채우고 save()로 새 파일을 만든다(양식 원본은 바꾸지 않는다)."""

    def __init__(self, path):
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as z:
            self.infos = z.infolist()
            self.files = {i.filename: z.read(i.filename) for i in self.infos}
        self.doc = etree.fromstring(self.files["word/document.xml"])
        self.body = self.doc.find("w:body", NS)

    # ------------------------------------------------------------ 쓰기

    @staticmethod
    def write_cell(tc, value, rpr=None):
        """칸의 첫 문단에 value를 쓴다(이전 글자는 지운다). rpr는 쓸 글자 모양(w:rPr)."""
        paras = tc.findall("w:p", NS)
        if not paras:
            paras = [etree.SubElement(tc, w("p"))]
        for p in paras[1:]:  # 값 칸은 한 문단만 쓴다
            tc.remove(p)
        p = paras[0]
        old = p.findall("w:r", NS)
        keep = rpr if rpr is not None else (old[0].find("w:rPr", NS) if old else None)
        for r in old:
            p.remove(r)
        lines = str(value).split("\n")
        for i, line in enumerate(lines):
            r = etree.SubElement(p, w("r"))
            if keep is not None:
                r.append(copy.deepcopy(keep))
            if i:
                etree.SubElement(r, w("br"))
            t = etree.SubElement(r, w("t"))
            t.set(XML_SPACE, "preserve")
            t.text = line

    @staticmethod
    def write_para(p, value):
        """문단 글자를 value로 바꾼다(첫 run의 글자 모양 유지)."""
        runs = p.findall("w:r", NS)
        rpr = runs[0].find("w:rPr", NS) if runs else None
        for r in runs:
            p.remove(r)
        r = etree.SubElement(p, w("r"))
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        t = etree.SubElement(r, w("t"))
        t.set(XML_SPACE, "preserve")
        t.text = value

    def fill(self, values, cfg):
        """values: {이름: 값}(빈 값은 없는 것으로 본다). 보고서 {filled, missing, notes}를 돌려준다."""
        ctx = SafeDict({k: v for k, v in values.items() if v not in (None, "")})
        names = cfg["names"]
        report = {"filled": [], "missing": [], "notes": []}
        need = lambda tpl: re.findall(r"\{(\w+)\}", tpl)
        has_all = lambda tpl: all(ctx.get(k) not in (None, "") for k in need(tpl))

        labels = {
            key(lab): role
            for role, c in cfg["cells"].items()
            if not role.startswith("_")
            for lab in c["labels"]
        }
        seen = set()
        for tr in self.body.iter(w("tr")):
            cells = tr.findall("w:tc", NS)
            for i, tc in enumerate(cells[:-1]):
                role = labels.get(key(cell_text(tc)))
                if not role or role in seen:
                    continue
                seen.add(role)
                tpl = cfg["cells"][role]["value"]
                label_run = tc.find(".//w:r", NS)
                rpr = label_run.find("w:rPr", NS) if label_run is not None else None
                if rpr is not None:  # 값은 라벨과 같은 글꼴·크기로, 굵게는 하지 않는다
                    rpr = copy.deepcopy(rpr)
                    for b in rpr.findall("w:b", NS) + rpr.findall("w:bCs", NS):
                        rpr.remove(b)
                if has_all(tpl):
                    self.write_cell(cells[i + 1], tpl.format_map(ctx), rpr)
                    report["filled"].append(
                        {
                            "role": role,
                            "label": names.get(role, role),
                            "value": tpl.format_map(ctx),
                        }
                    )
                else:
                    self.write_cell(cells[i + 1], "", rpr)
                    report["missing"].append(
                        {"role": role, "label": names.get(role, role)}
                    )
        for role in labels.values():
            if role not in seen and not any(
                m["role"] == role for m in report["missing"]
            ):
                report["notes"].append(
                    f"양식에서 '{names.get(role, role)}' 칸을 찾지 못함"
                )
                seen.add(role)

        for rule in cfg["paragraphs"]:
            pat = re.compile(rule["match"])
            p = next(
                (p for p in self.body.iter(w("p")) if pat.search(para_text(p))), None
            )
            role = rule["role"]
            if p is None:
                report["notes"].append(
                    f"양식에서 '{names.get(role, role)}' 줄을 찾지 못함"
                )
            elif has_all(rule["value"]):
                self.write_para(p, rule["value"].format_map(ctx))
                report["filled"].append(
                    {
                        "role": role,
                        "label": names.get(role, role),
                        "value": rule["value"].format_map(ctx),
                    }
                )
            elif not any(m["role"] == role for m in report["missing"]):
                report["missing"].append({"role": role, "label": names.get(role, role)})
        return report

    def save(self, path, drop=()):
        """채운 문서를 새 DOCX로 저장한다. drop의 부분(옛 미리보기 그림 등)은 빼고 관계 목록에서도 지운다."""
        self.files["word/document.xml"] = etree.tostring(
            self.doc, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        drop = {d for d in drop if d in self.files}
        if drop and "_rels/.rels" in self.files:
            rels = etree.fromstring(self.files["_rels/.rels"])
            for rel in list(rels):
                if rel.get("Target", "").lstrip("/") in drop:
                    rels.remove(rel)
            self.files["_rels/.rels"] = etree.tostring(
                rels, xml_declaration=True, encoding="UTF-8", standalone=True
            )
        path = Path(path)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for (
                info
            ) in self.infos:  # [Content_Types].xml을 맨 앞에 두는 원래 순서를 지킨다
                if info.filename not in drop:
                    z.writestr(info.filename, self.files[info.filename])
        return path

    # ------------------------------------------------------------ 미리보기

    def to_html(self):
        """화면 미리보기용 HTML(표·문단·굵기·가운데 정렬·글자 크기만 흉내 낸다). 제출본은 DOCX다."""

        def run_html(r):
            text = "".join(
                (t.text or "") if t.tag == w("t") else "\n"
                for t in r
                if t.tag in (w("t"), w("br"))
            )
            if not text:
                return ""
            rpr = r.find("w:rPr", NS)
            css = []
            if rpr is not None:
                if rpr.find("w:b", NS) is not None:
                    css.append("font-weight:700")
                sz = rpr.find("w:sz", NS)
                if sz is not None and sz.get(w("val"), "").isdigit():
                    css.append(f"font-size:{int(sz.get(w('val'))) / 2:g}pt")
            return f'<span style="{";".join(css)}">{html.escape(text)}</span>'

        def para_html(p):
            jc = p.find("w:pPr/w:jc", NS)
            align = {"center": "center", "right": "right", "both": "justify"}.get(
                jc.get(w("val")) if jc is not None else "", "left"
            )
            inner = "".join(run_html(r) for r in p.iter(w("r")))
            return f'<p style="text-align:{align}">{inner or "&nbsp;"}</p>'

        def table_html(tbl):
            rows = []
            for tr in tbl.findall("w:tr", NS):
                tds = []
                for tc in tr.findall("w:tc", NS):
                    span = tc.find("w:tcPr/w:gridSpan", NS)
                    cs = f' colspan="{span.get(w("val"))}"' if span is not None else ""
                    tds.append(
                        f"<td{cs}>{''.join(para_html(p) for p in tc.findall('w:p', NS))}</td>"
                    )
                rows.append(f"<tr>{''.join(tds)}</tr>")
            return f"<table>{''.join(rows)}</table>"

        parts = []
        for el in self.body:
            if el.tag == w("p"):
                parts.append(para_html(el))
            elif el.tag == w("tbl"):
                parts.append(table_html(el))
        css = (
            "@page{size:A4;margin:18mm}body{font-family:'Malgun Gothic','Apple SD Gothic Neo','Noto Sans KR',sans-serif;"
            "font-size:11pt;color:#111;max-width:640px;margin:24px auto;line-height:1.5}"
            "table{border-collapse:collapse;width:100%;margin:8px 0}td{border:1px solid #333;padding:6px 8px;vertical-align:middle}"
            "td:first-child{width:28%}p{margin:4px 0;white-space:pre-wrap}"
        )
        return f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><style>{css}</style></head><body>{"".join(parts)}</body></html>'
