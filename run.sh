#!/usr/bin/env sh
# 품의 Agent 실행 (macOS / Linux):  ./run.sh
cd "$(dirname "$0")" || exit 1
PY=python3; command -v $PY >/dev/null 2>&1 || PY=python
command -v $PY >/dev/null 2>&1 || { echo "Python 3.11 이상을 먼저 설치하세요: https://www.python.org/downloads/"; exit 1; }
$PY -c "import lxml, pypdf" 2>/dev/null || { echo "필요한 패키지를 설치합니다..."; $PY -m pip install -q -r requirements.txt || exit 1; }
[ -f .env ] || { cp .env.example .env; echo ".env 파일을 만들었습니다. 견적서 업로드를 쓰려면 UPSTAGE_API_KEY를 넣으세요."; }
exec $PY -B tools/server.py --open
