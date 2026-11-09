"""호환 실행 진입점. 구현은 app 패키지에 있다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.adapters.studio import *

if __name__ == "__main__":
    from app.cli.run_api import main

    main()
