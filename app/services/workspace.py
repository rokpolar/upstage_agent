"""공유 작업 수명, 원자적 변경과 외부 작업의 버전 확인."""

import copy
import json
import shutil
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from app.adapters.files import inside
from app.adapters.memory_store import MemoryStore
from app.adapters.store import StateStore
from app.adapters.studio import StudioClient
from app.config import Settings
from app.domain import gyeolui
from app.domain.select_vendor import finalize, recommend
from app.models import Progress, Workspace

from .expenses import ExpensesService
from .forms import FormsService
from .quotes import QuotesService


class ConflictError(RuntimeError):
    pass


@dataclass
class Dependencies:
    studio: StudioClient
    store: StateStore | MemoryStore


class Application(QuotesService, FormsService, ExpensesService):
    def __init__(self, settings=None, dependencies=None):
        self.settings = settings or Settings.load()
        self.base_settings = self.settings
        self.temporary = None
        if dependencies is None or isinstance(dependencies.store, MemoryStore):
            self.temporary = tempfile.TemporaryDirectory(prefix="upstage-work-")
            root = Path(self.temporary.name)
            # Only shipped resources are copied. Historical uploads/results are ignored.
            for name in ("config", "examples", "templates", "ui", "forms"):
                shutil.copytree(
                    self.settings.root / name,
                    root / name,
                    ignore=shutil.ignore_patterns("uploads", "__pycache__"),
                )
            self.settings = replace(
                self.settings, root=root, database=root / "out/state.sqlite3"
            )
        self.root = self.settings.root
        self.cfg = self.settings.ui
        self.gcfg = self.cfg["gyeolui_sets"]
        self.out = self.root / self.cfg["output_dir"]
        deps = dependencies or Dependencies(
            StudioClient(self.base_settings.root, self.settings.runtime),
            MemoryStore(),
        )
        self.studio, self.store = deps.studio, deps.store
        self.lock = threading.RLock()
        self.version = 0
        self.progress: dict[str, Progress] = {}
        self.threads = []
        self.state: Workspace = self.store.load() or {
            "case": json.loads(
                (self.root / self.cfg["default_case"]).read_text(encoding="utf-8")
            ),
            "quotes": {},
            "selection": None,
            "texts": {},
            "g": {},
            "handoff": None,
        }
        self.load_quotes()
        self.load_gyeolui()
        self.store.save(self.state)

    def execute(self, operation, *args, expected=None):
        """Persist first, acknowledge second; restore memory if any part fails."""
        with self.lock:
            if expected is not None and expected != self.version:
                raise ConflictError("작업 내용이 변경되었습니다. 다시 시도하세요.")
            before = copy.deepcopy(self.state)
            try:
                result = operation(*args)
                self.store.save(self.state)
            except BaseException:
                self.state = before
                raise
            self.version += 1
            return copy.deepcopy(result)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state), self.version

    def invalidate_handoff(self):
        """편집 뒤 이전 품의 생성 결과를 새 결의의 기준으로 사용하지 않는다."""
        self.state["handoff"] = None
        for quote in self.state["quotes"].values():
            quote["fill"] = None

    def studio_missing(self, kind="pumui"):
        return self.studio.settings(kind)[1]

    def form_path(self, relative):
        if not isinstance(relative, str):
            raise ValueError("양식 경로가 필요합니다.")
        path = (self.root / relative).resolve()
        dirs = set(self.cfg["form_dirs"] + self.cfg["gyeolui_form_dirs"])
        exts = {
            self.cfg.get("form_ext", ".hwpx"),
            self.cfg.get("gyeolui_form_ext", ".hwpx"),
            ".hwpx",
        }
        if (
            path.suffix.lower() not in exts
            or not any(inside(path, self.root / d) for d in dirs)
            or not path.is_file()
        ):
            raise FileNotFoundError("양식을 찾지 못했습니다.")
        return path

    def source_path(self, gid):
        with self.lock:
            entry = self.state["g"].get(gid)
            if not entry or not entry["source"]:
                raise FileNotFoundError("not found")
            path = Path(entry["source"]).resolve()
            if (
                not any(inside(path, self.root / entry[1]) for entry in self.gcfg)
                or not path.is_file()
            ):
                raise FileNotFoundError("not found")
            return path

    def set_case(self, case):
        from app.config import config

        for key, *_ in config("ui")["case"]["fields"]:
            if key in case and not isinstance(case[key], str):
                raise ValueError(f"품의 정보 '{key}'는 문자열이어야 합니다.")
        self.state["case"] = case
        for qid in self.state["quotes"]:
            self.check(qid)
        self.invalidate_handoff()
        return {"quotes": [self.summary(i) for i in self.state["quotes"]]}

    def select(self, ids, pick, reason):
        quotes = [self.quote_for_select(i) for i in ids]
        rec, auto, _, _ = recommend(quotes)
        chosen = next((q for q in quotes if q["file"] == pick), None)
        if chosen is None:
            raise ValueError("선택한 견적이 비교 목록에 없습니다.")
        self.state["selection"] = finalize(
            quotes, rec, auto, chosen, reason.strip(), True
        )
        self.invalidate_handoff()
        return self.state["selection"]

    def g_answer(self, gid, key, value, who):
        e = self.state["g"][gid]
        if e["status"] != "done":
            raise ConflictError("처리가 완료된 뒤 답변하세요.")
        gyeolui.answer(
            e["state"],
            key,
            value,
            who
            or e["state"]["info"].get("drafter")
            or self.state["case"].get("drafter", ""),
        )
        e["fill"] = None
        return self.g_summary(gid)

    def g_info(self, gid, info):
        e = self.state["g"][gid]
        if e["status"] != "done":
            raise ConflictError("처리가 완료된 뒤 저장하세요.")
        if any(not isinstance(v, str) for v in info.values()):
            raise ValueError("결의 정보는 문자열이어야 합니다.")
        # Missing fields are unchanged, including metadata hidden from the form.
        # An explicit blank string clears only that field.
        updated = dict(e["state"]["info"])
        for key, value in info.items():
            if value.strip():
                updated[key] = value
            else:
                updated.pop(key, None)
        e["state"]["info"] = updated
        e["info_saved"] = True
        e["fill"] = None
        return self.g_summary(gid)

    def close(self):
        for thread in self.threads:
            thread.join(timeout=1)
        if self.temporary and not any(t.is_alive() for t in self.threads):
            self.temporary.cleanup()
