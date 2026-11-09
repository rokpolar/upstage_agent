import json

from app.config import fields


def unwrap(d):
    """Studio/API 응답이 한 겹 감싸져 있으면 품의서 키가 있는 dict를 꺼낸다."""
    if isinstance(d, dict) and fields()["items"] in d:
        return d
    if isinstance(d, dict):
        for v in d.values():
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    continue
            found = unwrap(v)
            if found is not None:
                return found
    if isinstance(d, list):
        for v in d:
            found = unwrap(v)
            if found is not None:
                return found
    return None
