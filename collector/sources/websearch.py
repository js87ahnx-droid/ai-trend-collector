"""Brave Search API로 Reddit·Threads·기타 웹의 최근 화제글을 보완 수집.

검색 결과의 제목·스니펫·URL·게시일만 쓰고 원문 페이지는 열지 않는다.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from .. import http
from .base import MAX_PER_SOURCE, Context, raw

API = "https://api.search.brave.com/res/v1/web/search"
SUBREDDITS = ("ClaudeAI", "ChatGPT", "LocalLLaMA", "singularity", "OpenAI", "ChatGPTPro")
REDDIT_KW = ("tips", "workflow", "prompt", "new model", "agent", "coding")
THREADS_KW = ("AI 팁", "클로드", "챗GPT", "프롬프트", "Claude", "GPT")
WEB_KW = ("AI 활용 팁", "Claude Code tips", "LLM prompt engineering tips", "AI 에이전트 활용", "ChatGPT 업무 활용")
PER_RUN = {"reddit": 3, "threads": 2, "web": 1}   # 회차당 6회 (지시서: 5~8회)
MAX_AGE = timedelta(hours=48)

REDDIT_ID = re.compile(r"reddit\.com/r/[^/]+/comments/([a-z0-9]+)", re.I)
THREADS_ID = re.compile(r"threads\.(?:com|net)/@[^/]+/post/([A-Za-z0-9_-]+)")
REL_AGE = re.compile(r"(\d+)\s+(minute|hour|day)s?\s+ago", re.I)
VOTES = re.compile(r"([\d,.]+k?)\s+(?:votes?|upvotes?|points?)", re.I)
COMMENTS = re.compile(r"([\d,.]+k?)\s+comments?", re.I)


def queries_for_run(run_index: int) -> list[tuple[str, str]]:
    """(source, query) 목록. 회차 번호로 서브레딧·키워드를 돌려 쓴다."""
    reddit_pool = [(s, k) for k in REDDIT_KW for s in SUBREDDITS]
    out = []
    for i in range(PER_RUN["reddit"]):
        sub, kw = reddit_pool[(run_index * PER_RUN["reddit"] + i) % len(reddit_pool)]
        out.append(("reddit", f"site:reddit.com/r/{sub} {kw}"))
    for i in range(PER_RUN["threads"]):
        kw = THREADS_KW[(run_index * PER_RUN["threads"] + i) % len(THREADS_KW)]
        out.append(("threads", f"site:threads.com {kw}"))
    for i in range(PER_RUN["web"]):
        out.append(("web", WEB_KW[(run_index + i) % len(WEB_KW)]))
    return out


def doc_id(url: str) -> tuple[str, str] | None:
    """(source, id). Reddit·Threads인데 게시글 ID가 없으면 게시글이 아니므로 None."""
    host = (urlparse(url).hostname or "").removeprefix("www.").removeprefix("old.")
    if host.endswith("reddit.com"):
        m = REDDIT_ID.search(url)
        return ("reddit", f"reddit-{m.group(1).lower()}") if m else None
    if host.startswith("threads."):
        m = THREADS_ID.search(url)
        return ("threads", f"threads-{m.group(1)}") if m else None
    return "web", "web-" + hashlib.sha1(url.encode()).hexdigest()[:10]


def published(result: dict, now: datetime) -> datetime | None:
    pa = result.get("page_age")
    if pa:
        try:
            dt = datetime.fromisoformat(pa.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    m = REL_AGE.search(result.get("age") or "")
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now - timedelta(**{f"{unit}s": n})
    return None


def _num(s: str) -> int:
    s = s.replace(",", "").lower()
    return int(float(s[:-1]) * 1000) if s.endswith("k") else int(float(s))


def snippet_counts(text: str) -> tuple[int | None, int | None]:
    v, c = VOTES.search(text), COMMENTS.search(text)
    return (_num(v.group(1)) if v else None, _num(c.group(1)) if c else None)


def to_raw(result: dict, now: datetime) -> dict | None:
    url = result.get("url", "")
    if not url.startswith("https://"):
        return None
    ident = doc_id(url)
    pub = published(result, now)
    if not ident or not pub or now - pub > MAX_AGE:
        return None
    source, id_ = ident
    snippet = " ".join([result.get("description", "")] + list(result.get("extra_snippets") or []))
    score, comments = snippet_counts(snippet)
    return raw(id=id_, title=result.get("title", ""), url=url, source=source, score=score,
               comments=comments, excerpt=snippet, published_at=pub.isoformat())


def search(query: str, key: str) -> list[dict]:
    qs = urlencode({"q": query, "freshness": "pd", "count": 20, "safesearch": "moderate"})
    data = http.get_json(f"{API}?{qs}", headers={"X-Subscription-Token": key, "Accept": "application/json"})
    return (data.get("web") or {}).get("results") or []


def fetch(ctx: Context, sleep=time.sleep) -> list[dict]:
    if not ctx.search_api_key:
        return []
    out, seen = [], set()
    for i, (expect, q) in enumerate(queries_for_run(ctx.run_index)):
        if i:
            sleep(1.1)   # Brave 무료 요금제 1 req/s
        for res in search(q, ctx.search_api_key):
            r = to_raw(res, ctx.now)
            if r and r["id"] not in seen and r["id"] not in ctx.known_ids:
                if expect != "web" and r["source"] != expect:
                    continue
                if sum(1 for o in out if o["source"] == r["source"]) >= MAX_PER_SOURCE:
                    continue
                seen.add(r["id"])
                out.append(r)
    return out
