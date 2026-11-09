"""로컬 웹 서버 실행 진입점."""

import os
import sys
import webbrowser

from app.services.workspace import Application
from app.web.server import create_server


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    app = Application()
    host = os.environ.get("APP_HOST") or app.cfg["host"]
    port = int(os.environ.get("APP_PORT") or app.cfg["port"])
    server = create_server(app, (host, port))
    url = f"http://{host}:{port}"
    print(f"품의·결의 agent 화면: {url}  끄려면 Ctrl+C", flush=True)
    if "--open" in sys.argv[1:]:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
