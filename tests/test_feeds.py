from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector import http
from collector.sources import feeds
from collector.sources.base import Context

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)
POD = feeds.Feed("sample", "https://x/feed", "pod", 3, feeds.timedelta(days=7))


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_rss_parse_and_age_and_https(monkeypatch):
    monkeypatch.setattr(http, "get_text", lambda url, **kw: read("feed_substack.xml"))
    out = feeds.fetch_feed(POD, Context(now=NOW))
    assert len(out) == 1                                   # 7일 넘은 글·http 링크 제외
    r = out[0]
    assert r["source"] == "pod" and r["id"].startswith("pod-") and len(r["id"]) == 14
    assert r["title"] == "How AI product teams write evals before PRDs"
    assert r["excerpt"] == "Episode summary placeholder & more"


def test_atom_product_hunt_id():
    e = feeds.parse(read("feed_ph.xml"))[0]
    ph = feeds.Feed("ph", "u", "ph", 30, feeds.timedelta(hours=48))
    r = feeds.to_raw(e, ph)
    assert r["id"] == "ph-1297603" and r["url"] == "https://www.producthunt.com/products/sample-ai"
    assert r["excerpt"] == "Turns meeting audio into to-dos"


def test_show_hn_uses_hn_item_id():
    e = feeds.parse(read("feed_showhn.xml"))[0]
    showhn = feeds.Feed("showhn", "u", "hn", 30, feeds.timedelta(hours=48))
    r = feeds.to_raw(e, showhn)
    assert r["id"] == "hn-50300001" and r["url"] == "https://github.com/example/nesformer"


def test_one_feed_failure_does_not_stop_others(monkeypatch):
    def fake(url, **kw):
        if "lenny" in url:
            raise http.FetchError("HTTP 503 lenny")
        return read("feed_showhn.xml") if "showrss" in url else "<rss><channel></channel></rss>"

    monkeypatch.setattr(http, "get_text", fake)
    ctx = Context(now=NOW)
    out = feeds.fetch(ctx)
    assert [r["id"] for r in out] == ["hn-50300001"]
    assert "lenny" in ctx.notes["feeds"][0]


def test_all_feeds_failing_raises(monkeypatch):
    monkeypatch.setattr(http, "get_text", lambda url, **kw: (_ for _ in ()).throw(http.FetchError("down")))
    with pytest.raises(http.FetchError):
        feeds.fetch(Context(now=NOW))
