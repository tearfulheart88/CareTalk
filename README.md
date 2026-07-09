# 돌봄톡 (CareTalk) — 카카오톡 네이티브 독거노인 AI 돌봄 에이전트

> **"카톡 친구 추가만 하면 시작됩니다 — 말 한마디로 안부를 전하고, AI가 24시간 살핍니다"**
>
> AGENTIC PLAYER 10 공모전 출품작 | 버전: v2.1 (본선 Tool + Widget B 구현 완료)

---

## 📋 프로젝트 개요

**돌봄톡(CareTalk)** 은 카카오톡 채널 기반의 AI 돌봄 에이전트입니다.
독거노인 사용자가 별도 앱 설치나 회원가입 없이 카카오톡 채널 친구 추가만으로
매일 안부 확인, 건강 체크, 위험 감지, 정서 지원, 가족 연결 서비스를 이용할 수 있습니다.

### 핵심 가치

| 가치 | 설명 |
|------|------|
| **접근성** | 카카오톡 4,800만 MAU — 앱 설치율 0%, 학습 비용 0% |
| **통합성** | 안부·건강·정서·위험·가족연결 5대 기능을 하나의 대화창에서 |
| **정서 지원** | AI 기반 감정 분석·맞춤형 대화·추억 회상 (본선) |
| **가족 연결** | 알림톡 + Widget 대시보드로 자녀가 부모님 상태 실시간 확인 |
| **비용 효율** | IoT 장비(30~50만원/가구) 대비 소프트웨어 구독(월 5천~1만원) |

### 대상

- **1차 사용자**: 65세+ 독거노인 (2024년 기준 228.8만 가구)
- **2차 사용자**: 가족 구성원 (자녀), 복지사, 지자체 돌봄 담당자

---

## 🚀 빠른 시작

### 사전 요구사항

- **Python**: 3.10 이상 (검증 환경 3.10.6)
- **Mock 모드**: 외부 패키지·API 키 **불필요** — `python server.py --mock` 만으로 즉시 동작 ✅
- **실제 모드 API 키** (선택):
  - OpenAI API 키 (GPT-4o-mini) — 없으면 규칙기반 분석으로 자동 폴백
  - 카카오 REST API 키 (카카오 로그인)
  - 카카오 비즈메시지 API 키 (알림톡)

### 설치

```bash
# 1. 저장소 클론
git clone https://github.com/your-org/caretalk-mcp.git
cd caretalk-mcp

# 2. 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 의존성 설치
pip install -r requirements.txt
```

### 환경변수 설정

```bash
# .env.example 파일을 복사하여 .env 생성
cp .env.example .env

# .env 파일을 열어 실제 API 키 입력
# 필수 항목:
#   OPENAI_API_KEY=sk-...
#   KAKAO_REST_API_KEY=...
# 선택 항목 (본선):
#   KAKAO_BIZ_API_KEY=...
#   KAKAO_SENDER_KEY=...
```

### 실행

```bash
# 기본 실행 (포트 9000)
python server.py --port 9000

# Mock 모드 실행 (API 키 없이 테스트용)
python server.py --mock --port 9000

# 카카오클라우드 배포 모드
python server.py --port 9000 --host 0.0.0.0
```

---

## 🧰 MCP Tool 목록

돌봄톡은 JSON-RPC 2.0 표준을 준수하는 MCP 서버로, `tools/list` → `tools/call` 패턴으로 동작합니다.

### 예선 MVP (4개 Tool, 서버에 등록·검증 완료 ✅)

| Tool | 설명 | 입력 | 출력 |
|------|------|------|------|
| **`daily_checkin`** | 매일 안부 메시지 발송 + 응답 감정 분석 | `user_id`, `action`(initiate/analyze/no_response), `message`/`nickname`(선택) | `status`, `sentiment`, `health_keywords`, `follow_up_action` |
| **`emergency_detect`** | 위험 키워드 실시간 감지 + 긴급 레벨 판정 | `user_id`, `action`(detect/silence), `message`(선택) | `risk_level`, `detected_keywords`, `recommended_action`, `notify_targets` |
| **`family_report`** | 가족용 주간/일일 안부·건강 리포트 생성 | `senior_user_id`, `report_type`(weekly/daily) | `report_json`(BasicCard), `summary_text`, `alert_items` |
| **`daily_care_widget`** | 노인용 "오늘의 돌봄" Widget A 렌더 | `user_id`, `nickname`(선택), `sentiment`(선택) | 카카오 스킬 응답 v2.0 (SimpleText + quickReplies) |

