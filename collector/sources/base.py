"""소스 공통 타입과 도우미."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

MAX_PER_SOURCE = 30


@dataclass
class Context:
    now: datetime
    known_ids: set[str] = field(default_factory=set)
    run_index: int = 0          # 회차 번호 — 검색 키워드 순환에 사용
    search_api_key: str | None = None
    client: object = None       # Claude API 클라이언트 — 스레드 웹검색에 사용
    threads_searched_at: str | None = None   # 마지막 스레드 검색 시각
    notes: dict = field(default_factory=dict)   # 소스가 남기는 부분 실패 메모 (status에 기록)


def raw(*, id, title, url, source, score=None, comments=None, excerpt="", published_at=None) -> dict:
    return {
        "id": id,
        "title": clean(title),
        "url": url,
        "source": source,
        "score": score,
        "comments": comments,
        "origin": urlparse(url).hostname.removeprefix("www.") if url else None,
        "excerpt": clean(excerpt)[:300],
        "body": clean(excerpt)[:3000],   # 상세 작성 때 원문을 못 읽으면 쓰는 대체 본문 (저장하지 않음)
        "published_at": published_at,
    }


TAG = re.compile(r"<[^>]+>")


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", s or ""))).strip()
