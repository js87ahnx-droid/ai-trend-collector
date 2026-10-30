"""긱뉴스 RSS(Atom). 작성자 neo(GN⁺ 봇)의 글은 gnp, 나머지는 gn.

/new, /plus HTML은 CloudFront가 403으로 막으므로 RSS 한 곳만 쓴다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import http
from .base import MAX_PER_SOURCE, Context, raw

FEED = "https://news.hada.io/rss/news"
NS = {"a": "http://www.w3.org/2005/Atom"}
GNP_AUTHORS = {"neo"}
TOPIC_ID = re.compile(r"topic\?id=(\d+)")


def parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        link = e.find("a:link", NS)
        url = link.get("href") if link is not None else ""
        m = TOPIC_ID.search(url)
        if not m:
            continue
        author = (e.findtext("a:author/a:name", default="", namespaces=NS) or "").strip()
        prefix = "gnp" if author in GNP_AUTHORS else "gn"
        out.append(raw(
            id=f"{prefix}-{m.group(1)}",
            title=e.findtext("a:title", default="", namespaces=NS),
            url=url.replace("http://", "https://"),
            source=prefix,
            excerpt=e.findtext("a:content", default="", namespaces=NS),
            published_at=e.findtext("a:published", default=None, namespaces=NS),
        ))
    return out


def fetch(ctx: Context) -> list[dict]:
    items = parse(http.get_text(FEED))
    gn = [i for i in items if i["source"] == "gn"][:MAX_PER_SOURCE]
    gnp = [i for i in items if i["source"] == "gnp"][:MAX_PER_SOURCE]
    return gn + gnp
