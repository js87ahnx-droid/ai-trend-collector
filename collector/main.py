"""매시간 진입점: 수집 → AI 필터 → 중복 제거 → 분류·요약 → 저장 → 테마 요약 → 7일 정리."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import classify, store
from .sources import gn, gpters, hn, websearch
from .sources.base import Context

log = logging.getLogger("collector")

SOURCES = [("gn", gn), ("hn", hn), ("gpters", gpters), ("websearch", websearch)]
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))


def run(now: datetime | None = None, data_dir: Path = DATA, client=None, sources=SOURCES) -> dict:
    now = now or datetime.now(timezone.utc)
    items_path, status_path = data_dir / "items.json", data_dir / "status.json"
    items = store.load_items(items_path)
    status = store.load_status(status_path)
    ctx = Context(
        now=now,
        known_ids={it["id"] for it in items},
        run_index=int(now.timestamp() // 3600),
        search_api_key=os.environ.get("SEARCH_API_KEY") or None,
    )

    # 1. 수집 — 소스별로 실패를 가둔다
    report: dict[str, dict] = {}
    cands: list[dict] = []
    for name, mod in sources:
        try:
            got = mod.fetch(ctx)
            cands += got
            report[name] = {"fetched": len(got)}
            log.info("수집 %s: %d건", name, len(got))
        except Exception as e:
            report[name] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:300]}
            log.warning("수집 실패 %s: %s", name, e)
    if not ctx.search_api_key:
        report.setdefault("websearch", {})["note"] = "SEARCH_API_KEY 없음 — 건너뜀"

    # 2~3. AI 필터, 중복 제거, 우선순위
    filtered = classify.prefilter(cands)
    fresh = store.rank_for_run(store.dedupe_candidates(filtered, items))[: classify.MAX_TO_CLASSIFY]
    log.info("후보 %d → AI 필터 %d → 신규 %d", len(cands), len(filtered), len(fresh))

    # 4~6. 분류·저장·테마 요약
    added: list[dict] = []
    llm_note = None
    if client is None and os.environ.get("ANTHROPIC_API_KEY"):
        client = classify.make_client()
    if client is None:
        llm_note = "ANTHROPIC_API_KEY 없음 — 분류·저장 건너뜀"
        log.warning(llm_note)
    elif fresh:
        try:
            stored = [it["title"] for it in store.recent(items, now, 24)]
            labeled = classify.classify(fresh, client, stored)
            items, added = store.add_items(items, labeled, now)
            log.info("분류 %d건 중 저장 %d건", len(labeled), len(added))
        except Exception as e:
            llm_note = f"분류 실패: {type(e).__name__}: {e}"[:300]
            log.warning(llm_note)

    items = store.prune(items, now)
    recent = store.recent(items, now, 24)
    themes = status.get("themes", [])
    if client is not None and added:
        try:
            themes = classify.summarize_themes(recent, client)
        except Exception as e:
            log.warning("테마 요약 실패: %s", e)
    present = {it["theme"] for it in recent}
    themes = [t for t in themes if t["key"] in present]

    for name in report:
        report[name]["kept"] = sum(1 for a in added if _source_group(a["source"]) == name)
    new_status = {
        "last_run": store.iso(now),
        "themes": themes,
        "sources": report,
        "run": {"candidates": len(cands), "ai_filtered": len(filtered), "new": len(fresh),
                "added": len(added), "total": len(items), **({"note": llm_note} if llm_note else {})},
    }
    # 이번 회차에 추가된 글이 없으면 '새로 들어옴' 표시가 사라지도록 last_run만 앞으로 간다.
    store.save_json(items_path, {"items": items})
    store.save_json(status_path, new_status)
    log.info("완료: 추가 %d, 전체 %d", len(added), len(items))
    return new_status


def _source_group(source: str) -> str:
    return {"gnp": "gn", "reddit": "websearch", "threads": "websearch", "web": "websearch"}.get(source, source)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
