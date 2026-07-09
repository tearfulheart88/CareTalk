# 돌봄톡(CareTalk) 개발 현황 (STATUS)

> 매 작업 시작 시 먼저 읽고, 작업 끝에 갱신. 새 폴더 만들지 말고 이 루트에서만 작업.
> 최종 갱신: 2026-07-04 (Tool 8종: health_facility 신규 + 건강 입력 경로 source + E2E 87개)

## ✅ 현재 상태: 본선 MVP 동작 완료 + 제출물 정리 완료 + 안전 판정 개선

**Tool 8종** 구현 완료 (예선 4 + 본선 3 + 확장 1).
E2E 87/87 통과 (mock 모드, 회귀 포함). 서버 JSON-RPC 정상 응답 확인.
기획서·데모시나리오 작성 완료.

### 2026-07-04 확장: 무료 건강 서비스 안내 + 입력 연동 구조

1. **신규 Tool 8: `health_facility`** (tools/health_facility.py)
   - `search`: 지역별 보건소·치매안심센터 검색 (데모용 내장 샘플 12곳 —
     실서비스는 공공데이터포털 '전국 보건소 표준데이터' API 연동 예정)
   - `programs`: 어르신 무료 건강 프로그램 7종 (국가건강검진, 독감/폐렴구균 무료접종,
     혈압·혈당 무료측정, 치매 조기검진, 고혈압·당뇨 등록관리 등) + 시즌 표시
   - `recommend`: 최근 14일 health_logs 이상 수치 기반 맞춤 추천
     (혈압 이상 → 고혈압·당뇨 등록관리 + 지역 보건소)
   - `notify`: 카카오 v2.0 알림 메시지 JSON 생성 (독감 시즌 10~11월 자동 강조)
2. **건강 입력 경로(source) 구조**: health_logs에 `source` 컬럼 추가
   (manual=직접 입력 / device=혈압계 등 기기 연동 / ocr=측정기 사진 판독).
   MCP inputSchema에 노출 — 기기/OCR 연동 시 파이프라인 수정 없이 수용 가능.
   기존 DB는 ensure_schema의 ALTER TABLE 마이그레이션으로 자동 반영.
3. **health_log ↔ health_facility 연계**: warning/danger 수치 기록 시 응답에
   `facility_tip`(보건소 무료 측정 안내) 자동 포함.
4. E2E 13개 추가 (74→87). `_demo_local.html`에 ⑧ 무료 건강 서비스 데모 버튼 추가.

### 2026-07-02 안전 판정 버그 수정 (코드 리뷰 후속)

1. **감정 부정어 처리**: "몸이 안 좋아요"가 '좋아' 키워드에 걸려 positive로 오판되던 버그 수정.
   `NEGATED_POSITIVE_PATTERNS` 선처리 도입 (daily_checkin + gpt_service mock 동일 적용).
2. **응급 RED 오탐 축소**: "혼자 살려고"(살려), "구조조정"(구조), "응급실 다녀왔어"(응급),
   단독 "의식" 등 부분 문자열 오탐 제거 — 증상/요청 표현 단위로만 매칭.
3. **응급 RED 과소판정 방지**: 컨텍스트 하향을 2그룹으로 분리.
   과거/타인 이야기는 RED→YELLOW 하향 허용, 안심 표현("괜찮아")은 YELLOW→NONE만.
   "숨이 안 쉬어져... 괜찮아지겠지"는 RED 유지 (괜찮아지겠지는 안심 표현으로 안 봄).
4. **시간대 통일 (UTC→localtime)**: SQLite `CURRENT_TIMESTAMP`(UTC)와 `datetime.now()`(KST)
   혼용으로 무응답 경보가 9시간 일찍 발령되던 버그 수정. 스키마 기본값을
   `datetime('now','localtime')`으로 통일, 조회 쿼리도 localtime 기준.
   ※ 기존 `db/caretalk.db`는 구 스키마라 삭제 후 재생성됨.
