import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from collector import classify, detail, main, store
from collector.sources.base import raw

NOW = datetime(2026, 10, 30, 12, 37, tzinfo=timezone.utc)
DETAIL = {"points": ["핵심 하나", "핵심 둘", "핵심 셋"], "steps": ["해보기"], "extra": "",
          "figure": {"type": "flow", "title": "", "before": "", "after": "", "before_label": "", "after_label": "",
                     "steps": ["입력", "출력"], "unit": "", "bars": [], "text": "", "speaker": ""}}


def msg(payload, stop_reason="end_turn"):
    text = json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


class FakeClient:
    """분류 요청(tools 없음)과 상세 요청(web_fetch 도구)을 구분해 응답한다."""

    def __init__(self, classify_fn=None, detail_fn=None):
        self.classify_fn = classify_fn or (lambda kw: {"results": []})
        self.detail_fn = detail_fn or (lambda kw: msg(DETAIL))
        self.calls = []
        self.lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        with self.lock:
            self.calls.append(kw)
        if kw.get("tools"):
            return self.detail_fn(kw)
        out = self.classify_fn(kw)
        return out if isinstance(out, SimpleNamespace) else msg(out)

    def classify_calls(self):
        return [c for c in self.calls if not c.get("tools")]


def r(id_, title, source="hn", **kw):
    return raw(id=id_, title=title, url=f"https://ex.com/{id_}", source=source, **kw)


def ids_in(kw):
    return [json.loads(l)["id"] for l in kw["messages"][0]["content"].splitlines() if l.startswith("{")]


def label_all(cat="tip", pick=5, replaces=""):
    return lambda kw: {"results": [{"id": i, "skip": False, "cat": cat, "headline": f"{i} 헤드라인", "tag": "5분",
                                    "why": "이유", "pick": pick, "replaces": replaces} for i in ids_in(kw)]}


def test_prefilter_general_sources_need_ai_words_and_ads_dropped():
    cands = [
        r("hn-1", "New open-weights LLM tops benchmark"),
        r("hn-2", "Rust 2.0 released"),
        r("gn-3", "클로드로 회의록 자동화", source="gn"),
        r("gpters-4", "주간 회고", source="gpters"),
        r("gpters-5", "ChatGPT 강의 오픈! 얼리버드 할인", source="gpters"),
        r("pod-6", "Gardening with grandpa", source="pod"),
        r("ph-7", "AI notetaker for meetings", source="ph"),
    ]
    assert [c["id"] for c in classify.prefilter(cands)] == ["hn-1", "gn-3", "gpters-4", "ph-7"]


def test_classify_filters_and_sorts_by_pick():
    cands = [r("hn-1", "a"), r("hn-2", "b"), r("hn-3", "c"), r("hn-4", "d")]
    res = [
        {"id": "hn-1", "skip": False, "cat": "tool", "headline": "무료 CLI", "tag": "무료", "why": "w", "pick": 4, "replaces": "x-9"},
        {"id": "hn-2", "skip": True, "cat": "none", "headline": "", "tag": "", "why": "", "pick": 0, "replaces": ""},
        {"id": "hn-3", "skip": False, "cat": "model", "headline": "h", "tag": "t", "why": "", "pick": 9, "replaces": ""},
        {"id": "hn-4", "skip": False, "cat": "tip", "headline": "팁", "tag": "", "why": "", "pick": 15, "replaces": "gn-1"},
        {"id": "hn-9", "skip": False, "cat": "tip", "headline": "없는 id", "tag": "", "why": "", "pick": 5, "replaces": ""},
    ]
    client = FakeClient(lambda kw: {"results": res})
    stored = [{"id": "gn-1", "cat": "tip", "headline": "예전 팁"}]
    out, skipped = classify.classify(cands, client, stored)
    assert [(o["id"], o["pick"], o["replaces"]) for o in out] == [("hn-4", 10, "gn-1"), ("hn-1", 4, None)]
    assert skipped == ["hn-2", "hn-3"]                     # 응답에 없는 id는 어느 쪽에도 없음
    assert out[0]["tag"] == "참고"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "gn-1 | tip | 예전 팁" in prompt and client.calls[0]["fallbacks"] == "default"


def test_classify_excludes_superseded_from_stored():
    client = FakeClient()
    classify.classify([r("hn-1", "a")], client, [{"id": "gn-1", "cat": "tip", "headline": "옛 팁", "superseded_by": "x"}])
    assert "옛 팁" not in client.calls[0]["messages"][0]["content"]


def test_refusal_raises():
    with pytest.raises(classify.LLMError):
        classify.classify([r("hn-1", "a")], FakeClient(lambda kw: msg({"results": []}, "refusal")))


def test_detail_uses_web_fetch_and_continues_after_pause():
    seq = [msg({}, "pause_turn"), msg(DETAIL)]
    client = FakeClient(detail_fn=lambda kw: seq.pop(0))
    item = r("hn-1", "t") | {"headline": "h", "cat": "tip"}
    out = detail.write(item, client)
    assert out["detail"]["points"][0] == "핵심 하나"
    first, second = client.calls
    assert first["tools"][0]["type"] == "web_fetch_20260209"
    assert "https://ex.com/hn-1" in first["messages"][0]["content"]
    assert second["messages"][-1]["role"] == "assistant"


