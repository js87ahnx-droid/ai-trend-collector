from datetime import datetime, timedelta, timezone

from collector import store

NOW = datetime(2026, 10, 30, 10, 37, tzinfo=timezone.utc)


def item(i, **kw):
    base = {
        "id": f"hn-{i}", "title": f"t{i}", "url": f"https://example.com/{i}", "source": "hn",
        "theme": "model", "gist": "요약", "score": 1, "collected_at": store.iso(NOW),
    }
    base.update(kw)
    return base


def test_existing_id_skipped():
    existing = [item(1)]
    out = store.dedupe_candidates([item(1, url="https://other.com/x"), item(2)], existing)
    assert [c["id"] for c in out] == ["hn-2"]


def test_same_url_keeps_higher_score():
    a = item(1, id="gn-1", source="gn", url="https://blog.dev/post", score=3)
    b = item(2, id="hn-2", url="https://www.blog.dev/post/", score=90)
    out = store.dedupe_candidates([a, b], [])
    assert [c["id"] for c in out] == ["hn-2"]


def test_url_already_stored_is_skipped():
    existing = [item(1, url="https://blog.dev/post")]
    assert store.dedupe_candidates([item(2, url="https://blog.dev/post")], existing) == []


def test_rank_interleaves_sources_by_score():
    cands = [item(1, score=900), item(2, score=500), item(3, score=700),
             item(4, id="gn-4", source="gn", score=None), item(5, id="gn-5", source="gn", score=None)]
    assert [c["id"] for c in store.rank_for_run(cands)] == ["hn-1", "gn-4", "hn-3", "gn-5", "hn-2"]


def test_run_cap_15():
    _, added = store.add_items([], [item(i) for i in range(16)], NOW)
    assert len(added) == 15


def test_prune_7_days():
    old = item(1, collected_at=store.iso(NOW - timedelta(days=8)))
    edge = item(2, collected_at=store.iso(NOW - timedelta(days=6)))
    assert [i["id"] for i in store.prune([old, edge], NOW)] == ["hn-2"]


def test_rejects_http_and_bad_theme():
    _, added = store.add_items([], [item(1, url="http://x.com"), item(2, theme="misc"), item(3)], NOW)
    assert [a["id"] for a in added] == ["hn-3"]


def test_added_items_get_run_stamp_and_drop_none():
    _, added = store.add_items([], [item(1, score=None, collected_at="x", excerpt="본문")], NOW)
    assert added[0]["collected_at"] == "2026-10-30T10:37:00Z"
    assert "score" not in added[0] and "excerpt" not in added[0]
