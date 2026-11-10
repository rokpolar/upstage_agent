"""표준 라이브러리 HTTP 전송 계층. 앱 상태는 서버 인스턴스가 소유한다."""

import json
import mimetypes
import traceback
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from app.adapters.files import confined
from app.adapters.memory_store import MemoryStore
from app.services.workspace import Application, ConflictError

from .routes import Routes
from .workspaces import Workspaces


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = (
            body
            if isinstance(body, bytes)
            else json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def file(self, base, relative):
        p = confined(base, unquote(relative))
        if not p.is_file():
            raise FileNotFoundError("not found")
        ctype = (
            {  # Windows는 확장자별 형식을 레지스트리에서 읽어 .js를 text/plain으로 줄 때가 있다(모듈 스크립트가 안 뜸)
                ".hwpx": "application/hwp+zip",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".js": "text/javascript",
                ".mjs": "text/javascript",
                ".css": "text/css",
                ".html": "text/html",
                ".json": "application/json",
            }.get(p.suffix.lower())
            or mimetypes.guess_type(p.name)[0]
            or "application/octet-stream"
        )
        self.send(
            200,
            p.read_bytes(),
            ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""),
        )

    def body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n < 0:
                raise ValueError
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise ValueError("요청 JSON 형식이 올바르지 않습니다.") from None

    def dispatch(self, post=False):
        u = urlparse(self.path)
        try:
            a = self.server.app
            pool = self.server.workspaces
            if pool and u.path == "/api/workspace/new" and post:
                self.body()
                return self.send(200, {"prefix": pool.new()})
            if u.path in ("/", "/index.html"):
                return self.file(a.root / "ui", "index.html")
            if u.path.startswith("/ui/"):
                return self.file(a.root / "ui", u.path[4:])
            path = u.path
            context = nullcontext(a)
            if pool and path.startswith("/work/"):
                _, _, token, rest = path.split("/", 3)
                path = "/" + rest
                if path == "/api/workspace/close" and post:
                    self.body()
                    pool.release(token)
                    return self.send(200, {"ok": True})
                context = pool.use(token)
            elif pool and path not in ("/healthz", "/api/init"):
                raise FileNotFoundError("새 작업을 시작하세요.")
            with context as a:
                routes = Routes(a)
                if path == "/api/workspace/ping" and post:
                    self.body()
                    return self.send(200, {"ok": True})
                if post:
                    return self.send(200, routes.post(path, self.body()))
                if path.startswith("/out/"):
                    return self.file(a.out, path[5:])
                query = parse_qs(u.query)
                if path == "/api/gyeolui/source":
                    p = a.source_path(query.get("id", [""])[0])
                    return self.file(p.parent, p.name)
                return self.send(200, routes.get(path, query))
        except ConflictError as e:
            self.send(409, {"error": str(e)})
        except (FileNotFoundError, StopIteration):
            self.send(404, {"error": "not found"})
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.send(
                500, {"error": "요청을 처리하지 못했습니다. 서버 로그를 확인하세요."}
            )

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(post=True)


class Server(ThreadingHTTPServer):
    def server_close(self):
        super().server_close()
        if self.workspaces:
            self.workspaces.close()
        self.app.close()


def create_server(app=None, address=None, *, session_factory=None):
    app = app or Application()
    server = Server(address or (app.cfg["host"], app.cfg["port"]), Handler)
    server.app = app
    server.routes = Routes(app)
    if session_factory is None and isinstance(app.store, MemoryStore):
        session_factory = lambda: Application(app.base_settings)
    server.workspaces = Workspaces(session_factory) if session_factory else None
    return server
