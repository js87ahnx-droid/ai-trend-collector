"""저장된 글을 쉬운 말로 다시 쓴다 — 원문은 다시 읽지 않고 저장된 정리만 바탕으로 (문체 변경 때 한 번 쓰는 작업).
`python -m collector.rewrite` · 실패한 글은 그대로 둔다."""
from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import classify, detail, store
from .main import DATA

log = logging.getLogger("collector")

PROMPT = """너는 AI에 관심 있는 일반 직장인을 위한 큐레이터다. 아래는 이미 원문을 읽고 정리해 둔 글이다.
사실·숫자·제품명·단계 순서는 그대로 두고 문체만 바꿔 다시 써라. 새 사실을 더하지 않는다.

{style}
필드:
- headline: 한국어 45자 이내, 독자가 얻을 것을 한 줄로, 비전문가도 바로 이해할 말로.
- why: 60자 이내, 왜 지금 볼 만한지.
- points 3개(각 80자 이내), steps(원래 개수 그대로, 각 120자 이내, `백틱` 속 명령어는 그대로), extra(300자 이내, 없으면 "").
- figure: 원래 그래픽의 type·수치는 그대로, 글자만 쉬운 말로 (원래 없으면 type="none").
- terms: 다시 쓴 본문에 남은 전문용어 중 일반인이 모를 만한 것 0~3개, meaning은 일상 비유를 곁들인 한 문장(60자 이내).
- buzz: 세계적으로 크게 화제인 소식(주요 AI 회사의 큰 발표·사고, 여러 매체가 다룬 이야기)이면 true.
- pick: 0~10, 개인이 오늘 바로 써먹을 가치가 높을수록·세계적 화제일수록 높게. 개발자만 쓸 수 있는 내용은 조금 낮춘다.

글(JSON):
{item}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "why": {"type": "string"},
        "buzz": {"type": "boolean"},
        "pick": {"type": "integer"},
    } | {k: detail.SCHEMA["properties"][k] for k in ("points", "steps", "extra", "figure", "terms")},
    "required": ["headline", "why", "buzz", "pick", "points", "steps", "extra", "figure", "terms"],
    "additionalProperties": False,
}


def rewrite_one(item: dict, client) -> dict:
    src = {k: item.get(k) for k in ("title", "cat", "headline", "tag", "why", "score", "comments")} | {"detail": item["detail"]}
    data = classify.ask(client, PROMPT.format(style=detail.STYLE, item=json.dumps(src, ensure_ascii=False)), SCHEMA, 6000)
    new_detail = store.clean_detail(data)
    headline = (data.get("headline") or "").strip()[:60]
    if not new_detail or not headline:
        raise classify.LLMError("다시 쓴 결과가 비어 있음")
    out = item | {"headline": headline, "detail": new_detail,
                  "why": (data.get("why") or "").strip()[:90] or item.get("why"),
                  "pick": max(0, min(10, int(data.get("pick") or 0)))}
    out.pop("buzz", None)
    return out | ({"buzz": True} if data.get("buzz") is True else {})


def rewrite_all(items: list[dict], client) -> tuple[list[dict], list[str]]:
    """(새 목록, 실패 사유). 순서·id·url·cat·수집 시각·대체 관계는 바꾸지 않는다."""
    def one(it):
        try:
            return rewrite_one(it, client), None
        except Exception as e:
            log.warning("다시 쓰기 실패 %s: %s", it["id"], e)
            return it, f"{it['id']}: {type(e).__name__}: {e}"[:200]

    with ThreadPoolExecutor(max_workers=detail.WORKERS) as pool:
        results = list(pool.map(one, items))
    return [r for r, _ in results], [e for _, e in results if e]


def main(data_dir: Path = DATA, client=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = data_dir / "items.json"
    items = store.load_items(path)
    out, errors = rewrite_all(items, client or classify.make_client())
    store.save_json(path, store.items_doc(out))
    print(f"다시 쓰기 {len(items) - len(errors)}/{len(items)}건" + (f", 실패 {len(errors)}건" if errors else ""))
    return 1 if items and len(errors) == len(items) else 0


if __name__ == "__main__":
    sys.exit(main())
