# 품의·결의 Agent 컨테이너. 화면과 API를 tools/server.py 하나가 같이 제공한다.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOST=0.0.0.0 \
    PDF_BROWSER=/usr/bin/chromium \
    PDF_BROWSER_FLAGS="--no-sandbox --disable-dev-shm-usage"

# 양식 PDF 미리보기(hwpx_preview → html_to_pdf)에 쓸 Chromium과 한글 글꼴
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-nanum fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY tools/ ./tools/
COPY config/ ./config/
COPY ui/ ./ui/
COPY forms/ ./forms/
COPY templates/ ./templates/
COPY examples/ ./examples/

# 컨테이너 안에서 쓰는 폴더(compose.yaml이 호스트 폴더를 붙인다)
RUN mkdir -p data_upload/gyeolui results_upload results_gyeolui_upload forms/uploads out

EXPOSE 8766
CMD ["python", "-B", "tools/server.py"]