def test_detail_can_correct_headline():
    item = r("hn-1", "t") | {"headline": "2.1.312·313 점검", "cat": "shift"}
    out = detail.write(item, FakeClient(detail_fn=lambda kw: msg(DETAIL | {"headline": "2.1.313 점검"})))
    assert out["headline"] == "2.1.313 점검" and "headline" not in out["detail"]
    same = detail.write(item, FakeClient(detail_fn=lambda kw: msg(DETAIL | {"headline": " "})))
    assert same["headline"] == "2.1.312·313 점검"


def test_detail_failure_isolated():
    def det(kw):
        if "hn-2" in kw["messages"][0]["content"]:
            raise RuntimeError("fetch 실패")
        return msg(DETAIL)

    items = [r(f"hn-{i}", "t") | {"headline": "h", "cat": "tip"} for i in (1, 2, 3)]
    ok, errs, thin = detail.write_all(items, FakeClient(detail_fn=det))
    assert [o["id"] for o in ok] == ["hn-1", "hn-3"] and "hn-2" in errs[0] and thin == []


def test_detail_insufficient_reported_separately():
    def det(kw):
        if "hn-2" in kw["messages"][0]["content"]:
            return msg(DETAIL | {"insufficient": True})
        return msg(DETAIL | {"insufficient": False})

    items = [r(f"hn-{i}", "t") | {"headline": "h", "cat": "tip"} for i in (1, 2)]
    ok, errs, thin = detail.write_all(items, FakeClient(detail_fn=det))
    assert [o["id"] for o in ok] == ["hn-1"] and errs == [] and thin == ["hn-2"]
    assert "insufficient" not in ok[0]["detail"]


class Boom:
    @staticmethod
    def fetch(ctx):
        raise RuntimeError("소스 다운")


class Good:
    @staticmethod
    def fetch(ctx):
        return [r("hn-10", "Claude agent memory tips", score=50),
                r("hn-11", "Gardening news"),
                r("hn-12", "GPT prompt trick", score=5)]


def test_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    old = {"id": "hn-1", "title": "old", "url": "https://ex.com/old", "source": "hn", "cat": "tip", "headline": "옛",
           "tag": "t", "detail": DETAIL, "collected_at": store.iso(NOW - timedelta(days=8))}
    store.save_json(tmp_path / "items.json", store.items_doc([old]))
    client = FakeClient(label_all())
    st = main.run(NOW, tmp_path, client, sources=[("boom", Boom), ("hn", Good)])

    items = store.load_items(tmp_path / "items.json")
    assert sorted(i["id"] for i in items) == ["hn-10", "hn-12"]           # 8일 지난 글 삭제, 비AI 글 제외
    it = items[0]
    assert it["collected_at"] == st["last_run"] and it["detail"]["figure"]["type"] == "flow"
    assert "body" not in it and "excerpt" not in it
    assert "소스 다운" in st["sources"]["boom"]["error"]
    assert st["sources"]["hn"] == {"fetched": 3, "kept": 2}
    assert len(client.classify_calls()) == 1 and len(client.calls) == 3      # 분류 1 + 상세 2


def test_run_replacement_marks_old(tmp_path):
    main.run(NOW, tmp_path, FakeClient(label_all()), sources=[("hn", Good)])

    class Newer:
        @staticmethod
        def fetch(ctx):
            return [r("hn-20", "Better Claude agent memory approach")]

    main.run(NOW + timedelta(hours=1), tmp_path, FakeClient(label_all(replaces="hn-10")), sources=[("hn", Newer)])
    items = {i["id"]: i for i in store.load_items(tmp_path / "items.json")}
    assert items["hn-10"]["superseded_by"] == "hn-20" and items["hn-20"]["replaces"] == "hn-10"


def test_run_without_key_still_updates_last_run(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st = main.run(NOW, tmp_path, None, sources=[("hn", Good)])
    assert st["last_run"] == "2026-10-30T12:37:00Z"
    assert "ANTHROPIC_API_KEY" in st["run"]["note"]
    assert store.load_items(tmp_path / "items.json") == []


def test_skipped_ids_remembered_and_expire(tmp_path):
    def judge(kw):
        return {"results": [{"id": i, "skip": i == "hn-12", "cat": "none" if i == "hn-12" else "tip",
                             "headline": "h", "tag": "t", "why": "", "pick": 1, "replaces": ""} for i in ids_in(kw)]}

    main.run(NOW, tmp_path, FakeClient(judge), sources=[("hn", Good)])
    mem = json.loads((tmp_path / "skipped.json").read_text(encoding="utf-8"))
    assert list(mem) == ["hn-12"]
    client = FakeClient()
    main.run(NOW + timedelta(hours=1), tmp_path, client, sources=[("hn", Good)])
    assert client.calls == []                                # 건너뛴 글을 다시 보내지 않음
    client = FakeClient(judge)
    main.run(NOW + timedelta(days=9), tmp_path, client, sources=[("hn", Good)])
    assert ids_in(client.calls[0]) == ["hn-12"]              # 8일 지나면 기억에서 빠짐
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert "skipped" not in st


def test_second_run_skips_known(tmp_path):
    main.run(NOW, tmp_path, FakeClient(label_all()), sources=[("hn", Good)])
    client = FakeClient()
    st = main.run(NOW + timedelta(hours=1), tmp_path, client, sources=[("hn", Good)])
    assert client.calls == [] and st["run"]["added"] == 0
