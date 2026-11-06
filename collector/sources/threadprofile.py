"""AI 소식을 올리는 Threads 공개 프로필을 브라우저로 읽어 수집 (개인 용도, 하루 2회쯤, 적은 양).

페이지가 자바스크립트로 그려져 헤드리스 Chromium이 필요하다. 신원을 숨기지 않으며,
로그인 화면이나 차단이 나오면 우회하지 않고 다음 예정 시각까지 쉰다.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..store import iso, load_status, parse_iso
from .base import Context, clean, raw

log = logging.getLogger("collector")

ACCOUNTS = ("choi.openai", "ai.trend.kr", "koreatech_ai", "sodam_ai", "biggerthanseoul.ai", "jwon.ig")
INTERVAL = timedelta(hours=11)   # 매시 수집 중 하루 2회쯤만 연다
MAX_AGE = timedelta(days=3)
MAX_PER_ACCOUNT = 3
MIN_TEXT = 40
SAME_THREAD = timedelta(seconds=60)   # 이어 쓴 글은 몇 초 간격으로 올라온다

READ_POSTS = """() => {
  const out = [], seen = new Set();
  for (const t of document.querySelectorAll('a[href*="/post/"] time')) {
    const a = t.closest('a'), href = a.getAttribute('href');
    if (seen.has(href)) continue;
    seen.add(href);
    let c = a;
    for (let i = 0; i < 9 && c.parentElement; i++) { c = c.parentElement; if (c.innerText.length > 60) break; }
    out.push({href, at: t.getAttribute('datetime'), stamp: t.innerText, text: c.innerText});
  }
  return out;
}"""


def post_text(post: dict) -> str:
    """글 상자 글자에서 작성자·시각 머리와 좋아요 같은 끝 숫자를 뗀다."""
    lines = [l.strip() for l in post["text"].splitlines() if l.strip()]
    stamp = (post.get("stamp") or "").strip()
    if stamp in lines:
        lines = lines[lines.index(stamp) + 1:]
    while lines and lines[-1].replace(",", "").replace(".", "").replace("천", "").replace("만", "").isdigit():
        lines.pop()
    return clean(" ".join(lines))


def to_raws(posts: list[dict], now: datetime, known: set[str]) -> list[dict]:
    """한 계정의 글 → 후보. 몇 초 간격으로 이어 쓴 글은 첫 글 하나로 합치고 최신 몇 건만 남긴다."""
    parsed = sorted(((parse_iso(p["at"]), p) for p in posts if p.get("at") and "/post/" in p.get("href", "")),
                    key=lambda x: x[0])
    groups: list[list] = []
    for at, p in parsed:
        if groups and at - groups[-1][-1][0] <= SAME_THREAD:
            groups[-1].append((at, p))
        else:
            groups.append([(at, p)])
    out = []
    for group in reversed(groups):
        at, head = group[0]
        text = " ".join(post_text(p) for _, p in group).strip()
        path = head["href"].split("?")[0]
        code = path.rstrip("/").rsplit("/", 1)[-1]
        id_ = f"threads-{code}"
        if now - at > MAX_AGE or id_ in known or len(text) < MIN_TEXT:
            continue
        out.append(raw(id=id_, title=post_text(head)[:60] or text[:60], url=f"https://www.threads.com{path}",
                       source="threads", excerpt=text, published_at=at.isoformat()))
        if len(out) >= MAX_PER_ACCOUNT:
            break
    return out


def load_profiles(accounts=ACCOUNTS) -> dict[str, list[dict]]:
    """계정별 글 목록. 글이 안 보이는 계정(로그인 화면 등)은 빈 목록."""
    from playwright.sync_api import sync_playwright   # CI에서 검색 차례에만 설치

    result = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="ko-KR")
        for name in accounts:
            try:
                page.goto(f"https://www.threads.com/@{name}", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector('a[href*="/post/"] time', timeout=15000)
                result[name] = page.evaluate(READ_POSTS)
            except Exception as e:
                log.info("프로필 읽기 실패 %s: %s", name, e)
                result[name] = []
            time.sleep(2)
        browser.close()
    return result


def due(last: str | None, now: datetime) -> bool:
    return not last or now - parse_iso(last) >= INTERVAL


def fetch(ctx: Context, load=load_profiles) -> list[dict]:
    if not due(ctx.threads_searched_at, ctx.now):
        return []
    ctx.notes["threads_searched_at"] = iso(ctx.now)   # 막혀도 다음 예정 시각까지 다시 열지 않는다
    profiles = load()
    out, seen = [], set(ctx.known_ids)
    for posts in profiles.values():
        got = to_raws(posts, ctx.now, seen)
        seen |= {r["id"] for r in got}
        out += got
    readable = sum(1 for posts in profiles.values() if posts)
    ctx.notes["threads"] = f"프로필 {len(profiles)}곳 중 {readable}곳 읽음 · 새 글 {len(out)}건"
    return out


if __name__ == "__main__":
    # 워크플로가 브라우저 설치 여부를 정할 때 쓴다: 0=이번 회차에 읽을 차례
    from datetime import timezone

    status = load_status(Path(sys.argv[1]) / "status.json")
    sys.exit(0 if due(status.get("threads_searched_at"), datetime.now(timezone.utc)) else 1)
