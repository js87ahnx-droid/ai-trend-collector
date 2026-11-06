from datetime import datetime, timedelta, timezone

from collector import detail
from collector.sources import feeds
from collector.sources import threadprofile as tp
from collector.sources.base import Context
from types import SimpleNamespace as NS

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
LONG = "클로드에 캡처 한 장을 올리고 내 주제로 다시 만들어 달라고 부탁하면 돼요. 설치 없이 바로 쓸 수 있습니다."


def post(code, minutes_ago, text=LONG, user="ai.trend.kr", stamp="1시간"):
    at = NOW - timedelta(minutes=minutes_ago)
    return {"href": f"/@{user}/post/{code}", "at": at.isoformat().replace("+00:00", "Z"), "stamp": stamp,
            "text": f"{user}\nAI Threads\n{stamp}\n{text}\n7\n2\n1.2천"}


def test_post_text_drops_header_and_counts():
    assert tp.post_text(post("A", 1)) == LONG


def test_merges_follow_ups_into_head_post():
    follow = post("B", 59.5, "이어 쓴 설명입니다.")
    out = tp.to_raws([follow, post("A", 60)], NOW, set())
    assert [r["id"] for r in out] == ["threads-A"]
    assert out[0]["url"] == "https://www.threads.com/@ai.trend.kr/post/A"
    assert "이어 쓴 설명" in out[0]["body"] and len(out[0]["title"]) <= 60


def test_drops_old_known_short():
    posts = [post("OLD", 60 * 24 * 4), post("KNOWN", 300), post("SHORT", 600, "풀영상 링크")]
    assert tp.to_raws(posts, NOW, {"threads-KNOWN"}) == []


def test_keeps_newest_per_account():
    posts = [post(f"P{i}", 10 * i + 10) for i in range(5)]
    assert [r["id"] for r in tp.to_raws(posts, NOW, set())] == ["threads-P0", "threads-P1", "threads-P2"]


def test_fetch_records_and_stamps():
    ctx = Context(now=NOW)
    out = tp.fetch(ctx, load=lambda: {"a": [post("A", 5, user="a")], "b": []})
    assert [r["id"] for r in out] == ["threads-A"]
    assert ctx.notes["threads"] == "프로필 2곳 중 1곳 읽음 · 새 글 1건"
    assert ctx.notes["threads_searched_at"] == "2026-11-06T12:00:00Z"


def test_blocked_profiles_still_wait_until_next_turn():
    ctx = Context(now=NOW)
    assert tp.fetch(ctx, load=lambda: {"a": []}) == []
    assert ctx.notes["threads_searched_at"]
    def fail():
        raise AssertionError("열면 안 됨")
    later = Context(now=NOW + timedelta(hours=2), threads_searched_at=ctx.notes["threads_searched_at"])
    assert tp.fetch(later, load=fail) == []
    again = Context(now=NOW + timedelta(hours=12), threads_searched_at=ctx.notes["threads_searched_at"])
    assert tp.fetch(again, load=lambda: {"a": []}) == []
    assert again.notes["threads_searched_at"]


class Client:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def test_detail_uses_collected_text_for_threads():
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
