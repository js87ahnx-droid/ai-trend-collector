"""Hacker News 공식 Firebase API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .. import http
from .base import MAX_PER_SOURCE, Context, raw

API = "https://hacker-news.firebaseio.com/v0"


def to_raw(it: dict) -> dict | None:
    if not it or it.get("type") != "story" or it.get("dead") or it.get("deleted"):
        return None
    discuss = f"https://news.ycombinator.com/item?id={it['id']}"
    url = it.get("url") or discuss
    if not url.startswith("https://"):
        url = discuss
    return raw(
        id=f"hn-{it['id']}",
        title=it.get("title", ""),
        url=url,
        source="hn",
        score=it.get("score"),
        comments=it.get("descendants"),
        excerpt=it.get("text", ""),
        published_at=datetime.fromtimestamp(it.get("time", 0), timezone.utc).isoformat(),
    )


def fetch(ctx: Context) -> list[dict]:
    ids = http.get_json(f"{API}/topstories.json")[:MAX_PER_SOURCE]
    ids = [i for i in ids if f"hn-{i}" not in ctx.known_ids]
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(lambda i: http.get_json(f"{API}/item/{i}.json"), ids))
    return [r for r in map(to_raw, items) if r]
