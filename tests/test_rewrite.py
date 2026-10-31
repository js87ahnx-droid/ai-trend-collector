from datetime import datetime, timezone

from collector import rewrite, store
from tests.test_pipeline import DETAIL, FakeClient, msg

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)


def stored(i, **kw):
    return {"id": f"hn-{i}", "title": f"t{i}", "url": f"https://ex.com/{i}", "source": "hn", "cat": "tool",
            "headline": f"어려운 헤드라인 {i}", "tag": "무료", "why": "이유", "pick": 5, "buzz": True,
            "detail": DETAIL, "collected_at": store.iso(NOW)} | kw


def test_rewrite_changes_text_only_and_keeps_failures(tmp_path):
    items = [stored(1), stored(2, superseded_by="hn-1", superseded_at=store.iso(NOW)), stored(3)]
    store.save_json(tmp_path / "items.json", store.items_doc(items))

    def answer(kw):
        prompt = kw["messages"][0]["content"]
        if "어려운 헤드라인 3" in prompt:
            return {"bad": True}          # 필드 누락 → 이 글은 그대로
        return DETAIL | {"headline": "쉬운 헤드라인", "why": "쉬운 이유", "buzz": False, "pick": 9,
                         "terms": [{"term": "MCP", "meaning": "연결 방식"}]}

    assert rewrite.main(tmp_path, FakeClient(answer)) == 0
    out = store.load_items(tmp_path / "items.json")
    assert [i["id"] for i in out] == ["hn-1", "hn-2", "hn-3"]
    a, b, c = out
    assert a["headline"] == "쉬운 헤드라인" and a["why"] == "쉬운 이유" and a["pick"] == 9 and "buzz" not in a
    assert a["detail"]["terms"][0]["term"] == "MCP"
    assert a["url"] == "https://ex.com/1" and a["cat"] == "tool" and a["collected_at"] == store.iso(NOW)
    assert b["superseded_by"] == "hn-1"
    assert c["headline"] == "어려운 헤드라인 3" and c["buzz"] is True


def test_rewrite_all_failed_returns_error(tmp_path):
    store.save_json(tmp_path / "items.json", store.items_doc([stored(1)]))
    assert rewrite.main(tmp_path, FakeClient(lambda kw: msg({}, stop_reason="refusal"))) == 1
