"""items.json / status.json 읽기·쓰기, 중복 제거, 회차 상한, 대체(취소선) 처리, 보관 기간 정리."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 2
SOURCES = ("gn", "gnp", "hn", "reddit", "threads", "gpters", "web", "pod", "ph", "nl")
CATS = ("tip", "tool", "biz", "voice", "shift")
MAX_PER_RUN = 10
RETENTION = timedelta(days=7)
SUPERSEDED_RETENTION = timedelta(days=3)
REQUIRED = ("id", "title", "url", "source", "cat", "headline", "tag", "detail", "collected_at")
OPTIONAL = ("score", "comments", "origin", "why", "pick", "replaces")
FIGURE_TYPES = ("compare", "flow", "bars", "quote")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_items(path: Path) -> list[dict]:
    """v2 형식 글만 읽는다. 이전 형식(schema 없음)은 버리고 새로 수집."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA_VERSION:
        return []
    return data.get("items", [])


def items_doc(items: list[dict]) -> dict:
    return {"schema": SCHEMA_VERSION, "items": items}


def load_status(path: Path) -> dict:
    if not path.exists():
        return {"last_run": None, "themes": [], "sources": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def normalize_url(url: str) -> str:
    u = url.strip().split("#")[0].rstrip("/")
    return u.replace("://www.", "://").lower()


def _short(s, n: int) -> str | None:
    return s.strip()[:n] if isinstance(s, str) and s.strip() else None


def clean_figure(fig) -> dict | None:
    """LLM이 준 그래픽 명세를 허용 형태로만 통과시킨다. 형태가 어긋나면 None."""
    if not isinstance(fig, dict) or fig.get("type") not in FIGURE_TYPES:
        return None
    t = fig["type"]
    title = _short(fig.get("title"), 60)
    if t == "compare":
        before, after = _short(fig.get("before"), 140), _short(fig.get("after"), 140)
        if not (before and after):
            return None
        return {"type": t, "title": title, "before": before, "after": after,
                "before_label": _short(fig.get("before_label"), 16) or "전",
                "after_label": _short(fig.get("after_label"), 16) or "후"}
    if t == "flow":
        steps = [s for s in (_short(x, 24) for x in fig.get("steps") or []) if s][:5]
        return {"type": t, "title": title, "steps": steps} if len(steps) >= 2 else None
    if t == "bars":
        bars = []
        for b in (fig.get("bars") or [])[:6]:
            label, value = _short((b or {}).get("label"), 24), (b or {}).get("value")
            if label and isinstance(value, (int, float)) and value >= 0:
                bars.append({"label": label, "value": value})
        if len(bars) < 2:
            return None
        return {"type": t, "title": title, "unit": _short(fig.get("unit"), 12) or "", "bars": bars}
    text = _short(fig.get("text"), 160)
    if not text:
        return None
    return {"type": t, "title": title, "text": text, "speaker": _short(fig.get("speaker"), 40) or ""}


def clean_detail(d) -> dict | None:
    if not isinstance(d, dict):
        return None
    points = [p for p in (_short(x, 160) for x in d.get("points") or []) if p][:3]
    steps = [s for s in (_short(x, 200) for x in d.get("steps") or []) if s][:6]
    if len(points) < 2:
        return None
    return {"points": points, "steps": steps, "extra": _short(d.get("extra"), 600) or "",
            "figure": clean_figure(d.get("figure"))}


def valid_item(item: dict) -> bool:
    if any(not item.get(k) for k in REQUIRED):
        return False
    return item["url"].startswith("https://") and item["source"] in SOURCES and item["cat"] in CATS


def dedupe_candidates(candidates: list[dict], existing: list[dict]) -> list[dict]:
    """기존 id는 건너뛰고, 같은 URL은 점수 높은 쪽 하나만 남긴다 (기존 글에 같은 URL이 있으면 버림)."""
    seen_ids = {it["id"] for it in existing}
    seen_urls = {normalize_url(it["url"]) for it in existing}
    best: dict[str, dict] = {}
    for c in candidates:
        if c["id"] in seen_ids:
            continue
        key = normalize_url(c["url"])
        if key in seen_urls:
            continue
        cur = best.get(key)
        if cur is None or (c.get("score") or 0) > (cur.get("score") or 0):
            best[key] = c
    ids = set()
    out = []
    for c in best.values():
        if c["id"] not in ids:
            ids.add(c["id"])
            out.append(c)
    return out


def rank_for_run(candidates: list[dict]) -> list[dict]:
    """회차 상한을 넘을 때 남길 순서. 점수가 없는 소스가 밀리지 않도록
    소스별로 (점수+댓글, 게시 최신) 순으로 줄 세운 뒤 한 건씩 번갈아 뽑는다."""
    by_source: dict[str, list[dict]] = {}
    for c in candidates:
        by_source.setdefault(c["source"], []).append(c)
    queues = [
        sorted(q, key=lambda c: ((c.get("score") or 0) + (c.get("comments") or 0), c.get("published_at") or ""),
               reverse=True)
        for q in by_source.values()
    ]
    out = []
    for i in range(max((len(q) for q in queues), default=0)):
        out += [q[i] for q in queues if i < len(q)]
    return out


def add_items(existing: list[dict], new: list[dict], now: datetime) -> tuple[list[dict], list[dict]]:
    """검증된 새 글을 최대 MAX_PER_RUN 건 추가하고, replaces가 가리키는 이전 글에 대체 표시를 남긴다."""
    stamp = iso(now)
    by_id = {it["id"]: it for it in existing}
    added = []
    for raw in new:
        item = {k: raw.get(k) for k in REQUIRED + OPTIONAL}
        item["collected_at"] = stamp
        item["detail"] = clean_detail(item.get("detail"))
        old = by_id.get(item.get("replaces") or "")
        if not old or old.get("superseded_by"):
            item["replaces"] = None
        item = {k: v for k, v in item.items() if v is not None}
        if not valid_item(item) or item["id"] in by_id:
            continue
        if item.get("replaces"):
            old = by_id[item["replaces"]]
            old["superseded_by"], old["superseded_at"] = item["id"], stamp
        by_id[item["id"]] = item
        added.append(item)
        if len(added) >= MAX_PER_RUN:
            break
    return existing + added, added


def prune(items: list[dict], now: datetime) -> list[dict]:
    """대체된 글은 대체 3일 뒤, 나머지는 수집 7일 뒤 삭제. 지워진 글을 가리키는 replaces는 정리."""
    keep = []
    for it in items:
        if it.get("superseded_at"):
            if now - parse_iso(it["superseded_at"]) > SUPERSEDED_RETENTION:
                continue
        elif now - parse_iso(it["collected_at"]) > RETENTION:
            continue
        keep.append(it)
    ids = {it["id"] for it in keep}
    for it in keep:
        if it.get("replaces") and it["replaces"] not in ids:
            del it["replaces"]
        if it.get("superseded_by") and it["superseded_by"] not in ids:
            it.pop("superseded_by"), it.pop("superseded_at", None)
    return keep


def recent(items: list[dict], now: datetime, hours: int = 24) -> list[dict]:
    cutoff = now - timedelta(hours=hours)
    return [it for it in items if parse_iso(it["collected_at"]) > cutoff]
