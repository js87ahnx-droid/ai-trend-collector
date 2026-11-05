"""Claude 웹검색으로 Threads 글을 찾아 보완 수집 (시험 운영, 하루 2회쯤).

검색 결과의 제목(글 본문 앞부분)·URL만 쓰고 글 페이지는 열지 않는다.
검색에 날짜 필터가 없어 옛글이 섞이므로, URL의 글 코드에 담긴 게시 시각으로 거른다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import http
from ..store import iso, parse_iso
from .base import Context, clean, raw

MODEL = "claude-haiku-5-5"
TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 2, "allowed_domains": ["threads.com"]}
KEYWORDS = ("AI 활용 꿀팁", "클로드 활용법", "챗GPT 꿀팁", "프롬프트 추천", "AI 도구 추천", "제미나이 활용법")
PER_SEARCH = 2
INTERVAL = timedelta(hours=11)   # 매시 수집 중 하루 2회쯤만 검색
MAX_AGE = timedelta(days=7)
MAX_CONTINUE = 3

POST = re.compile(r"threads\.com/(@[\w.]+)/post/([A-Za-z0-9_-]+)")
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
ID_EPOCH_MS = 1314220021721


def posted_at(code: str) -> datetime | None:
    """글 코드 → 게시 시각. 코드는 숫자 ID의 base64url이고, ID 상위 비트가 밀리초 시각이다."""
    n = 0
    for ch in code:
        n = n * 64 + ALPHABET.index(ch)
    try:
        return datetime.fromtimestamp(((n >> 23) + ID_EPOCH_MS) / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def queries(ctx: Context) -> list[str]:
    """회차 번호로 키워드를 돌려 쓰고, 최근 글이 잡히도록 연·월을 붙인다."""
    start = ctx.run_index // 12 * PER_SEARCH
    return [f"{KEYWORDS[(start + i) % len(KEYWORDS)]} {ctx.now.year}년 {ctx.now.month}월" for i in range(PER_SEARCH)]


def search(qs: list[str], client) -> list:
    """검색 결과 항목들. 서버 도구가 pause_turn으로 멈추면 이어서 요청한다."""
    prompt = ("web_search 도구로 아래 검색어를 각각 한 번씩만 검색하라. 결과는 프로그램이 직접 읽으니 요약하지 말고 '완료'라고만 답하라.\n"
              + "\n".join(qs))
    messages, found, errors = [{"role": "user", "content": prompt}], [], []
    for _ in range(MAX_CONTINUE):
        resp = client.beta.messages.create(model=MODEL, max_tokens=500, tools=[TOOL], messages=messages)
        for block in resp.content:
            if block.type != "web_search_tool_result":
                continue
            if isinstance(block.content, list):
                found += block.content
            else:
                errors.append(str(getattr(block.content, "error_code", "unknown")))
        if resp.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": resp.content}]
    if errors and not found:
        raise http.FetchError("웹검색 오류: " + ", ".join(errors))
    return found


def to_raw(result, now: datetime) -> dict | None:
    m = POST.search(getattr(result, "url", "") or "")
    text = clean(getattr(result, "title", ""))
    if not m or not text:
        return None
    user, code = m.groups()
    pub = posted_at(code)
    if not pub or not timedelta(days=-1) < now - pub <= MAX_AGE:
        return None
    return raw(id=f"threads-{code}", title=text[:60], url=f"https://www.threads.com/{user}/post/{code}",
               source="threads", excerpt=text, published_at=pub.isoformat())


def fetch(ctx: Context) -> list[dict]:
    last = ctx.threads_searched_at
    if not ctx.client or (last and ctx.now - parse_iso(last) < INTERVAL):
        return []
    found = search(queries(ctx), ctx.client)
    ctx.notes["threads_searched_at"] = iso(ctx.now)
    out, seen = [], set()
    for res in found:
        r = to_raw(res, ctx.now)
        if r and r["id"] not in seen and r["id"] not in ctx.known_ids:
            seen.add(r["id"])
            out.append(r)
    dates = [posted_at(m.group(2)) for m in (POST.search(getattr(res, "url", "") or "") for res in found) if m]
    newest = max((d for d in dates if d), default=None)
    ctx.notes["threads"] = (f"검색 결과 {len(found)}건 · 7일 안의 새 글 {len(out)}건"
                            + (f" · 가장 최근 글 {newest:%Y-%m-%d}" if newest else ""))
    return out
