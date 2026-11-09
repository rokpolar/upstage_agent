"""단일 공유 작업의 버전 있는 SQLite 스냅샷."""

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class StateStore:
    VERSION = 1

    def __init__(self, path, root=None):
        self.path = Path(path)
        self.root = Path(
            root
            or (
                self.path.parent.parent
                if self.path.parent.name == "out"
                else self.path.parent
            )
        ).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Existing or unknown files are never replaced on errors.
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.VERSION):
                raise RuntimeError(f"지원하지 않는 상태 DB 버전: {version}")
            db.execute(
                "CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
            )
            db.execute(f"PRAGMA user_version={self.VERSION}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM workspace WHERE id=1").fetchone()
        if row is None:
            return None
        state = json.loads(row[0])
        required = {"case": dict, "quotes": dict, "g": dict}
        if not isinstance(state, dict) or any(
            not isinstance(state.get(k), t) for k, t in required.items()
        ):
            raise RuntimeError("저장된 작업 상태의 형식이 올바르지 않습니다.")
        for key in ("selection", "handoff"):
            if key not in state or (
                state[key] is not None and not isinstance(state[key], dict)
            ):
                raise RuntimeError("저장된 작업 상태의 형식이 올바르지 않습니다.")
        for entry in state["quotes"].values():
            self.validate_review(
                entry.get("state") if isinstance(entry, dict) else None
            )
        for entry in state["g"].values():
            if not isinstance(entry, dict) or entry.get("status") not in {
                "done",
                "error",
            }:
                raise RuntimeError("저장된 결의 상태의 형식이 올바르지 않습니다.")
            if entry["status"] == "done":
                self.validate_review(entry.get("state"))
        old_root = state.pop("_root", None)
        if old_root and old_root != str(self.root):
            state = self.rebase(state, Path(old_root))
        state["texts"] = {}
        return state

    @staticmethod
    def validate_review(state):
        fields = {
            "result": dict,
            "questions": list,
            "resolution_log": list,
            "acknowledged": list,
            "hold": bool,
        }
        if not isinstance(state, dict) or any(
            not isinstance(state.get(k), t) for k, t in fields.items()
        ):
            raise RuntimeError("저장된 검토 상태의 형식이 올바르지 않습니다.")

    def rebase(self, value, old_root, key=""):
        """로컬·Docker 사이를 바꿔 실행해도 저장 파일을 같은 상대 경로로 찾는다."""
        if isinstance(value, dict):
            return {k: self.rebase(v, old_root, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.rebase(v, old_root) for v in value]
        if isinstance(value, str) and key in {"file", "source", "pdf", "hwpx", "form"}:
            path = Path(value)
            if path.is_absolute() and path.is_relative_to(old_root):
                return str(self.root / path.relative_to(old_root))
        return value

    def save(self, state):
        snapshot = copy.deepcopy({k: v for k, v in state.items() if k != "texts"})
        snapshot["g"] = {
            k: v for k, v in snapshot["g"].items() if v.get("status") != "running"
        }
        for entry in snapshot["g"].values():
            entry.pop("progress", None)
        snapshot["_root"] = str(self.root)
        payload = json.dumps(snapshot, ensure_ascii=False)
        with self.connect() as db:
            db.execute(
                "INSERT INTO workspace(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (payload,),
            )
