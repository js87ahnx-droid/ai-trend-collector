from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from collector import detail, http
from collector.sources import feeds
from collector.sources import threadsearch as ts
from collector.sources.base import Context

NOW = datetime(2026, 3, 5, 12, tzinfo=timezone.utc)
FRESH = "DVZ6Q7GkjxA"   # 2026-03-03 글
OLD = "DHZxovuySLN"     # 2025-03-20 글


def result(code, title="클로드 메모리 가져오기 사용법", user="@ai.trend.kr"):
    return NS(url=f"https://www.threads.com/{user}/post/{code}/slug?hl=ko", title=title, page_age=None)


class Client:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def reply(*results, stop="end_turn"):
    return NS(stop_reason=stop, content=[NS(type="web_search_tool_result", content=list(results)), NS(type="text", text="완료")])


def test_posted_at_matches_known_posts():
    assert ts.posted_at(FRESH).date().isoformat() == "2026-03-03"
    assert ts.posted_at(OLD).date().isoformat() == "2025-03-20"


def test_to_raw_trims_url_and_title():
    r = ts.to_raw(result(FRESH, "가" * 100), NOW)
    assert r["id"] == f"threads-{FRESH}" and r["source"] == "threads"
    assert r["url"] == f"https://www.threads.com/@ai.trend.kr/post/{FRESH}"
    assert len(r["title"]) == 60 and len(r["excerpt"]) == 100


def test_to_raw_drops_old_and_non_posts():
    assert ts.to_raw(result(OLD), NOW) is None
    assert ts.to_raw(NS(url="https://www.threads.com/@someone", title="프로필"), NOW) is None
    assert ts.to_raw(result(FRESH, "  "), NOW) is None


def test_fetch_filters_records_and_limits_domain():
    client = Client(reply(result(FRESH), result(FRESH), result(OLD)))
    ctx = Context(now=NOW, client=client)
    out = ts.fetch(ctx)
    assert [r["id"] for r in out] == [f"threads-{FRESH}"]
    assert ctx.notes["threads_searched_at"] == "2026-03-05T12:00:00Z"
    assert "검색 결과 3건" in ctx.notes["threads"] and "새 글 1건" in ctx.notes["threads"]
    assert client.calls[0]["tools"][0]["allowed_domains"] == ["threads.com"]


def test_fetch_skips_known_ids():
    ctx = Context(now=NOW, client=Client(reply(result(FRESH))), known_ids={f"threads-{FRESH}"})
    assert ts.fetch(ctx) == []


def test_fetch_waits_interval_then_searches_again():
    client = Client(reply())
    recent = (NOW - timedelta(hours=2)).isoformat()
    assert ts.fetch(Context(now=NOW, client=client, threads_searched_at=recent)) == []
    assert client.calls == []
    assert ts.fetch(Context(now=NOW, client=client, threads_searched_at=(NOW - timedelta(hours=12)).isoformat())) == []
    assert len(client.calls) == 1


def test_fetch_without_client_does_nothing():
    assert ts.fetch(Context(now=NOW)) == []


def test_search_error_raises_and_leaves_no_stamp():
    bad = NS(stop_reason="end_turn", content=[NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded"))])
    ctx = Context(now=NOW, client=Client(bad))
    with pytest.raises(http.FetchError):
        ts.fetch(ctx)
    assert "threads_searched_at" not in ctx.notes


def test_search_continues_after_pause_turn():
    client = Client(reply(result(FRESH), stop="pause_turn"), reply(result("DVZ6Q7GkjxB")))
    assert len(ts.search(["q"], client)) == 2
    assert len(client.calls) == 2


def test_queries_rotate_and_carry_month():
    a = ts.queries(Context(now=NOW, run_index=0))
    b = ts.queries(Context(now=NOW, run_index=12))
    assert len(a) == ts.PER_SEARCH and a != b
    assert all(q.endswith("2026년 3월") for q in a)


def test_detail_does_not_fetch_threads_pages():
    text = '{"insufficient": false, "headline": "h", "points": ["a", "b", "c"], "steps": [], "extra": "", "figure": {"type": "none"}, "terms": []}'
    client = Client(*[NS(stop_reason="end_turn", content=[NS(type="text", text=text)]) for _ in range(2)])
    base = {"title": "t", "headline": "h", "cat": "tip", "url": "https://x", "excerpt": "e"}
    detail.write(base | {"source": "threads"}, client)
    detail.write(base | {"source": "nl"}, client)
    assert "tools" not in client.calls[0] and client.calls[1]["tools"] == [detail.WEB_FETCH]


def test_feed_without_timezone_uses_feed_tz():
    xml = "<rss><channel><item><title>t</title><link>https://x</link><pubDate>2026-11-05 18:18:08</pubDate></item></channel></rss>"
    assert feeds.parse(xml, feeds.KST)[0]["published"] == datetime(2026, 11, 5, 9, 18, 8, tzinfo=timezone.utc)
    assert feeds.parse(xml)[0]["published"] == datetime(2026, 11, 5, 18, 18, 8, tzinfo=timezone.utc)
