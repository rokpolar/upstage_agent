"""Word 품의서 양식(forms/word/품의서.docx)을 만든다. 표준 라이브러리만 쓴다.

    python forms/word/make_form.py

기관 양식 forms/web/cne/품의서양식.hwpx(충청남도교육청 품의서)의 배치를 그대로 옮겼다:
쪽 여백, 결재란(담당·팀장·센터장), 제목·본문 문구, 1~6 항목, 세부내용 표(구분·내역·금액(원)·비고), 글꼴(굴림체).
칸 너비·줄 높이는 HWPX 값(HWPUNIT)을 twip으로 바꾼 것(÷5)이다.
문구가 HWPX 양식과 같으므로 config/form_roles.json의 라벨 사전으로 그대로 채워진다.
"""
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).with_name("품의서.docx")
FONT = "굴림체"
SIZE = 22            # 11pt (반 포인트 단위)
TITLE_SIZE = 40      # 20pt
PAGE = {"w": 11906, "h": 16838, "top": 1984, "bottom": 1701, "left": 1134, "right": 1134}  # A4, 35·30·20·20mm
MAIN_COLS = [4102, 2184, 1092, 1092, 1091]
ITEM_COLS = [2204, 3074, 1188, 1810]
BORDER = '<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'


def run(text, bold=False, size=None):
    pr = (f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}"/>'
          f'{"<w:b/>" if bold else ""}<w:sz w:val="{size or SIZE}"/><w:szCs w:val="{size or SIZE}"/></w:rPr>')
    return f'<w:r>{pr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>' if text else f'<w:r>{pr}</w:r>'


def para(text="", align="both", bold=False, size=None):
    return f'<w:p><w:pPr><w:jc w:val="{align}"/></w:pPr>{run(text, bold, size)}</w:p>'


def cell(width, body, span=1, vmerge=None, valign="center"):
    pr = f'<w:tcW w:w="{width}" w:type="dxa"/>'
    if span > 1:
        pr += f'<w:gridSpan w:val="{span}"/>'
    if vmerge:
        pr += '<w:vMerge w:val="restart"/>' if vmerge == "restart" else "<w:vMerge/>"
    pr += f'<w:vAlign w:val="{valign}"/>'
    return f"<w:tc><w:tcPr>{pr}</w:tcPr>{body}</w:tc>"


def row(height, cells, rule="atLeast"):
    return f'<w:tr><w:trPr><w:trHeight w:val="{height}" w:hRule="{rule}"/></w:trPr>{"".join(cells)}</w:tr>'


def table(cols, rows, align="left"):
    borders = "".join(BORDER.format(s=s) for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    pr = (f'<w:tblPr><w:tblW w:w="{sum(cols)}" w:type="dxa"/><w:jc w:val="{align}"/><w:tblBorders>{borders}</w:tblBorders>'
          '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr>')
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in cols)
    return f'<w:tbl>{pr}<w:tblGrid>{grid}</w:tblGrid>{"".join(rows)}</w:tbl>'


def build():
    c = MAIN_COLS
    item = table(ITEM_COLS, [
        row(483, [cell(w, para(t, "center")) for w, t in zip(ITEM_COLS, ["구분", "내역", "금액(원)", "비고"])]),
        row(1497, [cell(w, para("", "center")) for w in ITEM_COLS]),
    ], align="center")
    body = "".join([
        para(" "),
        para("  제 목 : 〇〇〇 지출의 건.", bold=True),
        para(" "),
        para("  〇〇〇〇 다음과 같이 지출하고자 합니다."),
        para(""), para(""), para(""),
        para("- 다            음 -", "center"),
        para(""),
        para("        1. 시행일자 :  "),
        para("        2. 예 산 액 : 金      整 (￦       )"),
        para("        3. 지 출 처 :  "),
        para("        4. 지출방법 :  "),
        para("        5. 세부내용 : "),
        item,
        para("", "center"),
        para("        6. 예산항목 : "),
        para("끝.", "left"),
    ])
    sign = "".join(cell(w, para(t, "center")) for w, t in zip(c[2:], ["담 당", "팀 장", "센터장"]))
    main = table(c, [
        row(483, [cell(c[0], para("문서번호: ") + para(" 작성일자: ") + para(" 작 성 자:"), vmerge="restart"),
                  cell(c[1], para(""), vmerge="restart"), sign]),
        row(896, [cell(c[0], para(""), vmerge="continue"), cell(c[1], para(""), vmerge="continue")]
            + [cell(w, para("", "center")) for w in c[2:]]),
        row(10269, [cell(sum(c), body, span=5, valign="top")]),
    ])
    sect = (f'<w:sectPr><w:pgSz w:w="{PAGE["w"]}" w:h="{PAGE["h"]}"/>'
            f'<w:pgMar w:top="{PAGE["top"]}" w:right="{PAGE["right"]}" w:bottom="{PAGE["bottom"]}" w:left="{PAGE["left"]}" '
            'w:header="851" w:footer="851" w:gutter="0"/></w:sectPr>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           f'{para("품     의     서", "center", True, TITLE_SIZE)}{para("", "center", True, TITLE_SIZE)}{main}{para("")}{sect}'
           f'</w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults>'
              f'<w:rPrDefault><w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
              f'<w:sz w:val="{SIZE}"/><w:szCs w:val="{SIZE}"/><w:lang w:val="ko-KR" w:eastAsia="ko-KR"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
              '</w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '</w:styles>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>',
        "word/document.xml": doc,
        "word/styles.xml": styles,
    }
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return OUT


if __name__ == "__main__":
    print(build())