5. **건강 danger → 가족 연결**: 위험 수치(예: 혈압 185) 기록 시 alerts 테이블 기록 +
   주간 리포트/BasicCard/Widget B에 "건강 수치 이상 N건" 표시.
6. **응답률 날짜 기준 집계**: 하루 2회 initiate 시 응답률이 50%로 왜곡되던 것 수정
   (get_checkin_stats·family_report 모두 DISTINCT 날짜 기준).
7. **응답-체크인 매칭 당일 제한**: 오늘 응답이 지난주 무응답 체크인을 소급 '응답됨'
   처리하던 것 수정.
8. **서버 개선**: `ThreadingHTTPServer` 전환(블로킹 방지), MCP 표준 `isError` 플래그 추가.
9. 위 전부 `_e2e_test.py` 회귀 테스트 16개 추가 (58→74개).

### 2026-06-27 추가 개선

1. `server.py --mock`에서 `family_report`가 GPT 요약 경로를 불필요하게 호출하지 않도록 `mock=MOCK_MODE` 전달.
2. `health_log` 자연어 혈압 파싱 개선: `135/85`뿐 아니라 `135 85`, `135에 85` 형태도 수축기/이완기로 기록.
3. `server.py` Tool 디스패처의 필수 인자 검증 강화: `user_id`/`senior_user_id` 누락 시 내부 오류 대신 한국어 error 반환.
4. 주간 리포트/Widget B의 기간 계산을 오늘 포함 정확히 N일로 정리.
5. 위 개선을 `_e2e_test.py` 회귀 테스트에 추가.

### 2026-06-24 안정화 작업

1. `health_log` 판정 기준을 기획서와 맞춤: 혈압 135/85는 warning, 수축기 140은 danger, 혈당 180은 warning.
2. `emergency_detect`에서 "못 일어나" 등 기립 불가 낙상 신호를 RED로 판정.
3. `daily_checkin` 응답 저장과 `get_checkin_stats()` 응답률 집계를 일치시켜 응답 후 response_rate가 100%로 계산되게 수정.
4. `_e2e_test.py` stdout/stderr를 UTF-8로 재설정해 Windows 기본 콘솔에서도 바로 실행 가능하게 수정.

### 등록 Tool 8종 (전부 구현·검증 완료)

| # | Tool | 파일 | 상태 |
|---|------|------|------|
| 1 | `daily_checkin` | tools/daily_checkin.py | ✅ 예선 |
| 2 | `emergency_detect` | tools/emergency_detect.py | ✅ 예선 |
| 3 | `family_report` | tools/family_report.py | ✅ 예선 |
| 4 | `daily_care_widget` | _widgets/widget_a.py | ✅ 예선 (Widget A) |
| 5 | `health_log` | tools/health_log.py | ✅ 본선 |
| 6 | `reminiscence_chat` | tools/reminiscence_chat.py | ✅ 본선 |
| 7 | `family_report_widget` | _widgets/widget_b.py | ✅ 본선 (Widget B) |
| 8 | `health_facility` | tools/health_facility.py | ✅ 확장 (무료 건강 서비스) |

### 이번 세션 완료 작업

1. **서버 JSON-RPC 검증**: `python server.py --mock --port 9000` 기동 → 7개 Tool tools/list + 10개 tools/call 전부 200 OK (Python urllib로 한글 인코딩 이슈 우회하여 검증)
2. **기획서 작성**: `기획서.md` — 11섹션 종합 기획서 (문제정의, 타겟사용자, 핵심기능, 아키텍처, 사용시나리오, 차별화, 로드맵, 검증결과, 제출물, 발전방향)
3. **데모시나리오 작성**: `데모시나리오.md` — 심사위원이 12단계로 따라할 수 있는 단계별 가이드 (Python 코드 포함)

### 제출물 파일 목록

