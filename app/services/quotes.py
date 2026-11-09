import json
import re
from pathlib import Path

from app.adapters.files import decode_data, safe_name, unique_name
from app.adapters.pdf import source_text
from app.adapters.response import unwrap
from app.adapters.studio_errors import NotQuote, StudioError
from app.config import config, fields
from app.domain.grouping import group as group_quotes
from app.domain.harness import coerce, normalize, recheck, resolve


class QuotesService:
    def add_quote(self, n, p, src_dir):
        src = next(
            (
                s
                for s in (self.root / src_dir).glob(f"{p.stem}.*")
                if s.suffix.lower() in self.cfg["quote_upload_ext"]
            ),
            None,
        )
        data = json.loads(p.read_text(encoding="utf-8"))
        qid = f"{n}:{p.stem}"
        self.state["quotes"][qid] = {
            "set": n,
            "name": re.sub(r"^[0-9a-f]{32}_", "", p.stem),
            "file": str(p),
            "source": str(src) if src else None,
            "state": {
                "result": normalize(unwrap(data) or data),
                "questions": [],
                "resolution_log": [],
                "acknowledged": [],
                "hold": False,
                "source": str(src) if src else None,
            },
        }
        self.check(qid)
        return qid

    def load_quotes(self):
        for n, (res_dir, src_dir, _label) in enumerate(self.cfg["quote_sets"]):
            for p in sorted((self.root / res_dir).glob("*.json")):
                if f"{n}:{p.stem}" not in self.state["quotes"]:
                    self.add_quote(n, p, src_dir)

    def text_of(self, q):
        if q["source"] not in self.state["texts"]:
            self.state["texts"][q["source"]] = source_text(q["source"])
        return self.state["texts"][q["source"]]

    def check(self, qid):
        q = self.state["quotes"][qid]
        recheck(q["state"], self.text_of(q), self.state["case"])

    def groups(self):
        """지금 견적 목록을 같은 물품끼리 묶는다 → {id: {"group": 번호, "label": 이름}}."""
        if not self.state["quotes"]:
            return {}
        of, names = group_quotes(
            {qid: q["state"]["result"] for qid, q in self.state["quotes"].items()}
        )
        return {qid: {"group": n, "label": names[n]} for qid, n in of.items()}

    def summary(self, qid):
        q = self.state["quotes"][qid]
        F, I, r = fields(), fields()["item"], q["state"]["result"]
        return {
            "id": qid,
            "set": q["set"],
            "name": q["name"],
            "has_source": bool(q["source"]),
            "vendor": r.get(F["vendor_name"]),
            "total": r.get(F["total"]),
            "supply": r.get(F["supply"]),
            "vat": r.get(F["vat"]),
            "quote_date": r.get(F["quote_date"]),
            "items": [
                {
                    "category": it.get(I["category"]),
                    "description": it.get(I["description"]),
                    "amount": it.get(I["amount"]),
                    "basis": it.get(I["basis"]),
                }
                for it in r.get(F["items"]) or []
            ],
            "questions": q["state"]["questions"],
            "hold": q["state"]["hold"],
            "answered": q["state"]["resolution_log"],
            "model_flags": r.get("model_flags", []),
            "studio": r.get("studio"),
            "fill": q.get("fill"),
        }

    def answer(self, qid, key, value, who):
        st = self.state["quotes"][qid]["state"]
        self.state["quotes"][qid]["fill"] = None
        qs = st["questions"]
        idx = next(i for i, x in enumerate(qs) if x["key"] == key)
        st["questions"] = (
            [qs[idx]] + qs[:idx] + qs[idx + 1 :]
        )  # resolve()는 맨 앞 질문에 답한다
        resolve(st, coerce(qs[idx]["field"], value), who)
        self.check(qid)

    def quote_for_select(self, qid):
        s = self.summary(qid)
        return {
            "file": qid,
            "vendor": s["vendor"],
            "total": s["total"] or 0,
            "quote_date": s["quote_date"] or "",
            "issues": [x["kind"] for x in s["questions"]],
            "issue_messages": [x["message"] for x in s["questions"]],
        }

    def studio_quote(self, name, data_b64, token=None):
        if self.studio_missing():
            raise ValueError("Studio API 설정을 확인하세요.")
        name = safe_name(name)
        if Path(name).suffix.lower() not in self.cfg["quote_upload_ext"]:
            raise ValueError(
                config("ui")["quotes"]["upload_bad_ext"].format(
                    ext=", ".join(self.cfg["quote_upload_ext"])
                )
            )
        data = decode_data(data_b64)
        res_dir, src_dir, _ = self.cfg["quote_sets"][0]
        src = self.root / src_dir / unique_name(name)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(data)

        def progress(done, found, finished):
            if token:
                with self.lock:
                    self.progress[token] = dict(
                        done=done, found=found, finished=finished
                    )

        try:
            _, job_id = self.studio.run_file(src, self.root / res_dir, progress)
            qid = self.execute(
                self.add_quote, 0, self.root / res_dir / f"{src.stem}.json", src_dir
            )
            return qid, job_id
        except NotQuote as e:
            src.unlink(missing_ok=True)
            raise ValueError(
                config("ui")["quotes"]["not_quote"].format(category=e.category)
            ) from None
        except StudioError as e:
            src.unlink(missing_ok=True)
            raise ValueError(
                config("ui")["quotes"]["studio_errors"][e.kind].format(code=e.code)
            ) from None
        except Exception:
            # A successfully written result may be imported at the next startup.
            if not (self.root / res_dir / f"{src.stem}.json").exists():
                src.unlink(missing_ok=True)
            raise
        finally:
            with self.lock:
                self.progress.pop(token, None)
