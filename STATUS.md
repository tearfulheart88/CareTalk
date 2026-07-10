# 돌봄톡 개발 현황

최종 점검: 2026-07-10

## 현재 상태

- 공식 `FastMCP` 기반 stateless Streamable HTTP 서버
- endpoint `/mcp`, health endpoint `/health`
- Tool 8개 등록
- 직접 함수 E2E 97개 회귀 케이스 통과
- 공식 MCP Python 클라이언트로 initialize, tools/list, tools/call 통과
- Mock/규칙 모드와 OpenAI 실모드 폴백 경로 분리
- PlayMCP in KC용 Dockerfile 준비

## 이번 최종 점검 반영

1. 직접 구현한 HTTP JSON-RPC 서버를 공식 FastMCP로 교체했습니다.
2. `MOCK_MODE`, `MCP_PORT`/`PORT`, `CARETALK_DB_PATH`가 실제 실행에 반영되도록 수정했습니다.
3. OpenAI 장애나 모델 오판이 명시적 RED 신호를 낮추지 못하도록 안전 하한을 추가했습니다.
4. 실제 신고·출동·알림톡 발송을 하지 않았는데 완료로 오인시키는 문구를 제거했습니다.
5. 부정 회상 대화의 공감 응답을 결정적으로 만들어 테스트 변동을 없앴습니다.
6. NaN, 무한대, 명백한 단위 오류와 과도하게 긴 입력을 저장 전에 차단했습니다.
7. LLM 사용 여부를 `analysis_source`와 `mock_mode`에 실제 경로대로 표시합니다.
8. OpenAI timeout, 1회 재시도, 환경변수 모델 설정을 적용했습니다.
9. 컨테이너를 non-root 사용자로 실행하고 healthcheck를 추가했습니다.
10. README의 미구현 암호화·자동 발송 주장을 제거하고 연동 범위를 정확히 구분했습니다.

## 검증 명령

```powershell
python -m compileall -q .
python _e2e_test.py
python server.py --mock --host 127.0.0.1 --port 9000
```

## 외부 준비가 필요한 항목

- 실제 OpenAI 경로: `OPENAI_API_KEY`
- 카카오 로그인: 앱 설정과 Redirect URI, 사용자 동의
- 실제 알림톡: 비즈채널, 발신프로필, 템플릿 승인, 공급사 API
- 실제 건강시설 검색: 공공데이터 API 교체
- 운영 개인정보: 인증·권한·암호화·보존/삭제·동의 정책
- PlayMCP in KC 빌드 및 최종 등록
