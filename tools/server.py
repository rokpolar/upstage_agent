"""기존 웹 서버 실행 명령과의 호환 진입점."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.cli.server import main

if __name__ == "__main__":
    main()
