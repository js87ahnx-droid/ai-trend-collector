"""GPTERS — 게시판 목록(/c/)은 robots.txt로 막혀 있어, 허용된 공간별 sitemap에서
최근 글 URL을 골라 게시글 페이지의 og 메타(제목·설명·게시일)만 읽는다."""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import timedelta

from .. import http
from ..store import parse_iso
from .base import Context, raw

BASE = "https://www.gpters.org"
SPACES = ("dev", "llm-service", "ai-writing", "nocode", "chatbot", "research", "news", "question", "data-science")
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
MAX_PAGES = 12           # 회차당 새 글 상한
MAX_SCAN = 24            # 회차당 게시글 페이지 요청 상한
MAX_AGE = timedelta(hours=48)
POST_ID = re.compile(r"/post/[^/?#]*?-([A-Za-z0-9]{10,})/?$")
META = re.compile(r'<meta[^>]+(?:property|name)="([a-z:_]+)"[^>]*?content="([^"]*)"', re.I)


def parse_sitemap(xml_text: str) -> list[tuple[str, str]]:
    root = ET.fromstring(xml_text)
    out = []
    for u in root.findall("s:url", NS):
        loc, mod = u.findtext("s:loc", "", NS), u.findtext("s:lastmod", "", NS)
        if "/post/" in loc and mod:
            out.append((loc, mod))
    return out


def parse_post(html_text: str) -> dict:
    return {k: v for k, v in META.findall(html_text)}


def interleave_by_space(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """공간별 최신 수정순 줄을 만들어 한 건씩 번갈아 뽑는다 — 한 공간의 옛 글 수정이 상위를 독차지하지 않게."""
    by_space: dict[str, list] = {}
    for u, m in sorted(entries, key=lambda x: x[1], reverse=True):
        by_space.setdefault(u.split("/post/")[0], []).append((u, m))
    queues = list(by_space.values())
    out = []
    for i in range(max((len(q) for q in queues), default=0)):
        out += [q[i] for q in queues if i < len(q)]
    return out


def post_id(url: str) -> str | None:
    m = POST_ID.search(url)
    return f"gpters-{m.group(1)}" if m else None


def fetch(ctx: Context, sleep=time.sleep) -> list[dict]:
    recent: list[tuple[str, str]] = []
    errors = []
    for space in SPACES:
        try:
            recent += parse_sitemap(http.get_text(f"{BASE}/{space}/sitemap.xml"))
        except Exception as e:  # 공간 하나 실패해도 계속
            errors.append(f"{space}: {e}")
    cutoff = ctx.now - MAX_AGE
    cand = [(u, m) for u, m in recent if parse_iso(m) > cutoff and post_id(u) and post_id(u) not in ctx.known_ids]
    cand = interleave_by_space(cand)
    out = []
    skipped = {"no_meta": 0, "old": 0, "error": 0}
    # lastmod는 옛 글 수정에도 바뀌므로, 공간별로 번갈아 보며 새 글 MAX_PAGES건을 찾을 때까지 최대 MAX_SCAN 페이지
    for url, _ in cand[:MAX_SCAN]:
        if len(out) >= MAX_PAGES:
            break
        try:
            meta = parse_post(http.get_text(url))
        except Exception:
            skipped["error"] += 1
            continue
        pub = meta.get("article:published_time")
        if not pub or not meta.get("og:title"):
            skipped["no_meta"] += 1
        elif parse_iso(pub) <= cutoff:
            skipped["old"] += 1
        else:
            out.append(raw(id=post_id(url), title=meta["og:title"], url=url, source="gpters",
                           excerpt=meta.get("og:description", ""), published_at=pub))
        sleep(0.5)
    if not out and errors and len(errors) == len(SPACES):
        raise http.FetchError("; ".join(errors))
    ctx.notes["gpters"] = (f"sitemap {len(recent)} · 최근 {len(cand)} · 페이지 {len(out) + sum(skipped.values())}"
                           f" · 메타 없음 {skipped['no_meta']} · 48시간 초과 {skipped['old']} · 오류 {skipped['error']}"
                           + (f" · 공간 실패 {'; '.join(errors)}" if errors else ""))
    return out