| 파일 | 내용 | 크기 |
|------|------|------|
| `기획서.md` | 공모전 제출용 종합 기획서 (11섹션) | ~16KB |
| `데모시나리오.md` | 심사위원용 12단계 데모 가이드 | ~14KB |
| `README.md` | 기술 문서 (설치, 실행, API) | ~15KB |
| `server.py` | MCP 서버 엔트리포인트 | ~15KB |
| `_e2e_test.py` | 87개 E2E 검증 스크립트 | — |
| `.env.example` | 환경변수 템플릿 | ~2KB |
| `requirements.txt` | Python 의존성 | — |

### 구조 (검증된 동작 경로)
- `server.py` — 순수 stdlib `http.server` 기반 MCP JSON-RPC 2.0 서버. 기동 시 `ensure_schema`로 DB 자동준비.
  - `execute_tool()` 디스패처가 8개 Tool 전부 라우팅.
  - 포트 8000번대 사용 금지 로직 포함 (9000번대 권장).
  - 엔드포인트: `/mcp` (POST), `/` (GET 서버 정보)
- `db/schema.py` — **모든 테이블 정의의 단일 진실원천** `ensure_schema(db_path)` + CRUD 헬퍼.
  - 테이블: users, checkins, checkin_responses, emergency_logs, silence_alerts, family_reports, alerts, health_logs
- `tools/` — daily_checkin, emergency_detect, family_report, health_log, reminiscence_chat, health_facility
- `services/` — alimtalk(알림톡), gpt_service(GPT, mock 내장), kakao_auth(OAuth)
- `_widgets/` — widget_a(노인용 "오늘의 돌봄"), widget_b(가족용 "주간 돌봄 리포트")
- `_e2e_test.py` — 87개 케이스 직접 함수 호출 E2E 검증 스크립트

### 검증 방법 (재현)
```bash
# 1) E2E 전체 검증 (87개 케이스)
python _e2e_test.py
#    → 결과: ✅ 87개 통과 / ❌ 0개 실패

# 2) 서버 기동 + JSON-RPC 검증
python server.py --mock --port 9000 --host 127.0.0.1
#    → GET / : 서버 정보 JSON
#    → POST /mcp tools/list : 8개 Tool 정상 반환
#    → POST /mcp tools/call : 각 Tool 정상 응답 (Python urllib로 한글 테스트)

# 3) 각 모듈 단독 (mock)
python -m db.schema
python tools/daily_checkin.py --mock
python tools/emergency_detect.py --mock
python tools/family_report.py --mock
python tools/health_log.py
python tools/reminiscence_chat.py
python _widgets/widget_a.py
python _widgets/widget_b.py
```
※ 이전 테스트 DB는 `_db_backup/`에 백업됨. `db/caretalk.db`는 런타임 재생성됨.
※ curl은 git-bash 환경에서 한글 인코딩 문제가 있으니 Python urllib로 테스트 권장.

## 다음 할 일 (배포/제출)
1. **실제 GPT 모드 검증**: 대표님 OPENAI_API_KEY 넣고 `python server.py`(비 mock)로 GPT 경로 확인
2. **카카오 비즈니스 채널 개설**: 대표님 직접 진행 → 알림톡 템플릿 승인 → `services/alimtalk.py` 실연동
3. **카카오클라우드 MCP Endpoint 배포**: 인스턴스 생성 → 서버 배포 → PlayMCP 등록
4. **공모전 제출**: 기획서.md + README.md + 데모시나리오.md + 소스코드 패키지

## 규칙 리마인더
- 소스는 write_file로만. .venv를 폴더 안에 만들지 말 것. 새 폴더 난립 금지(이 루트에서만).
- 테이블이 필요하면 `db/schema.py:SCHEMA_SQL`에만 추가하고 `ensure_schema`를 호출할 것 (tool에서 따로 CREATE TABLE 금지).
- 한 세션에 다 하려 하지 말고 단계로 쪼갤 것.
