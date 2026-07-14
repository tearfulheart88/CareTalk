# CareTalk / 돌봄톡

독거 어르신의 안부 확인, 건강 기록, 응급 신호 감지, 정서 지원과 가족 리포트를 제공하는
한국어 MCP 서버입니다. AGENTIC PLAYER 10 출품을 위한 검증 가능한 프로토타입입니다.

> **AI가 사람을 대신하는 돌봄이 아니라, 당사자의 선택을 사람의 돌봄으로 연결합니다.**

심사위원용 대표 질문과 구현 경계는 [SUBMISSION.md](SUBMISSION.md)에 30초 분량으로 정리했습니다.

## 대표 경험

처음 실행하면 어르신에게 서비스 목적과 자동 신고 여부를 짧고 큰 문장으로 설명하고,
`오늘은 괜찮아요`, `밥 먹었어요`, `조금 아파요`, `도움이 필요해요` 같은 선택 버튼을 먼저 보여줍니다.
글을 길게 쓰지 않아도 버튼 한 번으로 안부와 다음 확인을 이어갈 수 있습니다.

가족이 안부 시간·응답 여유·확인 역할·접근성 요구를 말하면 돌봄톡은 먼저 동의가 필요한
안전계획 초안을 만듭니다. 안부 확인, 자연어 건강 기록, 위급 신호, 가족 요약을 함께 검토할 수
있으며 실제 신고나 발송을 했다고 오인시키지 않습니다.

## 구현 상태

- 공식 MCP Python SDK의 `FastMCP` 사용
- stateless Streamable HTTP + JSON response
- MCP endpoint: `/mcp`
- health endpoint: `/health`
- Mock/규칙 모드 전체 기능 동작
- OpenAI 키 설정 시 감정 분석, 응급 맥락 확인, 회상 대화, 리포트 요약에 LLM 사용
- 키가 있어도 명시적 live opt-in 없이는 OpenAI 네트워크 호출 차단
- SQLite 일일 한도, 분당·동시 호출 제한, 2.5초 타임아웃 상한 적용
- PlayMCP 필수 Tool annotations 5개를 10개 Tool에 모두 명시
- SQLite 기반 체크인·건강 기록·리포트 연결
- 카카오 응답 포맷용 Widget A/B JSON 생성
- 직접 함수 E2E와 실제 MCP 클라이언트 핸드셰이크 검증

## MCP Tools

| Tool | 기능 | 주요 action |
|---|---|---|
| `care_guide` | 어르신·가족 첫 안내, 추천 답변, 접근성, FAQ, 개인정보 | `start`, `examples`, `faq`, `accessibility`, `privacy` |
| `daily_checkin` | 안부 메시지 생성, 응답 분석, 무응답 확인 | `initiate`, `analyze`, `no_response` |
| `emergency_detect` | 위험 표현과 장기 무응답을 보수적으로 판정 | `detect`, `silence` |
| `family_report` | 일일·주간 가족 리포트 생성 | `daily`, `weekly` |
| `daily_care_widget` | 어르신용 카카오 응답 JSON 생성 | - |
| `health_log` | 건강 수치 기록·조회·추세 분석·자연어 파싱 | `log`, `query`, `analyze`, `parse` |
| `reminiscence_chat` | 감정별 회상 대화와 주제 추천 | `chat`, `suggest_topic` |
| `family_report_widget` | 가족용 카카오 리포트 JSON 생성 | - |
| `health_facility` | 내장 데모 데이터 기반 보건소·무료 프로그램 안내 | `search`, `programs`, `recommend`, `notify` |
| `build_care_safety_plan` | 동의·접근성·사람 확인 중심 돌봄 안전계획 | - |

## 안전 원칙

돌봄톡의 위험·건강 판정은 의료 진단이나 119 신고를 대체하지 않습니다.

- 호흡 곤란, 의식 소실, 구조 요청 같은 명시적 RED 신호는 LLM이 하향할 수 없습니다.
- OpenAI 호출 실패 시에도 규칙 기반 위험 등급을 유지합니다.
- RED 응답은 119 연락을 안내하지만 신고나 출동이 완료됐다고 표시하지 않습니다.
- `notify_targets`와 카카오 JSON은 발송 요청 데이터입니다. MCP Tool 자체가 알림톡을 보내지는 않습니다.
- 건강 수치는 명백한 오입력과 비유한수 값을 저장 전에 차단합니다.
- 한 문장에 혈압·혈당 등 여러 수치가 함께 있어도 이름이 명시된 값을 각각 기록합니다.
- 건강 범위는 참고용 자동 분류이며 증상과 개인별 목표치는 의료진 판단을 우선합니다.
- 혈압 140/90 수준의 단일 측정은 RED로 과장하지 않고 재측정을 안내하며, 180/120을 넘는 값과 위급 증상을 강하게 확인합니다.
- 체중은 키·평소 기준·질환 정보 없이 절대값으로 위험 판정하지 않고 변화 추세만 확인합니다.
- 안전계획은 전화번호·주소·이메일을 받지 않고 관계 역할만 사용하며, 동의 전에는 초안 상태로 유지합니다.

## 빠른 시작

Python 3.10 이상이 필요합니다.

```powershell
python -m pip install -r requirements.txt
python server.py --mock --host 127.0.0.1 --port 9000
```

확인 주소:

- 상태: `http://127.0.0.1:9000/health`
- MCP: `http://127.0.0.1:9000/mcp`

