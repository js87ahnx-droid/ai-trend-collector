from datetime import datetime, timedelta, timezone

from collector import store

NOW = datetime(2026, 10, 30, 10, 37, tzinfo=timezone.utc)
DETAIL = {"points": ["하나", "둘", "셋"], "steps": ["해보기"], "extra": "", "figure": None}


def item(i, **kw):
    base = {
        "id": f"hn-{i}", "title": f"t{i}", "url": f"https://example.com/{i}", "source": "hn",
        "cat": "tip", "headline": f"헤드라인 {i}", "tag": "5분", "detail": DETAIL, "score": 1,
        "collected_at": store.iso(NOW),
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


def test_run_cap():
    _, added = store.add_items([], [item(i) for i in range(store.MAX_PER_RUN + 3)], NOW)
    assert len(added) == store.MAX_PER_RUN


def test_rejects_missing_v2_fields_and_bad_values():
    bad = [item(1, url="http://x.com"), item(2, cat="model"), item(3, headline=""),
           item(4, detail={"points": ["하나뿐"]}), item(5, detail=None)]
    _, added = store.add_items([], bad + [item(6)], NOW)
    assert [a["id"] for a in added] == ["hn-6"]


def test_added_items_get_run_stamp_and_drop_none():
    _, added = store.add_items([], [item(1, score=None, collected_at="x", excerpt="본문")], NOW)
    assert added[0]["collected_at"] == "2026-10-30T10:37:00Z"
    assert "score" not in added[0] and "excerpt" not in added[0]


def test_replaces_marks_old_item():
    old = item(1)
    items, added = store.add_items([old], [item(2, replaces="hn-1"), item(3, replaces="hn-999")], NOW)
    assert old["superseded_by"] == "hn-2" and old["superseded_at"] == store.iso(NOW)
    assert added[0]["replaces"] == "hn-1"
    assert "replaces" not in added[1]                      # 없는 글을 가리키면 버림


def test_already_superseded_not_replaced_twice():
    old = item(1, superseded_by="hn-2", superseded_at=store.iso(NOW))
    _, added = store.add_items([old, item(2)], [item(3, replaces="hn-1")], NOW)
    assert old["superseded_by"] == "hn-2" and "replaces" not in added[0]


def test_prune_superseded_3_days_normal_7_days():
    items = [
        item(1, superseded_by="hn-2", superseded_at=store.iso(NOW - timedelta(days=4))),
        item(2, replaces="hn-1"),
        item(3, superseded_by="hn-2", superseded_at=store.iso(NOW - timedelta(days=2))),
        item(4, collected_at=store.iso(NOW - timedelta(days=8))),
        item(5, collected_at=store.iso(NOW - timedelta(days=6))),
    ]
    kept = store.prune(items, NOW)
    assert [i["id"] for i in kept] == ["hn-2", "hn-3", "hn-5"]
    assert "replaces" not in kept[0]                         # 지워진 hn-1 참조 정리


def test_figure_validation():
    assert store.clean_figure({"type": "flow", "steps": ["a", "b", "c"]})["steps"] == ["a", "b", "c"]
    assert store.clean_figure({"type": "flow", "steps": ["a"]}) is None
    assert store.clean_figure({"type": "svg", "raw": "<svg/>"}) is None
    bars = store.clean_figure({"type": "bars", "unit": "곳", "bars": [{"label": "전", "value": 7}, {"label": "후", "value": "30"},
                                                                    {"label": "후", "value": 30}]})
    assert bars["bars"] == [{"label": "전", "value": 7}, {"label": "후", "value": 30}]
    c = store.clean_figure({"type": "compare", "before": "x" * 500, "after": "y"})
    assert len(c["before"]) == 140 and c["before_label"] == "전"
    assert store.clean_figure({"type": "quote", "text": "요지", "speaker": "누군가"})["speaker"] == "누군가"


def test_old_schema_file_is_ignored(tmp_path):
    p = tmp_path / "items.json"
    store.save_json(p, {"items": [item(1)]})
    assert store.load_items(p) == []
    store.save_json(p, store.items_doc([item(1)]))
    assert len(store.load_items(p)) == 1
