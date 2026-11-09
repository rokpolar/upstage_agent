import os
import subprocess
import tempfile
from pathlib import Path

from app.config import rules


class ConversionError(RuntimeError):
    """문서 미리보기를 만들지 못했다."""


def html_to_pdf(html_path, pdf_path):
    candidates = [os.environ.get("PDF_BROWSER")] + rules()["runtime"]["pdf_browsers"]
    browser = next((b for b in candidates if b and Path(b).exists()), None)
    if browser is None:
        raise ConversionError(
            "PDF 변환에 쓸 Edge/Chrome을 찾지 못했습니다. 환경변수 PDF_BROWSER나 config/rules.json runtime.pdf_browsers에 경로를 넣어 주세요."
        )
    # Each conversion owns a browser profile, including concurrent conversions.
    # This also keeps conversion processes separate from an open user browser.
    with tempfile.TemporaryDirectory(prefix="upstage-pdf-") as profile:
        try:
            subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-pdf-header-footer",
                    f"--user-data-dir={profile}",
                    *os.environ.get("PDF_BROWSER_FLAGS", "").split(),
                    f"--print-to-pdf={Path(pdf_path).resolve()}",
                    Path(html_path).resolve().as_uri(),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise ConversionError(
                "PDF 변환에 실패했습니다. 브라우저 설정을 확인하세요."
            ) from e
    if not Path(pdf_path).is_file():
        raise ConversionError("PDF 파일이 생성되지 않았습니다.")


def source_text(path):
    """텍스트가 들어 있는 PDF면 원문을 돌려준다. 스캔본이거나 pypdf가 없으면 ''."""
    if not path:
        return ""
    if Path(path).suffix.lower() != ".pdf":
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(path)).pages)
