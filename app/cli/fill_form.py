"""어떤 품의서 양식(HWP·HWPX·DOCX)이 와도 칸을 찾아 품의 agent 결과로 채운다.

    python tools/fill_form.py 양식.hwp <결과.json 또는 _resolved.json> <구매건.json> [--selection 선정.json] [--out out]

순서
  1) 한글로 양식을 HWPX로 바꾼다(tools/hwp_com.py).
  2) 파일 안에 여러 양식이 있으면 품의서 하나를 고른다(config/form_roles.json form_title).
  3) 품목 표를 찾는다: 머리글 줄의 칸 이름이 item_table.columns 동의어와 맞으면 품목 표.
     품목 수(+부가세 줄)만큼 줄을 채우고, 모자라면 줄을 늘리고, 남는 줄과 예시 글씨는 지운다.
  4) 나머지 칸에서 라벨(fields 동의어)을 찾아 오른쪽 값 칸이나 같은 칸의 '라벨 : 값' 자리에 쓴다.
  5) 값이 없는 라벨, 사전에 없는 라벨, 놓을 곳이 없는 값은 고치지 않고 보고서에 담아 담당자에게 묻는다.
  6) 한글로 HWP·PDF와 첫 쪽 그림을 만든다.

라벨·형식·자리표시 규칙은 전부 config/form_roles.json에 있다. 코드에는 칸을 찾고 쓰는 방법만 둔다.
지출결의서는 같은 방법에 사전만 바꿔 쓴다(config/form_roles_gyeolui.json, fill(..., roles_name="form_roles_gyeolui")).
"""

import argparse
import json
import sys
from pathlib import Path

from app.adapters.fill_form import fill
from app.config import ROOT


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("form")
    ap.add_argument("result")
    ap.add_argument("case")
    ap.add_argument("--selection")
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument(
        "--use-hwp",
        action="store_true",
        help="한글 자동화로 .hwp 양식 변환·HWP/PDF 출력 (한글 보안 확인창이 뜰 수 있음). 기본은 한글 없이 동작",
    )
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    from app.adapters.response import unwrap
    from app.domain.harness import normalize

    data = json.loads(Path(a.result).read_text(encoding="utf-8"))
    result = (
        data["result"] if "resolution_log" in data else normalize(unwrap(data) or data)
    )
    case = json.loads(Path(a.case).read_text(encoding="utf-8"))
    selection = (
        json.loads(Path(a.selection).read_text(encoding="utf-8"))
        if a.selection
        else None
    )

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    form = Path(a.form)
    stem = f"{case.get('case_id', 'case')}_{form.stem}"
    if form.suffix.lower() != ".hwpx":
        if not a.use_hwp:
            sys.exit(
                f"{form.name}: HWPX 양식만 한글 없이 처리할 수 있어요. 한글에서 '다른 이름으로 저장 → HWPX'로 저장해 주세요"
                " (또는 --use-hwp로 한글 자동화 사용)."
            )
        from app.adapters.hwp_com import convert

        form = convert(form, out / f"{form.stem}.form.hwpx")
    doc, report = fill(form, result, case, selection)
    filled = doc.save(out / f"{stem}.hwpx")
    from app.adapters.hwpx_preview import preview

    outputs = {"hwpx": str(filled), "preview_pdf": str(preview(filled, pdf=True))}
    if a.use_hwp:
        from app.adapters.hwp_com import Hwp

        with Hwp() as h:
            outputs["hwp"] = str(h.convert(filled, out / f"{stem}.hwp"))
    report["outputs"] = outputs
    (out / f"{stem}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
