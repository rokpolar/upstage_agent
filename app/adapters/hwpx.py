"""HWPX(한글 XML 문서)를 읽고 표 칸에 글자를 써 넣는다.

HWPX는 zip이고 본문은 Contents/section*.xml에 있다. 표는 hp:tbl, 칸은 hp:tc이며
칸 위치는 hp:cellAddr(colAddr, rowAddr), 병합은 hp:cellSpan(colSpan, rowSpan)에 있다.
글자는 hp:p > hp:run > hp:t 에 있다.

    python tools/hwpx.py 양식.hwpx        # 표·칸 구조를 출력 (양식 분석용)
"""

import re
import zipfile
from pathlib import Path

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


NS = {"hp": HP}


def q(tag):
    return f"{{{HP}}}{tag}"


class Cell:
    def __init__(self, table, el):
        self.table = table
        self.el = el
        addr = el.find("hp:cellAddr", NS)
        span = el.find("hp:cellSpan", NS)
        self.row, self.col = int(addr.get("rowAddr")), int(addr.get("colAddr"))
        self.rowspan, self.colspan = int(span.get("rowSpan")), int(span.get("colSpan"))

    @property
    def text(self):
        """이 칸 자신의 글자(칸 안에 든 다른 표의 글자는 빼고)."""
        sub = self.el.find("hp:subList", NS)
        if sub is None:
            return ""
        lines = []
        for p in sub.findall("hp:p", NS):
            lines.append("".join(t.text or "" for t in p.iter(q("t")) if self._own(t)))
        return "\n".join(lines).strip()

    def _own(self, t):
        node = t.getparent()
        while node is not None and node is not self.el:
            if node.tag == q("tbl"):
                return False
            node = node.getparent()
        return True

    @property
    def key(self):
        """라벨 비교용: 공백·기호를 뺀 글자."""
        return re.sub(r"[\s:：·.\-()\[\]※*]", "", self.text)

    def set_text(self, value, char_ref=None, para_ref=None):
        """칸 글자를 value로 바꾼다. 첫 문단의 첫 run만 남기고 나머지 문단·글자는 지운다.

        char_ref를 주면 그 글자 모양(charPrIDRef)을 쓴다(양식의 파란 예시 글씨 색을 따라가지 않게).
        para_ref를 주면 그 문단 모양(paraPrIDRef, 정렬 등)을 쓴다(같은 열의 줄끼리 정렬을 맞출 때).
        """
        sub = self.el.find("hp:subList", NS)
        paras = sub.findall("hp:p", NS)
        first = paras[0]
        if para_ref is not None:
            first.set("paraPrIDRef", para_ref)
        for p in paras[1:]:
            if p.find(".//hp:tbl", NS) is None:
                sub.remove(p)
        runs = first.findall("hp:run", NS)
        run = runs[0] if runs else etree.SubElement(first, q("run"))
        for r in runs[1:]:
            if r.find(".//hp:tbl", NS) is None:
                first.remove(r)
        for child in list(run):
            if child.tag == q("t"):
                run.remove(child)
        if char_ref is not None:
            run.set("charPrIDRef", char_ref)
        t = etree.SubElement(run, q("t"))
        t.text = "" if value is None else str(value)
        ls = first.find(
            "hp:linesegarray", NS
        )  # 줄 배치 캐시. 지우면 한글이 다시 계산한다
        if ls is not None:
            first.remove(ls)

    def para_ref(self):
        p = self.el.find("hp:subList/hp:p", NS)
        return p.get("paraPrIDRef") if p is not None else None

    def char_ref(self):
        run = self.el.find(".//hp:run", NS)
        return run.get("charPrIDRef") if run is not None else None

    def paragraphs(self):
        """이 칸 자신의 문단들(칸 안 다른 표의 문단 제외)."""
        sub = self.el.find("hp:subList", NS)
        return [] if sub is None else sub.findall("hp:p", NS)

    def append_line(self, value, char_ref=None):
        """칸 맨 아래에 문단 하나를 더해 value를 쓴다(첫 문단의 문단 모양을 따른다)."""
        import copy

        paras = self.paragraphs()
        new = copy.deepcopy(paras[-1])
        for tbl in new.findall(".//hp:tbl", NS):
            tbl.getparent().remove(tbl)
        paras[-1].addnext(new)
        self.set_para_text(new, value, char_ref)

    def insert_line_before(self, para, value):
        """para 문단 바로 앞에 같은 모양의 문단을 넣어 value를 쓴다."""
        import copy

        new = copy.deepcopy(para)
        for tbl in new.findall(".//hp:tbl", NS):
            tbl.getparent().remove(tbl)
        para.addprevious(new)
        self.set_para_text(new, value)

    @staticmethod
    def para_text(p):
        """문단 자신의 글자(문단 안에 든 표의 글자는 빼고)."""
        return "".join(
            t.text or "" for t in p.iter(q("t")) if next(t.iterancestors(q("p"))) is p
        )

    @staticmethod
    def set_para_text(p, value, char_ref=None):
        """문단 하나의 글자를 value로 바꾼다(첫 run의 글자 모양 유지)."""
        runs = [r for r in p.findall("hp:run", NS) if r.find(".//hp:tbl", NS) is None]
        run = runs[0] if runs else etree.SubElement(p, q("run"))
        for r in runs[1:]:
            p.remove(r)
        for child in list(run):
            if child.tag == q("t"):
                run.remove(child)
        if char_ref is not None:
            run.set("charPrIDRef", char_ref)
        t = etree.SubElement(run, q("t"))
        t.text = value
        ls = p.find("hp:linesegarray", NS)
        if ls is not None:
            p.remove(ls)


