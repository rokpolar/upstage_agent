"""경로 경계, 충돌 없는 이름, 원자적 결과 저장."""

import base64
import binascii
import json
import os
import re
import tempfile
import uuid
from pathlib import Path


def inside(path, base):
    return Path(path).resolve().is_relative_to(Path(base).resolve())


def confined(base, relative):
    path = (Path(base) / relative).resolve()
    if not inside(path, base):
        raise FileNotFoundError("not found")
    return path


def decode_data(value):
    if not isinstance(value, str):
        raise ValueError("파일 데이터는 Base64 문자열이어야 합니다.")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("파일 데이터의 Base64 형식이 올바르지 않습니다.") from None


def safe_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("파일 이름이 필요합니다.")
    return (
        re.sub(r"[^\w가-힣.() -]", "_", value.replace("\\", "/").rsplit("/", 1)[-1])
        or "document"
    )


def unique_name(value):
    return f"{uuid.uuid4().hex}_{safe_name(value)}"


def write_json(path, value):
    """같은 파일시스템에서 교체하여 독자가 부분 JSON을 읽지 않게 한다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".json-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
