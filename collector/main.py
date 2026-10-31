"""매시간 진입점: 수집 → AI 필터 → 중복 제거 → 분류 → 상세 작성 → 저장(대체 반영) → 정리."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import classify, detail, store
from .sources import feeds, gn, gpters, hn, websearch
from .sources.base import Context

log = logging.getLogger("collector")

SOURCES = [("gn", gn), ("hn", hn), ("gpters", gpters), ("feeds", feeds), ("websearch", websearch)]
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
GROUP = {"gnp": "gn", "reddit": "websearch", "threads": "websearch", "web": "websearch",
         "pod": "feeds", "ph": "feeds", "nl": "feeds"}


def run(now: datetime | None = None, data_dir: Path = DATA, client=None, sources=SOURCES) -> dict:
    now = now or datetime.now(timezone.utc)
    items_path, status_path = data_dir / "items.json", data_dir / "status.json"
    items = store.load_items(items_path)
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
    if ctx.notes.get("feeds") and "feeds" in report:
        report["feeds"]["note"] = "; ".join(ctx.notes["feeds"])[:300]
    if ctx.notes.get("gpters") and "gpters" in report:
        report["gpters"]["note"] = ctx.notes["gpters"][:300]
    if not ctx.search_api_key and "websearch" in report:
        report["websearch"]["note"] = "SEARCH_API_KEY 없음 — 건너뜀"

    # 2~3. AI 필터, 중복 제거, 우선순위
    filtered = classify.prefilter(cands)
    fresh = store.rank_for_run(store.dedupe_candidates(filtered, items))[: classify.MAX_TO_CLASSIFY]
    log.info("후보 %d → AI 필터 %d → 신규 %d", len(cands), len(filtered), len(fresh))

    # 4~5. 분류 → 상세 → 저장
    added: list[dict] = []
    notes: list[str] = []
    labeled_n = 0
    if client is None and os.environ.get("ANTHROPIC_API_KEY"):
        client = classify.make_client()
    if client is None:
        notes.append("ANTHROPIC_API_KEY 없음 — 분류·저장 건너뜀")
        log.warning(notes[-1])
    elif fresh:
        try:
            labeled = classify.classify(fresh, client, store.recent(items, now, 24 * 7))
            labeled_n = len(labeled)
            written, errors = detail.write_all(labeled[: store.MAX_PER_RUN], client)
            if errors:
                notes.append(f"상세 실패 {len(errors)}건: " + "; ".join(errors)[:300])
            items, added = store.add_items(items, written, now)
            log.info("분류 통과 %d → 상세 %d → 저장 %d", labeled_n, len(written), len(added))
        except Exception as e:
            notes.append(f"분류 실패: {type(e).__name__}: {e}"[:300])
            log.warning(notes[-1])

    items = store.prune(items, now)
    for name in report:
        report[name]["kept"] = sum(1 for a in added if GROUP.get(a["source"], a["source"]) == name)
    new_status = {
        "last_run": store.iso(now),
        "sources": report,
        "run": {"candidates": len(cands), "ai_filtered": len(filtered), "new": len(fresh), "labeled": labeled_n,
                "added": len(added), "total": len(items), **({"note": " / ".join(notes)} if notes else {})},
    }
    store.save_json(items_path, store.items_doc(items))
    store.save_json(status_path, new_status)
    log.info("완료: 추가 %d, 전체 %d", len(added), len(items))
    return new_status


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