### 본선 확장 (구현 완료 ✅ — E2E 58/58 통과)

| Tool | 설명 | Actions | 핵심 출력 |
|------|------|---------|-----------|
| **`health_log`** | 건강 데이터(혈압·혈당·체중·체온·맥박) 기록·추세 분석 | `log`(기록), `query`(조회), `analyze`(추세 분석), `parse`(자연어 파싱) | `status`(normal/warning/danger), `trend_alert`, `advice`, `normal_range` |
| **`reminiscence_chat`** | 추억 회상 기반 정서 지원 대화 (감정별 맞춤 주제 9종) | `chat`(대화 응답), `suggest_topic`(주제 추천) | `response_text`, `suggested_topic`, `suggested_media` |
| **`family_report_widget`** | 가족용 "주간 돌봄 리포트" Widget B 렌더 | — | 카카오 스킬 응답 v2.0 (BasicCard + ListCard) |

> **자연어 건강 기록**: "오늘 혈압 135/85요" → 수축기 135, 이완기 85 자동 추출·기록
> **감정 맞춤 회상**: positive/neutral/negative 3단계 감정에 따라 다른 회상 주제 추천
> **GPT 연동**: `reminiscence_chat`은 OPENAI_API_KEY 있으면 GPT-4o-mini 사용, 없으면 템플릿 폴백

### Tool 사용 예시

```json
// tools/call 예시 — daily_checkin
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "daily_checkin",
    "arguments": {
      "user_id": "senior_001",
      "message": "좋아요"
    }
  },
  "id": 1
}
```

```json
// tools/call 예시 — emergency_detect
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "emergency_detect",
    "arguments": {
      "user_id": "senior_001",
      "message": "아이고, 쓰러졌어. 너무 어지러워..."
    }
  },
  "id": 2
}
```

```json
// tools/call 예시 — health_log (자연어 파싱)
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "health_log",
    "arguments": {
      "user_id": "senior_001",
      "action": "parse",
      "message": "오늘 혈압 135/85요",
      "nickname": "순자"
    }
  },
  "id": 3
}
```

```json
// tools/call 예시 — reminiscence_chat (추억 회상 대화)
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "reminiscence_chat",
    "arguments": {
      "user_id": "senior_001",
      "action": "chat",
      "message": "옛날 고향 생각이 나네요",
      "sentiment": "positive",
      "nickname": "순자"
    }
  },
  "id": 4
}
```

---

## 🎨 Widget

### Widget A: 노인용 "오늘의 돌봄" (예선)

SimpleText + quickReplies 조합으로 매일 아침 안부 메시지 제공.

```json
{
  "version": "2.0",
  "template": {
    "outputs": [
      {
        "simpleText": {
          "text": "🌞 좋은 아침이에요, 순자님!\n\n오늘 기분: 😊 좋아요\n오늘 날씨: 맑음 ☀️, 24°C\n💡 산책하기 좋은 날이에요!\n\n어제까지 7일 연속 안부 확인 완료 ✅"
        }
      }
    ],
    "quickReplies": [
      {"label": "🩺 건강 체크", "action": "message", "messageText": "건강 체크할게요"},
      {"label": "📞 아들에게 전화", "action": "message", "messageText": "아들에게 전화"},
      {"label": "🍽 오늘 식단", "action": "message", "messageText": "오늘 식단 알려줘"}
    ]
  }
}
```

### Widget B: 가족용 "주간 돌봄 리포트" (본선, 구현 완료 ✅)

BasicCard + ListCard 조합으로 주간 건강·정서 데이터 대시보드 제공.

