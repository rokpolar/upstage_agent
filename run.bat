@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.11 이상을 먼저 설치하세요: https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import lxml, pypdf" 2>nul || (echo 필요한 패키지를 설치합니다... & python -m pip install -q -r requirements.txt || (pause & exit /b 1))
if not exist .env (copy .env.example .env >nul & echo .env 파일을 만들었습니다. 견적서·증빙 업로드를 쓰려면 UPSTAGE_API_KEY를 넣으세요.)
python -B tools\server.py --open
pause
