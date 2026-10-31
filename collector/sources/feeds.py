"""공개 RSS/Atom 피드 — 팟캐스트 인터뷰(pod), Product Hunt(ph), 뉴스레터(nl), Show HN(hn)."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .. import http
from .base import MAX_PER_SOURCE, Context, raw

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    source: str
    max_items: int
    max_age: timedelta


FEEDS = (
    Feed("lenny", "https://www.lennysnewsletter.com/feed", "pod", 3, timedelta(days=7)),
    Feed("latent", "https://www.latent.space/feed", "pod", 3, timedelta(days=7)),
    Feed("dwarkesh", "https://www.dwarkesh.com/feed", "pod", 3, timedelta(days=7)),
    Feed("lex", "https://lexfridman.com/feed/podcast/", "pod", 3, timedelta(days=7)),
    Feed("producthunt", "https://www.producthunt.com/feed?category=artificial-intelligence", "ph", MAX_PER_SOURCE, timedelta(hours=48)),
    Feed("bensbites", "https://www.bensbites.com/feed", "nl", 5, timedelta(days=7)),
    Feed("ycblog", "https://www.ycombinator.com/blog/rss", "nl", 5, timedelta(days=7)),
    Feed("showhn", "https://news.ycombinator.com/showrss", "hn", MAX_PER_SOURCE, timedelta(hours=48)),
)

HN_ITEM = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)")
PH_POST = re.compile(r"Post/(\d+)")


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse(xml_text: str) -> list[dict]:
    """RSS 2.0·Atom 공통: [{title, link, guid, published, summary, comments}]"""
    root = ET.fromstring(xml_text)
    out = []
    for e in root.iter("item"):
        out.append({
            "title": e.findtext("title", ""),
            "link": (e.findtext("link") or "").strip(),
            "guid": (e.findtext("guid") or "").strip(),
            "published": _date(e.findtext("pubDate")),
            "summary": e.findtext("description") or e.findtext(CONTENT) or "",
            "comments": (e.findtext("comments") or "").strip(),
        })
    for e in root.iter(f"{ATOM}entry"):
        link = next((l.get("href") for l in e.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"), "")
        out.append({
            "title": e.findtext(f"{ATOM}title", ""),
            "link": (link or "").strip(),
            "guid": (e.findtext(f"{ATOM}id") or "").strip(),
            "published": _date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")),
            "summary": e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary") or "",
            "comments": "",
        })
    return out


def to_raw(entry: dict, feed: Feed) -> dict | None:
    url = entry["link"]
    if not url.startswith("https://") or not entry["title"].strip():
        return None
    if feed.source == "hn":
        m = HN_ITEM.search(entry["comments"]) or HN_ITEM.search(url)
        if not m:
            return None
        id_ = f"hn-{m.group(1)}"
    elif feed.source == "ph":
        m = PH_POST.search(entry["guid"])
        id_ = f"ph-{m.group(1)}" if m else "ph-" + hashlib.sha1(url.encode()).hexdigest()[:10]
    else:
        key = entry["guid"] or url
        id_ = f"{feed.source}-" + hashlib.sha1(key.encode()).hexdigest()[:10]
    pub = entry["published"]
    return raw(id=id_, title=entry["title"], url=url, source=feed.source, excerpt=entry["summary"],
               published_at=pub.isoformat() if pub else None)


def fetch_feed(feed: Feed, ctx: Context) -> list[dict]:
    out = []
    for e in parse(http.get_text(feed.url, timeout=25)):
        if not e["published"] or ctx.now - e["published"] > feed.max_age:
            continue
        r = to_raw(e, feed)
        if r and r["id"] not in ctx.known_ids:
            out.append(r)
        if len(out) >= feed.max_items:
            break
    return out


def fetch(ctx: Context) -> list[dict]:
    """피드 하나가 실패해도 나머지는 계속. 모두 실패하면 예외."""
    out, errors = [], []
    for feed in FEEDS:
        try:
            out += fetch_feed(feed, ctx)
        except Exception as e:
            errors.append(f"{feed.name}: {e}")
    if errors and not out and len(errors) == len(FEEDS):
        raise http.FetchError("; ".join(errors))
    ctx.notes["feeds"] = errors
    return out