- **BasicCard**: 주간 요약 — 응답률, 감정 추이(긍정/보통/나쁨), 주요 건강 키워드, 위험 감지 이력(RED/YELLOW 건수)
- **ListCard**: 일별 상태 목록 — 최대 7일, 감정 이모지 + 메시지 요약
- **버튼**: 전화하기, 상세 리포트 보기

```json
{
  "version": "2.0",
  "template": {
    "outputs": [
      {
        "basicCard": {
          "title": "👵 순자님 주간 돌봄 리포트",
          "description": "기간: 2026-06-16 ~ 2026-06-23\n안부 응답률: 85.7% (7일 중)\n주간 기분: 😊 긍정적\n긍정 4회 / 보통 2회 / 나쁨 1회\n주요 건강 키워드: 혈압(3회), 수면(2회)\n⚠️ 주의 필요일: 1일"
        }
      },
      {
        "listCard": {
          "title": "📋 일별 상태 (순자님)",
          "items": [
            {"title": "06-23 😊 좋음", "description": "오늘 기분이 좋아요! 산책도 했어요"},
            {"title": "06-22 😐 보통", "description": "그저 그래요..."},
            {"title": "06-21 😔 나쁨 (주의)", "description": "무릎이 너무 아파요..."}
          ],
          "buttons": [
            {"label": "📞 전화하기", "action": "phone", "phoneNumber": "010-0000-0000"},
            {"label": "📋 상세 리포트", "action": "message", "messageText": "상세 리포트 보기"}
          ]
        }
      }
    ]
  }
}
```

---

## 📁 디렉토리 구조

```
caretalk_돌봄톡/
├── server.py                  # MCP 서버 엔트리포인트 (순수 stdlib http.server, JSON-RPC 2.0)
├── _e2e_test.py               # E2E 검증 스크립트 (58개 케이스, 직접 함수 호출)
├── requirements.txt           # Python 의존성 (실제 모드용, 선택)
├── .env.example               # 환경변수 템플릿
├── README.md                  # 프로젝트 문서 (현재 파일)
├── STATUS.md                  # 개발 인계 노트
│
├── _widgets/                  # Kakao Tools Widget 모듈
│   ├── __init__.py
│   ├── widget_a.py            # Widget A: 노인용 "오늘의 돌봄" (SimpleText + quickReplies)
│   └── widget_b.py            # Widget B: 가족용 "주간 돌봄 리포트" (BasicCard + ListCard)
│
├── services/                  # 외부 API 연동 서비스 모듈
│   ├── __init__.py
│   ├── kakao_auth.py          # 카카오 로그인 OAuth 2.0 (인가코드 → 토큰 → 사용자 정보)
│   ├── alimtalk.py            # 카카오 알림톡 발송 (비즈메시지 API)
│   └── gpt_service.py         # GPT-4o-mini API 연동 (감정 분석·건강 키워드, Mock 모드 지원)
│
├── tools/                     # MCP Tool 구현
│   ├── __init__.py
│   ├── daily_checkin.py       # Tool 1: 매일 안부 확인 (initiate/analyze/no_response)
│   ├── emergency_detect.py    # Tool 2: 위험 신호 감지 (detect/silence)
│   ├── family_report.py       # Tool 3: 가족용 주간/일일 리포트
│   ├── health_log.py          # Tool 4: 건강 데이터 기록·추세 분석 (log/query/analyze/parse) [본선]
│   └── reminiscence_chat.py   # Tool 5: 추억 회상 정서 지원 대화 (chat/suggest_topic) [본선]
│
└── db/                        # 데이터베이스 (스키마 단일 진실원천)
    ├── __init__.py
    ├── schema.py              # ensure_schema() — 전 테이블 정의 + CRUD 헬퍼
    └── caretalk.db            # SQLite 데이터베이스 (런타임 생성)
```

> **스키마 단일 진실원천**: 모든 테이블 정의는 `db/schema.py`의 `ensure_schema(db_path)` 한 곳에만 있습니다.
> tool들은 각자 테이블을 만들지 않고 이 함수를 호출하므로, 스키마가 어긋날 일이 없습니다.
> 서버는 기동 시 `ensure_schema`를 호출해 DB를 자동 준비합니다(`--init-db` 불필요).

---

