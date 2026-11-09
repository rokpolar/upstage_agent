"""한글(HWP) 자동화로 문서 형식을 바꾼다. 어떤 양식 파일이 와도 HWPX로 맞춘 뒤 읽고 채우기 위해 쓴다.

    python tools/hwp_com.py 입력.hwp 출력.hwpx      # 확장자로 형식 결정 (config/rules.json runtime.hwp.formats)
    python tools/hwp_com.py 입력.hwpx 출력.pdf

한글이 설치된 Windows에서만 동작한다(pywin32 필요).
"""

from pathlib import Path

from app.config import rules


class Hwp:
    """한글 인스턴스 하나를 열어 여러 파일을 변환하고 닫는다."""

    def __enter__(self):
        import win32com.client

        self.cfg = rules()["runtime"]["hwp"]
        self.app = win32com.client.DispatchEx(
            self.cfg["progid"]
        )  # 새 인스턴스(사용자가 연 한글과 섞이지 않게)
        self.app.SetMessageBoxMode(self.cfg["message_box_mode"])
        return self

    def convert(self, src, dst):
        src, dst = Path(src).resolve(), Path(dst).resolve()
        fmt = self.cfg["formats"].get(dst.suffix.lower())
        if fmt is None:
            raise ValueError(
                f"지원하지 않는 출력 형식: {dst.suffix} (rules.json runtime.hwp.formats)"
            )
        if not self.app.Open(str(src), "", self.cfg["open_options"]):
            raise RuntimeError(f"한글이 파일을 열지 못함: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        ok = self.app.SaveAs(str(dst), fmt, "")
        self.app.Clear(1)  # 저장하지 않고 닫기
        if not ok or not dst.exists():
            raise RuntimeError(f"한글이 {fmt}로 저장하지 못함: {dst}")
        return dst

    def page_image(self, src, png, page=0, dpi=100):
        """문서 한 쪽을 그림으로 저장한다(채운 결과를 눈으로 확인하는 용도)."""
        src, png = Path(src).resolve(), Path(png).resolve()
        if not self.app.Open(str(src), "", self.cfg["open_options"]):
            raise RuntimeError(f"한글이 파일을 열지 못함: {src}")
        png.parent.mkdir(parents=True, exist_ok=True)
        bmp = png.with_suffix(".bmp")  # 한글은 형식 인자와 상관없이 BMP로 저장한다
        self.app.CreatePageImage(str(bmp), page, dpi, 24, "bmp")
        self.app.Clear(1)
        from PIL import Image

        Image.open(bmp).save(png)
        bmp.unlink()
        return png

    def __exit__(self, *exc):
        try:
            self.app.Quit()
        except Exception:
            pass


def convert(src, dst):
    with Hwp() as h:
        return h.convert(src, dst)
