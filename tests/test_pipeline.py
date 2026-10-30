import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from collector import classify, main, store
from collector.sources.base import raw

NOW = datetime(2026, 10, 30, 12, 37, tzinfo=timezone.utc)


class FakeClient:
    """client.beta.messages.create 흉내. 요청을 기록하고 준비된 JSON을 차례로 돌려준다."""

    def __init__(self, *payloads, stop_reason="end_turn"):
        self.payloads = list(payloads)
        self.calls = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        p = self.payloads.pop(0)
        text = json.dumps(p(kw) if callable(p) else p, ensure_ascii=False)
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


def r(id_, title, source="hn", **kw):
    return raw(id=id_, title=title, url=f"https://ex.com/{id_}", source=source, **kw)


def test_prefilter_general_sources_need_ai_words_and_ads_dropped():
    cands = [
        r("hn-1", "New open-weights LLM tops benchmark"),
        r("hn-2", "Rust 2.0 released"),
        r("gn-3", "클로드로 회의록 자동화", source="gn"),
        r("gpters-4", "주간 회고", source="gpters"),
        r("gpters-5", "ChatGPT 강의 오픈! 얼리버드 할인", source="gpters"),
    ]
    assert [c["id"] for c in classify.prefilter(cands)] == ["hn-1", "gn-3", "gpters-4"]


def test_classify_drops_skip_unknown_and_bad_theme():
    cands = [r("hn-1", "a"), r("hn-2", "b"), r("hn-3", "c")]
    client = FakeClient({"results": [
        {"id": "hn-1", "skip": False, "theme": "model", "gist": "새 모델 공개"},
        {"id": "hn-2", "skip": True, "theme": "none", "gist": ""},
        {"id": "hn-9", "skip": False, "theme": "tip", "gist": "없는 id"},
        {"id": "hn-3", "skip": False, "theme": "none", "gist": "x"},
        {"id": "hn-1", "skip": False, "theme": "tip", "gist": "중복"},
    ]})
    out = classify.classify(cands, client)
    assert [(o["id"], o["theme"]) for o in out] == [("hn-1", "model")]
    req = client.calls[0]
    assert req["model"] == classify.MODEL and req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "hn-3" in req["messages"][0]["content"]


def test_classify_sends_stored_titles_for_cross_run_dedupe():
    client = FakeClient({"results": []})
    classify.classify([r("hn-1", "a")], client, ["We ran AI agent swarms in Minecraft"])
    prompt = client.calls[0]["messages"][0]["content"]
    assert "- We ran AI agent swarms in Minecraft" in prompt and "같은 소식" in prompt
    classify.classify([r("hn-1", "a")], FakeClient({"results": []}))   # 저장 글 없어도 동작


def test_run_passes_recent_titles(tmp_path):
    main.run(NOW, tmp_path, FakeClient(label_all, {"themes": []}), sources=[("hn", Good)])

    class More:
        @staticmethod
        def fetch(ctx):
            return [r("hn-20", "Another LLM release")]

    client = FakeClient(label_all, {"themes": []})
    main.run(NOW + timedelta(hours=1), tmp_path, client, sources=[("hn", More)])
    assert "- Claude agent memory tips" in client.calls[0]["messages"][0]["content"]


def test_refusal_raises():
    with pytest.raises(classify.LLMError):
        classify.classify([r("hn-1", "a")], FakeClient({"results": []}, stop_reason="refusal"))


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


def label_all(kw):
    ids = [json.loads(l)["id"] for l in kw["messages"][0]["content"].splitlines() if l.startswith("{")]
    return {"results": [{"id": i, "skip": False, "theme": "tip", "gist": f"{i} 요약"} for i in ids]}


def test_run_isolates_source_failure_and_writes_status(tmp_path, monkeypatch):
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    old = {"id": "hn-1", "title": "old", "url": "https://ex.com/old", "source": "hn", "theme": "model",
           "gist": "오래됨", "collected_at": store.iso(NOW - timedelta(days=8))}
    store.save_json(tmp_path / "items.json", {"items": [old]})
    client = FakeClient(label_all, {"themes": [{"key": "tip", "summary": "프롬프트 요령이 화제"},
                                               {"key": "code", "summary": "없는 테마"}]})
    st = main.run(NOW, tmp_path, client, sources=[("boom", Boom), ("hn", Good)])

    items = store.load_items(tmp_path / "items.json")
    assert sorted(i["id"] for i in items) == ["hn-10", "hn-12"]          # 8일 지난 글 삭제, 비AI 글 제외
    assert all(i["collected_at"] == st["last_run"] for i in items)
    assert "소스 다운" in st["sources"]["boom"]["error"]
    assert st["sources"]["hn"] == {"fetched": 3, "kept": 2}
    assert st["themes"] == [{"key": "tip", "summary": "프롬프트 요령이 화제"}]
    assert len(client.calls) == 2


def test_run_without_key_still_updates_last_run(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st = main.run(NOW, tmp_path, None, sources=[("hn", Good)])
    assert st["last_run"] == "2026-10-30T12:37:00Z"
    assert "ANTHROPIC_API_KEY" in st["run"]["note"]
    assert store.load_items(tmp_path / "items.json") == []


def test_second_run_skips_known_and_keeps_themes(tmp_path):
    main.run(NOW, tmp_path, FakeClient(label_all, {"themes": [{"key": "tip", "summary": "요약"}]}),
             sources=[("hn", Good)])
    client = FakeClient()
    st = main.run(NOW + timedelta(hours=1), tmp_path, client, sources=[("hn", Good)])
    assert client.calls == [] and st["run"]["added"] == 0
    assert st["themes"] == [{"key": "tip", "summary": "요약"}]
