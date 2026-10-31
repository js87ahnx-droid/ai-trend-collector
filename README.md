# AI 동향 수집함

국내외 커뮤니티의 AI 트렌드·활용 팁을 매시간 수집해 테마별로 묶어 보여주는 정적 대시보드.

- 화면: GitHub Pages (`web/index.html` + `data/*.json`)
- 수집: GitHub Actions `37 * * * *` → `python -m collector.main` → `data/` 커밋 → Pages 재배포

## 설정

저장소 **Settings → Secrets and variables → Actions** 에 등록:

| 이름 | 용도 | 없으면 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 분류·헤드라인·원문 읽고 상세 작성 (기본 모델 `claude-opus-5-5`) | 수집만 하고 저장 안 함 (`last_run`만 갱신) |
| `SEARCH_API_KEY` | Brave Search API — Reddit·Threads·웹 보완 | 웹검색 단계 건너뜀 |

모델 교체: 워크플로 `env`에 `ANTHROPIC_MODEL`을 추가.

## 로컬 실행

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
.venv/Scripts/python scripts/live_check.py        # 소스별 실제 수집 건수만 확인 (저장·LLM 없음)
.venv/Scripts/python -m collector.main            # 전체 1회차 (DATA_DIR로 출력 위치 변경 가능)
.venv/Scripts/python scripts/build_site.py && .venv/Scripts/python -m http.server -d _site
```

## 수집 소스

| 접두어 | 방법 |
|---|---|
| `gn` / `gnp` | 긱뉴스 RSS `news.hada.io/rss/news`. 작성자 `neo`(GN⁺ 봇) 글을 `gnp`로 분류. `/new`, `/plus` HTML은 403이라 쓰지 않음 |
| `hn` | Hacker News 공식 API, topstories 상위 30 |
| `gpters` | 게시판 목록(`/c/`)은 robots.txt 차단 → 공간별 `sitemap.xml`의 최근 48시간 글 최대 12건, 게시글 og 메타(제목·설명·게시일)만 읽음 |
| `reddit` / `threads` / `web` | Brave Search `freshness=pd`, 회차당 6회 호출. 제목·스니펫·URL·게시일만 사용, 원문 미방문 |

## 운영 체크리스트 대응

| 항목 | 위치 |
|---|---|
| 소스별 실패 격리 | `collector/main.py` 수집 루프 try/except, GPTERS는 공간별로도 격리 |
| 소스별 건수·실패 사유 로그 | Actions 로그 + `data/status.json`의 `sources`, 화면 하단 "수집 상태" |
| LLM 1~2회 / 검색 5~8회 | 분류 1회 + 테마 요약 1회(새 글 있을 때만), `websearch.PER_RUN` 합계 6 |
| 문서 수 상한 | 회차당 15건(`store.MAX_PER_RUN`), 7일 보관(`store.RETENTION`) |
| 원문 복사 금지 | 분류 프롬프트에 직접 요약 지시, 저장 필드에 본문 없음 |
| 요청 한도 | `collector/http.py` 지수 백오프(429·5xx, `Retry-After` 존중), Brave 호출 간 1.1초 |
| 2시간 정지 시 "지연됨" | `web/index.html` `LATE_MS` |

공개 저장소의 예약 워크플로는 60일간 저장소 활동이 없으면 GitHub가 멈춥니다. 매시 데이터 커밋이 활동으로 잡히지만, 멈추면 Actions 탭에서 다시 켭니다.
