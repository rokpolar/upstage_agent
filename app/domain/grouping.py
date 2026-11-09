"""같은 물품의 견적서끼리 묶는다(Solar의 품목 분류 + 품목 이름 글자 유사도).

    python tools/grouping.py results_team          # 묶음 결과 보기
    python tools/grouping.py results_team --check  # 파일 이름 앞 번호(01_, 02_…)와 맞는지 채점

방법: 견적서마다 품목 이름들을 글자 두 개씩(bigram)으로 쪼개 세고, 두 견적서의 코사인 유사도가
config/rules.json grouping.threshold 이상이면 같은 묶음으로 본다(평균 연결 군집).
"랜케이블"과 "LAN 케이블", "도시락(한식 5찬)"과 "정식 도시락 5찬"처럼 띄어쓰기·표기가 달라도 묶이도록
숫자·기호·규격 단위는 빼고 글자만 본다.
Studio 견적 검토가 쓴 '품목 분류'(studio.kind, 예: 사무용품, 사무용 의자)가 같으면 유사도에 kind_bonus를 더한다.
그래서 분류가 같으면 묶이기 쉽고, 분류가 달라도(표기만 다른 경우) 품목 글자가 아주 비슷하면 묶인다.
분류가 같아도 품목이 전혀 다르면(식탁과 의자를 둘 다 '가구'라고 쓴 경우) 따로 둔다.
묶음 이름은 품목 분류가 있으면 그것, 없으면 묶음 안에서 가장 많이 겹치는 첫 품목 이름이다.
"""

import math
import re
from collections import Counter

from app.config import fields, rules


def cfg():
    return rules()["grouping"]


def words(text):
    """품목 이름에서 숫자·기호·단위를 빼고 글자 덩어리만 남긴다."""
    text = re.sub(cfg()["strip_pattern"], " ", str(text or "").lower())
    stop = set(cfg()["stopwords"])
    return [w for w in text.split() if len(w) >= cfg()["min_len"] and w not in stop]


def bigrams(result):
    I = fields()["item"]
    c = Counter()
    for it in result.get(fields()["items"]) or []:
        for w in words(it.get(I["description"])):
            w = f"^{w}$"  # 단어 경계도 글자로 넣어 짧은 단어가 묻히지 않게 한다
            c.update(w[i : i + 2] for i in range(len(w) - 1))
    return c


def cosine(a, b):
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (
        math.sqrt(sum(v * v for v in a.values()))
        * math.sqrt(sum(v * v for v in b.values()))
    )


def kind(result):
    return re.sub(r"\s+", "", ((result.get("studio") or {}).get("kind") or ""))


def label_of(results):
    """묶음 이름: 묶음 안 견적서들의 첫 품목 이름 중 다른 견적서와 가장 많이 겹치는 것."""
    I = fields()["item"]
    firsts = []
    for r in results:
        items = r.get(fields()["items"]) or []
        if items:
            firsts.append(
                " ".join(words(items[0].get(I["description"])))
                or str(items[0].get(I["description"]) or "")
            )
    kinds = Counter((r.get("studio") or {}).get("kind") for r in results if kind(r))
    if kinds:
        return kinds.most_common(1)[0][0]
    if not firsts:
        return ""
    best = max(
        firsts,
        key=lambda f: (sum(cosine(Counter(f), Counter(g)) for g in firsts), -len(f)),
    )
    n = len((results[0].get(fields()["items"]) or []))
    return (
        cfg()["label"].format(first=best.strip(), more=n - 1) if n > 1 else best.strip()
    )


def group(results):
    """results: {id: 추출 JSON} → {id: 묶음 번호}, {묶음 번호: 이름}. 평균 연결 군집."""
    ids = list(results)
    vec = {i: bigrams(results[i]) for i in ids}
    clusters = [[i] for i in ids]
    th = cfg()["threshold"]

    bonus = cfg()["kind_bonus"]

    def sim(x, y):
        same = kind(results[x]) and kind(results[x]) == kind(results[y])
        return cosine(vec[x], vec[y]) + (bonus if same else 0)

    def link(a, b):
        return sum(sim(x, y) for x in a for y in b) / (len(a) * len(b))

    while len(clusters) > 1:
        best, pair = th, None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                s = link(clusters[x], clusters[y])
                if s >= best:
                    best, pair = s, (x, y)
        if not pair:
            break
        x, y = pair
        clusters[x] += clusters.pop(y)
    # 큰 묶음이 앞에 오게, 같으면 처음 올라온 순서대로
    clusters.sort(key=lambda c: (-len(c), ids.index(c[0])))
    of, names = {}, {}
    for n, c in enumerate(clusters):
        names[n] = label_of([results[i] for i in c])
        for i in c:
            of[i] = n
    return of, names
