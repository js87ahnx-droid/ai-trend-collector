"""items.json / status.json 읽기·쓰기, 중복 제거, 회차 상한, 7일 정리."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCES = ("gn", "gnp", "hn", "reddit", "threads", "gpters", "web")
THEMES = ("tip", "agent", "code", "model", "research", "industry")
MAX_PER_RUN = 15
RETENTION = timedelta(days=7)
REQUIRED = ("id", "title", "url", "source", "theme", "gist", "collected_at")
OPTIONAL = ("score", "comments", "origin")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("items", [])


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


def valid_item(item: dict) -> bool:
    if any(not item.get(k) for k in REQUIRED):
        return False
    return (
        item["url"].startswith("https://")
        and item["source"] in SOURCES
        and item["theme"] in THEMES
    )


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
    """회차 상한을 넘을 때 남길 순서. 점수가 없는 국내 소스가 밀리지 않도록
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
    """검증된 새 글을 최대 MAX_PER_RUN 건 추가. (전체 목록, 실제 추가된 글) 반환."""
    stamp = iso(now)
    ids = {it["id"] for it in existing}
    added = []
    for raw in new:
        item = {k: raw.get(k) for k in REQUIRED + OPTIONAL}
        item["collected_at"] = stamp
        item = {k: v for k, v in item.items() if v is not None}
        if not valid_item(item) or item["id"] in ids:
            continue
        ids.add(item["id"])
        added.append(item)
        if len(added) >= MAX_PER_RUN:
            break
    return existing + added, added


def prune(items: list[dict], now: datetime) -> list[dict]:
    cutoff = now - RETENTION
    return [it for it in items if parse_iso(it["collected_at"]) > cutoff]


def recent(items: list[dict], now: datetime, hours: int = 24) -> list[dict]:
    cutoff = now - timedelta(hours=hours)
    return [it for it in items if parse_iso(it["collected_at"]) > cutoff]
