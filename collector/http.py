"""표준 라이브러리 HTTP 도우미 — 타임아웃, 지수 백오프 재시도."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

UA = "ai-trend-collector/1.0 (+https://github.com/js87ahnx/ai-trend-collector)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


def get(url: str, *, headers: dict | None = None, timeout: float = 15, retries: int = 3, sleep=time.sleep) -> bytes:
    hdrs = {"User-Agent": UA, "Accept-Encoding": "identity"}
    hdrs.update(headers or {})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == retries - 1:
                raise FetchError(f"HTTP {e.code} {url}") from e
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries - 1:
                raise FetchError(f"{type(e).__name__} {url}: {e}") from e
            wait = delay
        sleep(min(wait, 30))
        delay *= 2
    raise FetchError(f"재시도 소진 {url}")


def get_json(url: str, **kw):
    return json.loads(get(url, **kw))


def get_text(url: str, **kw) -> str:
    return get(url, **kw).decode("utf-8", errors="replace")
