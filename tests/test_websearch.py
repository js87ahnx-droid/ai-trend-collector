from datetime import datetime, timedelta, timezone

from collector.sources import websearch as ws
from collector.sources.base import Context

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def test_doc_id_rules():
    assert ws.doc_id("https://www.reddit.com/r/ClaudeAI/comments/1abcxyz/some_title/") == ("reddit", "reddit-1abcxyz")
    assert ws.doc_id("https://www.threads.com/@someone/post/DAbc_12-x") == ("threads", "threads-DAbc_12-x")
    assert ws.doc_id("https://www.reddit.com/r/ClaudeAI/") is None
    src, id_ = ws.doc_id("https://blog.example.com/ai-tips")
    assert src == "web" and id_.startswith("web-") and len(id_) == 14


def test_drops_old_or_undated():
    base = {"url": "https://www.reddit.com/r/OpenAI/comments/abc123/x/", "title": "t", "description": "d"}
    assert ws.to_raw({**base, "age": "3 hours ago"}, NOW)["id"] == "reddit-abc123"
    assert ws.to_raw({**base, "page_age": (NOW - timedelta(hours=49)).isoformat()}, NOW) is None
    assert ws.to_raw(base, NOW) is None
    assert ws.to_raw({**base, "url": "http://x.com", "age": "1 hour ago"}, NOW) is None


def test_snippet_counts_only_when_present():
    assert ws.snippet_counts("1.2k votes, 340 comments. blah") == (1200, 340)
    assert ws.snippet_counts("no numbers here") == (None, None)


def test_query_count_and_rotation():
    q0, q1 = ws.queries_for_run(0), ws.queries_for_run(1)
    assert 5 <= len(q0) <= 8
    assert q0 != q1
    assert all(q.startswith("site:reddit.com/r/") for s, q in q0 if s == "reddit")
    assert all(q.startswith("site:threads.com") for s, q in q0 if s == "threads")


def test_no_key_skips_without_calls(monkeypatch):
    monkeypatch.setattr(ws, "search", lambda *a: 1 / 0)
    assert ws.fetch(Context(now=NOW)) == []


def test_fetch_calls_capped_and_filters(monkeypatch):
    calls = []

    def fake_search(q, key):
        calls.append(q)
        return [
            {"url": "https://www.reddit.com/r/ChatGPT/comments/zz1/a/", "title": "Prompt tip", "age": "2 hours ago",
             "description": "512 votes, 40 comments"},
            {"url": "https://www.reddit.com/r/ChatGPT/", "title": "sub page", "age": "1 hour ago"},
        ]

    monkeypatch.setattr(ws, "search", fake_search)
    out = ws.fetch(Context(now=NOW, search_api_key="k"), sleep=lambda s: None)
    assert 5 <= len(calls) <= 8
    assert [r["id"] for r in out] == ["reddit-zz1"]
    assert out[0]["score"] == 512 and out[0]["comments"] == 40