Mock 모드는 외부 API를 호출하지 않습니다. `.env.example`을 참고해 `.env`를 만들거나
배포 환경변수에 키를 설정하면 실제 OpenAI 경로를 사용할 수 있습니다.

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=2.2
MOCK_MODE=true
LIVE_API_ENABLED=false
CARETALK_DB_PATH=./db/caretalk.db
```

`MOCK_MODE=true` 또는 `--mock`이면 규칙 기반으로 실행합니다. `--live`만 명시했을 때
`MOCK_MODE=false`와 `LIVE_API_ENABLED=true`가 함께 적용됩니다. OpenAI 키가 없거나 호출이 실패하면 안전한 규칙/템플릿으로 폴백하며,
응답의 `analysis_source`와 `mock_mode`에 실제 사용 경로를 표시합니다.

### 공개 배포의 API 키 원칙

- GitHub, Dockerfile, 이미지, MCP 응답에는 키를 넣지 않습니다.
- 현재 PlayMCP in KC Git/컨테이너 등록 가이드에 Secret 주입 단계가 명시되어 있지 않으므로,
  해당 화면에서 바로 배포할 때는 기본 keyless 규칙 모드를 유지합니다.
- 실시간 시연은 Secret 환경변수를 지원하는 서버에 별도로 배포한 뒤 HTTPS `/mcp` 주소를 등록합니다.
- 개인 기본 키 대신 출품 전용 OpenAI 프로젝트 키를 만들고 사용 알림·한도·키 회전을 함께 운영합니다.
- 앱의 SQLite 일일 한도는 인스턴스 단위입니다. 여러 인스턴스 배포에는 공급자 측 제한이나 공유 저장소가 필요합니다.

기본 보호값은 OpenAI 100회/일, 10회/분, 동시 2회이며 `.env.example`에서 더 낮출 수 있습니다.

## 검증

```powershell
python _e2e_test.py
python -m compileall -q .
```

현재 E2E 검증은 172개이며, 실제 Streamable HTTP 클라이언트로 10개 Tool metadata,
대표 호출, 오류 응답의 MCP `isError`도 확인합니다.

실제 MCP 연결은 공식 Python SDK의 `streamable_http_client`로 다음 순서를 검증합니다.

1. `initialize`
2. `tools/list`에서 10개 Tool 확인
3. `tools/call` 대표 시나리오 호출
4. 오류 Tool의 MCP `isError` 확인

## API 연동 범위

| 연동 | 현재 상태 | 필요한 것 |
|---|---|---|
| OpenAI | MCP 실행 경로에 연결됨 | `OPENAI_API_KEY` |
| 카카오 로그인 | `services/kakao_auth.py` 어댑터 구현, MCP Tool에는 미연결 | 카카오 앱 설정·Redirect URI |
| 카카오 알림톡 | `services/alimtalk.py` 어댑터 구현, MCP Tool은 메시지 생성까지만 수행 | 비즈채널·발신프로필·템플릿 승인·공급사 API |
| 건강시설 | 현재 내장 데모 데이터 | 실서비스 전 공공데이터 API 교체 |

카카오 키가 없어도 MCP 등록과 Tool 테스트는 가능합니다. 실제 로그인 또는 알림톡 발송을
시연할 때만 카카오 키와 사전 승인이 필요합니다.

## 데이터와 개인정보

현재 SQLite 저장소는 로컬 프로토타입이며 암호화·사용자 인증이 적용된 운영 의료 시스템이
아닙니다. 공개 데모에는 실제 이름, 전화번호, 진료정보를 입력하지 마세요. 운영 전에는 카카오
사용자 인증, 접근 제어, 암호화 저장소, 보존기간·삭제 정책, 동의 기록이 추가로 필요합니다.

서버 로그에는 MCP Tool 인자를 기록하지 않습니다. `.env`, DB, 로그 파일은 Git과 Docker build
context에서 제외합니다.

## Docker / PlayMCP in KC

저장소: `https://github.com/tearfulheart88/CareTalk`

Git 소스 빌드 입력값:

- Branch/ref: `main`
- Dockerfile path: `Dockerfile`
- Endpoint path: `/mcp`

로컬 Docker 실행 예시:

```powershell
docker build -t caretalk .
docker run --rm -p 9000:9000 -e MOCK_MODE=true caretalk
```

컨테이너는 non-root 사용자로 실행되고 `/health`를 점검합니다. 배포 플랫폼이 `PORT`를
주입하면 그 값을 사용하며, 없으면 9000 포트를 사용합니다.

Secret 저장을 지원하는 별도 호스팅에서만 `MOCK_MODE=false`, `LIVE_API_ENABLED=true`와
`OPENAI_API_KEY`를 서버 환경변수로 설정하세요. 키를 Docker build argument로 전달하지 마세요.

## 구조

```text
server.py                 FastMCP 서버, Tool 등록과 디스패치
tools/                    체크인·응급·리포트·건강·회상·시설 기능
_widgets/                 카카오 응답 JSON 생성기
db/schema.py              SQLite 스키마 단일 진실원천
services/                 OpenAI/Kakao 연동 어댑터
services/usage_guard.py   API 일일·분당·동시 호출 및 비밀값 보호
_e2e_test.py              회귀·안전·입력 검증
Dockerfile                PlayMCP in KC 배포 이미지
```
