"""실제 네트워크로 각 소스를 한 번씩 불러 건수만 출력한다 (저장·LLM 호출 없음)."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.main import SOURCES  # noqa: E402
from collector.sources.base import Context  # noqa: E402

ctx = Context(now=datetime.now(timezone.utc), search_api_key=os.environ.get("SEARCH_API_KEY"))
for name, mod in SOURCES:
    try:
        items = mod.fetch(ctx)
        sample = items[0]["title"][:60] if items else "-"
        print(f"{name:10} {len(items):3}건  예: {sample}")
    except Exception as e:
        print(f"{name:10} 실패: {e}")
