from datetime import datetime, timezone
from pathlib import Path

from collector import http
from collector.sources import gn, gpters, hn
from collector.sources.base import Context

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def test_gn_splits_gnp_by_author():
    items = gn.parse((FX / "gn.xml").read_text(encoding="utf-8"))
    assert len(items) == 6
    assert sum(i["source"] == "gnp" for i in items) == 3
    first = items[0]
    assert first["id"].startswith("gn-") and first["id"][3:].isdigit()
    assert first["url"].startswith("https://news.hada.io/topic?id=")
    assert first["origin"] == "news.hada.io"
    assert first["excerpt"] == "본문 자리표시 텍스트 & 태그"


def test_hn_to_raw():
    r = hn.to_raw({"id": 9, "type": "story", "title": "LLM thing", "url": "https://x.dev/a",
                   "score": 120, "descendants": 33, "time": 1761800000})
    assert r["id"] == "hn-9" and r["url"] == "https://x.dev/a" and r["origin"] == "x.dev"
    assert r["score"] == 120 and r["comments"] == 33


def test_hn_http_url_falls_back_to_discussion():
    r = hn.to_raw({"id": 9, "type": "story", "title": "t", "url": "http://insecure.org", "time": 0})
    assert r["url"] == "https://news.ycombinator.com/item?id=9"
    assert hn.to_raw({"id": 1, "type": "job"}) is None


def test_gpters_sitemap_and_post(monkeypatch):
    sm = (FX / "gpters_sitemap.xml").read_text(encoding="utf-8")
    post = (FX / "gpters_post.html").read_text(encoding="utf-8")
    entries = gpters.parse_sitemap(sm)
    assert entries and all("/post/" in u for u, _ in entries)
    assert gpters.post_id("https://www.gpters.org/dev/post/tried-making-claude-code-RyRmEROlGFnu1AZ") == "gpters-RyRmEROlGFnu1AZ"

    calls = []

    def fake_get_text(url, **kw):
        calls.append(url)
        return sm if url.endswith("sitemap.xml") else post

    monkeypatch.setattr(http, "get_text", fake_get_text)
    out = gpters.fetch(Context(now=NOW), sleep=lambda s: None)
    page_calls = [c for c in calls if "/post/" in c]
    assert len(page_calls) <= gpters.MAX_PAGES
    assert out[0]["source"] == "gpters" and out[0]["title"].startswith("클로드 코드")
    assert out[0]["excerpt"] == "게시글 설명 자리표시 & 요약"


def test_gpters_skips_known_ids(monkeypatch):
    sm = (FX / "gpters_sitemap.xml").read_text(encoding="utf-8")
    known = {gpters.post_id(u) for u, _ in gpters.parse_sitemap(sm)}
    monkeypatch.setattr(http, "get_text", lambda url, **kw: sm if url.endswith("sitemap.xml") else 1 / 0)
    assert gpters.fetch(Context(now=NOW, known_ids=known), sleep=lambda s: None) == []


def test_http_retries_with_backoff(monkeypatch):
    import urllib.error
    attempts, waits = [], []

    def boom(req, timeout):
        attempts.append(1)
        raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", boom)
    try:
        http.get("https://x.dev", sleep=waits.append)
    except http.FetchError:
        pass
    assert len(attempts) == 3 and waits == [1.0, 2.0]
