"""저장된 결과로 업무 규칙의 기존 동작을 고정한다. 외부 API는 호출하지 않는다."""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from common import fields
from grouping import group
from gyeolui import blocked, new_state
from harness import HOLD, normalize, recheck, resolve, review
from score import compare, unwrap
from select_vendor import finalize, recommend


@pytest.mark.parametrize(
    "results,answers,expected",
    [
        ("results", "answers", (5, 95, 95)),
        ("results_team", "answers_team", (20, 512, 512)),
    ],
)
def test_scoring_baseline(results, answers, expected):
    docs = correct = total = 0
    for p in sorted((ROOT / answers).glob("*.json")):
        rp = ROOT / results / p.name
        if not rp.exists():
            continue
        r = normalize(unwrap(json.loads(rp.read_text())))
        r[fields()["flags"]] = [q["message"] for q in review(r)]
        rows = [
            x
            for x in compare(r, unwrap(json.loads(p.read_text())))
            if x[3] in ("O", "X")
        ]
        docs += 1
        correct += sum(x[3] == "O" for x in rows)
        total += len(rows)
    assert (docs, correct, total) == expected


def test_grouping_baseline():
    results = {
        p.stem: unwrap(json.loads(p.read_text()))
        for p in (ROOT / "results_team").glob("*.json")
    }
    groups, _ = group(results)
    assert len(results) == 51
    assert len(set(groups.values())) == 20
    for a in results:
        for b in results:
            assert (groups[a] == groups[b]) == (a.split("_")[0] == b.split("_")[0])


def test_answers_recheck_and_hold():
    r = normalize(unwrap(json.loads((ROOT / "results/q04.json").read_text())))
    state = dict(result=r, acknowledged=[], resolution_log=[], hold=False)
    assert recheck(state, "", {})
    resolve(state, HOLD, "담당자")
    recheck(state, "", {})
    assert state["hold"]
    assert state["resolution_log"][0]["by"] == "담당자"


def test_vendor_selection_reason():
    quotes = [
        dict(
            file=str(i),
            vendor=str(i),
            total=100 + i,
            quote_date="2026-11-01",
            issues=[],
        )
        for i in range(2)
    ]
    rec, reason, _, _ = recommend(quotes)
    assert rec is quotes[0]
    assert finalize(quotes, rec, reason, rec, "", True)["reason"] == reason
    with pytest.raises(ValueError):
        finalize(quotes, rec, reason, quotes[1], "", True)


def test_gyeolui_existing_fixture():
    raw = json.loads(next((ROOT / "examples/gyeolui").glob("*.json")).read_text())
    state = new_state(copy.deepcopy(raw))
    assert state["result"]["receipts"]
    assert isinstance(blocked(state), list)
