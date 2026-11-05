"""매시간 진입점: 수집 → AI 필터 → 중복 제거 → 분류 → 상세 작성 → 저장(대체 반영) → 정리."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import classify, detail, store
from .sources import feeds, gn, gpters, hn, threadsearch, websearch
from .sources.base import Context

log = logging.getLogger("collector")

SOURCES = [("gn", gn), ("hn", hn), ("gpters", gpters), ("feeds", feeds), ("websearch", websearch), ("threads", threadsearch)]
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
SKIP_MEMORY = timedelta(days=8)   # 피드 최대 수집 창(7일)보다 길게
GROUP = {"gnp": "gn", "reddit": "websearch", "web": "websearch",
         "pod": "feeds", "ph": "feeds", "nl": "feeds", "news": "feeds"}


def run(now: datetime | None = None, data_dir: Path = DATA, client=None, sources=SOURCES) -> dict:
    now = now or datetime.now(timezone.utc)
    items_path, status_path, skipped_path = data_dir / "items.json", data_dir / "status.json", data_dir / "skipped.json"
    items = store.load_items(items_path)
    # 분류에서 빠졌거나 근거가 부족했던 글 — 다시 보내지 않는다 (화면이 읽지 않는 별도 파일)
    prev_skipped = json.loads(skipped_path.read_text(encoding="utf-8")) if skipped_path.exists() else {}
    skipped = {k: v for k, v in prev_skipped.items() if now - store.parse_iso(v) < SKIP_MEMORY}
    prev_status = store.load_status(status_path)
    if client is None and os.environ.get("ANTHROPIC_API_KEY"):
        client = classify.make_client()
    ctx = Context(
        now=now,
        known_ids={it["id"] for it in items} | set(skipped),
        run_index=int(now.timestamp() // 3600),
        search_api_key=os.environ.get("SEARCH_API_KEY") or None,
        client=client,
        threads_searched_at=prev_status.get("threads_searched_at"),
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
    if ctx.notes.get("threads") and "threads" in report:
        report["threads"]["note"] = ctx.notes["threads"][:300]
    if not ctx.search_api_key and "websearch" in report:
        report["websearch"]["note"] = "SEARCH_API_KEY 없음 — 건너뜀"

    # 2~3. AI 필터, 중복 제거, 우선순위
    filtered = classify.prefilter(cands)
    fresh = [c for c in store.dedupe_candidates(filtered, items) if c["id"] not in skipped]
    fresh = store.rank_for_run(fresh)[: classify.MAX_TO_CLASSIFY]
    log.info("후보 %d → AI 필터 %d → 신규 %d", len(cands), len(filtered), len(fresh))

    # 4~5. 분류 → 상세 → 저장
    added: list[dict] = []
    notes: list[str] = []
    labeled_n = 0
    if client is None:
        notes.append("ANTHROPIC_API_KEY 없음 — 분류·저장 건너뜀")
        log.warning(notes[-1])
    elif fresh:
        try:
            labeled, judged_out = classify.classify(fresh, client, store.recent(items, now, 24 * 7))
            labeled_n = len(labeled)
            written, errors, thin = detail.write_all(labeled[: store.MAX_PER_RUN], client)
            for sid in judged_out + thin:
                skipped[sid] = store.iso(now)
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
        "threads_searched_at": ctx.notes.get("threads_searched_at") or prev_status.get("threads_searched_at"),
        "run": {"candidates": len(cands), "ai_filtered": len(filtered), "new": len(fresh), "labeled": labeled_n,
                "added": len(added), "total": len(items), "skip_memory": len(skipped), **({"note": " / ".join(notes)} if notes else {})},
    }
    store.save_json(items_path, store.items_doc(items))
    store.save_json(status_path, new_status)
    store.save_json(skipped_path, skipped)
    log.info("완료: 추가 %d, 전체 %d", len(added), len(items))
    return new_status


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