class Table:
    def __init__(self, doc, el, index):
        self.doc, self.el, self.index = doc, el, index
        self.rows, self.cols = int(el.get("rowCnt")), int(el.get("colCnt"))
        self.cells = [Cell(self, tc) for tc in el.iter(q("tc")) if self._own(tc)]

    def _own(self, tc):
        node = tc.getparent()
        while node is not None and node.tag != q("tbl"):
            node = node.getparent()
        return node is self.el

    def at(self, row, col):
        """(row, col)을 덮는 칸 (병합 고려)."""
        for c in self.cells:
            if c.row <= row < c.row + c.rowspan and c.col <= col < c.col + c.colspan:
                return c
        return None

    def row_cells(self, row):
        return sorted(
            {id(c): c for c in self.cells if c.row <= row < c.row + c.rowspan}.values(),
            key=lambda c: c.col,
        )

    def insert_row_after(self, row):
        """row 줄을 복제해 바로 아래에 빈 줄을 넣는다. 아래 칸 주소·위에서 내려오는 병합·표 크기를 맞춘다."""
        import copy

        tr = next(
            tr
            for tr in self.el.findall("hp:tr", NS)
            if any(
                int(tc.find("hp:cellAddr", NS).get("rowAddr")) == row
                for tc in tr.findall("hp:tc", NS)
            )
        )
        clone = copy.deepcopy(tr)
        height = 0
        for tc in list(clone.findall("hp:tc", NS)):
            span = tc.find("hp:cellSpan", NS)
            if (
                int(span.get("rowSpan")) > 1
            ):  # 아래로 이어지는 병합 칸은 원래 칸을 늘린다
                clone.remove(tc)
                continue
            tc.find("hp:cellAddr", NS).set("rowAddr", str(row + 1))
            sz = tc.find("hp:cellSz", NS)
            if sz is not None:
                height = max(height, int(sz.get("height", "0")))
            Cell(self, tc).set_text("")
        for c in self.cells:
            if c.row > row:
                c.el.find("hp:cellAddr", NS).set("rowAddr", str(c.row + 1))
            elif c.row + c.rowspan - 1 >= row and c.rowspan > 1:
                c.el.find("hp:cellSpan", NS).set("rowSpan", str(c.rowspan + 1))
        tr.addnext(clone)
        self.el.set("rowCnt", str(self.rows + 1))
        sz = self.el.find("hp:sz", NS)
        if sz is not None and height:
            sz.set("height", str(int(sz.get("height", "0")) + height))
        self.__init__(self.doc, self.el, self.index)


class Document:
    def __init__(self, path):
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as z:
            self.files = {i.filename: z.read(i.filename) for i in z.infolist()}
            self.infos = {i.filename: i for i in z.infolist()}
        self.sections = {
            name: etree.fromstring(self.files[name])
            for name in sorted(self.files)
            if re.fullmatch(r"Contents/section\d+\.xml", name)
        }
        self.tables = [
            Table(self, el, i)
            for i, el in enumerate(
                e for root in self.sections.values() for e in root.iter(q("tbl"))
            )
        ]

    def body_paragraphs(self):
        """표 밖 본문 문단 글자(양식 제목·안내문 찾기용)."""
        out = []
        for root in self.sections.values():
            for p in root.iter(q("p")):
                if any(a.tag == q("tc") for a in p.iterancestors()):
                    continue
                txt = "".join(t.text or "" for t in p.iter(q("t")))
                if txt.strip():
                    out.append(txt.strip())
        return out

    def save(self, path):
        for name, root in self.sections.items():
            self.files[name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
        self.files.pop(
            "Preview/PrvText.txt", None
        )  # 미리보기 글자는 옛 내용이라 뺀다(한글이 다시 만든다)
        self.files.pop("Preview/PrvImage.png", None)
        with zipfile.ZipFile(path, "w") as z:
            for name, data in self.files.items():
                # mimetype은 압축하지 않고 맨 앞에 둔다(HWPX/OCF 규칙)
                ctype = (
                    zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
                )
                z.writestr(
                    self.infos.get(name, zipfile.ZipInfo(name)).filename,
                    data,
                    compress_type=ctype,
                )
        return Path(path)


def dump(path):
    doc = Document(path)
    print(f"# {Path(path).name}")
    for t in doc.tables:
        print(f"  [표 {t.index}] {t.rows}행 x {t.cols}열, 칸 {len(t.cells)}개")
        for r in range(t.rows):
            cells = [c for c in t.row_cells(r) if c.row == r]
            if cells:
                print(
                    f"    r{r}: "
                    + " | ".join(
                        f"c{c.col}{'' if c.colspan == 1 else f'+{c.colspan}'}{'' if c.rowspan == 1 else f'v{c.rowspan}'}"
                        f"「{c.text[:18].replace(chr(10), '/')}」"
                        for c in cells
                    )
                )