## 🔧 카카오클라우드 배포 가이드

### 1. 카카오클라우드 MCP Endpoint 생성

1. [카카오클라우드 콘솔](https://console.kakao.com/) 접속
2. **MCP Endpoint** 서비스 선택 → 새 엔드포인트 생성
3. 공모전용 무료 인스턴스 2대 할당 (예선 기간 내)
4. Python 3.11+ 런타임 선택

### 2. 서버 배포

```bash
# 카카오클라우드 인스턴스에 SSH 접속 후
cd /opt/caretalk-mcp

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
export OPENAI_API_KEY=sk-...
export KAKAO_REST_API_KEY=...
export KAKAO_BIZ_API_KEY=...
export KAKAO_SENDER_KEY=...

# 서버 실행 (백그라운드)
nohup python server.py --port 9000 --host 0.0.0.0 > server.log 2>&1 &
```

### 3. PlayMCP 등록

1. [PlayMCP](https://playmcp.com/) 접속 → MCP 서버 등록
2. 엔드포인트 URL: `https://your-instance.kakao.com:9000`
3. Tool 목록 자동 검색 (`tools/list`) 확인
4. 심사 요청 → 전체 공개 설정

### 4. 상태 확인

```bash
# 서버 상태 확인
curl https://your-instance.kakao.com:9000/health

# Tool 목록 확인
curl -X POST https://your-instance.kakao.com:9000/jsonrpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

---

## 📊 기술 스택

| 계층 | 기술 | 비고 |
|------|------|------|
| **MCP 서버** | Python 표준 라이브러리 `http.server` | JSON-RPC 2.0, 외부 의존성 0 |
| **AI** | GPT-4o-mini (OpenAI) · Mock 모드 내장 | 감정 분석·건강 키워드 추출·위험 컨텍스트 확인 (API 키 없으면 규칙기반 폴백) |
| **데이터** | SQLite | 경량, 카카오클라우드 내장 |
| **인증** | 카카오 로그인 REST API | OAuth 2.0 |
| **알림** | 카카오 알림톡 API | 비즈메시지 |
| **Widget** | Kakao Tools JSON v2.0 | SimpleText, BasicCard, ListCard |
| **배포** | 카카오클라우드 | 공모전용 2대 무료 |

---

## 💰 예상 비용 (MVP 50명 기준)

| 항목 | 월 예상 비용 |
|------|-------------|
| GPT-4o-mini API | 약 $30~50 (4.5~7.5만원) |
| 알림톡 API | 약 2,000~4,000원 |
| 카카오클라우드 | 무료 (공모전용) |
| **합계** | **약 5~8만원/월** |

---

## 📅 구현 로드맵

| 주차 | 목표 | 주요 태스크 |
|------|------|------------|
| **1주차** (6/15~21) | MCP 서버 기본 구조 + daily_checkin | FastMCP 서버 구현, GPT-4o-mini 연동, SQLite 스키마 |
| **2주차** (6/22~28) | emergency_detect + 카카오 로그인 | 위험 키워드 감지, 알림톡 연동, OAuth 2.0 |
| **3주차** (6/29~7/5) | family_report + Widget A | 주간 집계, BasicCard JSON, Widget 구현 |
| **4주차** (7/6~12) | 통합 테스트 + 심사 제출 | 전체 연동 테스트, PlayMCP 등록, 비즈폼 접수 |

---

## 🔒 개인정보 보호

- 건강 데이터 AES-256 암호화 (SQLite)
- 가족 접근 동의 기반 (카카오 로그인 scope)
- 데이터 최소 수집 (닉네임·응답 텍스트만)
- 카카오 OAuth 2.0 표준 인증

---

## 📜 라이선스

AGENTIC PLAYER 10 공모전 출품작. 본선 진출 시 오픈소스 라이선스 검토 예정.

---

## 👥 기여

돌봄톡은 AGENTIC PLAYER 10 공모전을 위한 프로젝트입니다.
공모전 기간 중에는 내부 개발팀이 주도하며, 본선 진출 후 외부 기여를 환영합니다.

---

**Made with ❤️ for AGENTIC PLAYER 10**
