"""연결 설정을 인스턴스별로 소유하는 Studio 클라이언트."""

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app.adapters.files import write_json
from app.adapters.studio_errors import (
    InputError,
)
from app.adapters.studio_errors import (
    NotQuote as NotQuote,
)
from app.adapters.studio_errors import (
    StudioError as StudioError,
)
from app.adapters.studio_gyeolui import (
    from_steps as gyeolui_steps,
)
from app.adapters.studio_gyeolui import (
    progress as gyeolui_progress,
)
from app.adapters.studio_pumui import from_steps, output_text, partial
from app.config import ROOT, rules

PROFILES = {"pumui": "studio", "gyeolui": "gyeolui_studio"}


class StudioClient:
    def __init__(self, root=ROOT, runtime=None):
        self.root = Path(root)
        self.runtime = runtime if runtime is not None else rules()["runtime"]
        self.base = (
            self.load_env().get("UPSTAGE_BASE_URL") or self.runtime["api_base_url"]
        )

    def limits(self):
        """API 입력 제한(rules.json runtime.limits): 지원 형식, 파일 크기, 쪽 수."""
        return self.runtime["limits"]

    def check_input(self, path):
        """API 명세의 입력 제한을 미리 검사한다. 걸리면 InputError(화면에 그대로 보여 줄 문장)."""
        lim, path = self.limits(), Path(path)
        msg = lim["messages"]
        if path.suffix.lower() not in lim["formats"]:
            raise InputError(
                msg["format"].format(
                    ext=path.suffix or "?", formats=", ".join(sorted(lim["formats"]))
                )
            )
        size = path.stat().st_size
        if size > lim["max_file_bytes"]:
            raise InputError(
                msg["size"].format(
                    mb=size // 1_000_000, max_mb=lim["max_file_bytes"] // 1_000_000
                )
            )
        if path.suffix.lower() == ".pdf":
            try:
                from pypdf import PdfReader

                pages = len(PdfReader(str(path)).pages)
            except Exception:  # 쪽 수를 못 세면 Studio에 맡긴다
                pages = 0
            if pages > lim["max_pages"]:
                raise InputError(
                    msg["pages"].format(pages=pages, max_pages=lim["max_pages"])
                )

    def load_env(self):
        """.env를 읽고, 같은 이름의 환경변수가 있으면 그 값을 쓴다(빈 값은 없는 것으로 본다)."""
        env = {}
        p = self.root / ".env"
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        env.update(
            {
                k: v
                for k, v in os.environ.items()
                if v
                and (
                    k.startswith(("UPSTAGE_", "PUMUI_", "GYEOLUI_"))
                    or k in ("AGENT_ID", "CONFIG_ID")
                )
            }
        )
        return {k: v for k, v in env.items() if v}

    def profile(self, kind="pumui"):
        return self.runtime.get(PROFILES[kind]) or {}

    def settings(self, kind="pumui"):
        """워크플로(kind)의 API 키·Agent ID·Config ID·Project ID를 정한다. 빠진 값 이름 목록도 돌려준다.

        워크플로마다 환경변수(rules.json runtime.<워크플로>.env의 이름) > .env > rules.json 기본값 순으로 쓴다.
        품의와 결의는 Agent가 다르므로 AGENT_ID 하나를 같이 쓰지 않는다.
        """
        env = self.load_env()
        self.base = env.get("UPSTAGE_BASE_URL") or self.runtime["api_base_url"]
        st = self.profile(kind)
        names = st["env"]
        first = lambda field: next((env[n] for n in names[field] if env.get(n)), None)
        config_id = first("config_id") or st.get("config_id")
        s = {
            "key": first("key"),
            "agent_id": first("agent_id") or st.get("agent_id"),
            "config_id": None
            if str(config_id).lower() in ("latest", "none", "")
            else str(config_id),
            "project_id": first("project_id") or st.get("project_id"),
        }
        missing = ([names["key"][-1]] if not s["key"] else []) + (
            [names["agent_id"][0]] if not s["agent_id"] else []
        )
        return s, missing

    def run_file(self, path, out_dir, progress=None, kind="pumui", on_job=None):
        """문서 하나를 Studio 워크플로(kind)로 돌려 out_dir/<이름>.json에 저장하고 (결과, job_id)를 돌려준다.
        progress(done, found, finished)는 품의 견적서 업로드 진행 표시용, on_job(job_id)는 실행이 접수되면 한 번 불린다."""
        s, missing = self.settings(kind)
        if missing:
            raise RuntimeError(
                f"{self.root / '.env'}에 {', '.join(missing)} 값이 없어요."
            )
        run = lambda agent, config: self.run_one(  # noqa: E731
            Path(path), s["key"], agent, config, progress, kind, on_job, s["project_id"]
        )
        try:
            result, job_id = run(s["agent_id"], s["config_id"])
        except StudioError as e:
            # .env의 Agent ID가 이 프로젝트에 없는 예전 Agent면(404) rules.json의 기본 Agent로 한 번 다시 돈다
            st = self.profile(kind)
            default = st.get("agent_id")
            if e.kind != "not_found" or not default or default == s["agent_id"]:
                raise
            print(f"[studio] .env의 Agent를 찾지 못해 설정 파일의 Agent({default})로 다시 실행합니다.", file=sys.stderr)
            result, job_id = run(default, None if st.get("config_id") in (None, "", "latest") else str(st["config_id"]))
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / f"{Path(path).stem}.json", result)
        return result, job_id

    def call(self, method, path, key, body=None, headers=None, project=None):
        """API 한 번 호출. 429·5xx는 rules.json runtime.retry대로 기다렸다 다시 보낸다(429는 Retry-After를 따른다).
        조회(GET)는 연결 오류도 다시 시도한다. 실행 생성(POST)의 연결 오류는 이미 접수됐을 수 있어 다시 보내지 않는다."""
        rt = self.runtime["retry"]
        for attempt in range(rt["attempts"]):
            req = urllib.request.Request(self.base + path, data=body, method=method)
            req.add_header("Authorization", f"Bearer {key}")
            if project:
                req.add_header("x-project-id", project)
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            last = attempt == rt["attempts"] - 1
            wait = min(rt["max_seconds"], rt["base_seconds"] * 2**attempt)
            try:
                with urllib.request.urlopen(req, timeout=120) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if last or not (e.code == 429 or e.code >= 500):
                    raise StudioError(
                        e.code, detail, f"{method} {path} → HTTP {e.code}: {detail}"
                    ) from None
                after = e.headers.get("Retry-After") if e.headers else None
                if after and after.isdigit():
                    wait = max(wait, min(rt["max_seconds"], int(after)))
            except (urllib.error.URLError, TimeoutError):
                if last or method != "GET":
                    raise
            time.sleep(wait)

    def upload(self, path, key, project=None):
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nuser_data\r\n'.encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode("utf-8")
            + path.read_bytes()
            + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        res = self.call(
            "POST",
            "/files",
            key,
            b"".join(parts),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            project,
        )
        return res["id"]

    def run_one(
        self,
        path,
        key,
        agent_id,
        config_id,
        progress=None,
        kind="pumui",
        on_job=None,
        project=None,
    ):
        """progress(done, found, finished)를 주면 단계가 끝날 때마다 알려 준다(화면 진행 표시용).
        project가 있으면 업로드·실행·조회 모두에 x-project-id를 보낸다."""
        self.check_input(path)
        st = self.profile(kind)
        include = st.get("include") or ["last"]
        poll_include = st.get("poll_include") or include
        tell = progress or (lambda *a: None)
        if kind == "gyeolui":  # 결의는 노드 구성이 달라 진행 해석도 따로 한다
            parse_progress = gyeolui_progress
        else:
            parse_progress = partial
        if (
            progress
        ):  # 진행을 보여 줄 때는 실행 중에도 단계별 결과(include all)를 받는다
            poll_include = include
        tell([], {}, False)
        file_id = self.upload(path, key, project)
        tell(["upload"], {}, False)
        req = {
            "model": agent_id,
            "include": include,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_file", "file_id": file_id}],
                }
            ],
        }
        if config_id:
            req["config_id"] = config_id
        job = self.call(
            "POST",
            "/responses",
            key,
            json.dumps(req).encode(),
            {"Content-Type": "application/json"},
            project,
        )
        job_id = job["id"]
        if on_job:
            on_job(job_id)
        started = time.time()
        rt = self.runtime
        timeout = st.get("api_timeout_seconds") or rt["api_timeout_seconds"]
        query = lambda inc: "&".join(f"include[]={x}" for x in inc)
        while job.get("status") in ("queued", "in_progress", None):
            if time.time() - started > timeout:
                raise RuntimeError(f"{job_id}: {timeout}초 넘게 끝나지 않음")
            time.sleep(rt["api_poll_seconds"])
            job = self.call(
                "GET",
                f"/responses/{job_id}?{query(poll_include)}",
                key,
                project=project,
            )
            if (
                progress
            ):  # Jobs API 조회 결과에서 끝난 단계와 읽어 낸 값을 화면에 넘긴다
                done, found = parse_progress(job, st)
                tell(["upload"] + done, found, False)
        if job.get("status") != "completed":
            raise RuntimeError(
                f"{job_id}: status={job.get('status')} (failed면 Studio에서 Agent 설정 확인)"
            )
        if poll_include != include:  # 끝난 뒤 단계별 결과를 한 번만 받는다
            job = self.call(
                "GET", f"/responses/{job_id}?{query(include)}", key, project=project
            )
        steps = [o for o in job.get("output") or [] if o.get("step_type")]
        if progress:
            done, found = parse_progress(job, st)
            tell(["upload"] + done, found, True)
        if kind == "gyeolui":
            return gyeolui_steps(steps, st, job_id), job_id
        if steps:
            return from_steps(steps, st, job_id), job_id
        text = output_text(job)
        try:
            return json.loads(text), job_id
        except json.JSONDecodeError:
            raise RuntimeError(
                f"{job_id}: output_text가 JSON이 아님: {text[:200]}"
            ) from None


# Functions preserve the previous Python API without process-global connection settings.
def settings(kind="pumui"):
    return StudioClient().settings(kind)


def profile(kind="pumui"):
    return StudioClient().profile(kind)


def check_input(path):
    return StudioClient().check_input(path)


def run_file(path, out_dir, progress=None, kind="pumui", on_job=None):
    return StudioClient().run_file(path, out_dir, progress, kind, on_job)


def run_one(
    path,
    key,
    agent_id,
    config_id,
    progress=None,
    kind="pumui",
    on_job=None,
    project=None,
):
    return StudioClient().run_one(
        path, key, agent_id, config_id, progress, kind, on_job, project
    )


DOC_EXT = set(rules()["runtime"]["limits"]["formats"])
