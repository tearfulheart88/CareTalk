#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
돌봄톡 E2E 직접 함수 호출 검증 (서버 기동 불필요)
서버의 execute_tool 함수를 직접 import하여 모든 tool을 검증한다.
DB 충돌을 피하기 위해 임시 DB 경로를 사용한다.
"""
import asyncio, os, sys, json, tempfile, shutil

# Windows 기본 콘솔(cp949)에서도 이모지/한글 테스트 로그가 깨지지 않게 한다.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# 프로젝트 루트
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# 임시 DB 사용
tmp_dir = tempfile.mkdtemp(prefix="caretalk_test_")
DB_PATH = os.path.join(tmp_dir, "test.db")

# server.py의 execute_tool 사용 (mock 모드)
from server import execute_tool, set_mock_mode, DB_PATH as SERVER_DB
import server
server.DB_PATH = DB_PATH  # server 모듈의 DB 경로를 임시로 교체
set_mock_mode(True)

# schema 초기화
from db.schema import ensure_schema
ensure_schema(DB_PATH)

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")

print("=" * 60)
print("돌봄톡 E2E 검증 (직접 함수 호출, mock 모드)")
print(f"DB: {DB_PATH}")
print("=" * 60)

# === 1. initialize / tools/list ===
print("\n[1] 서버 정보")
from server import TOOL_DEFINITIONS
test("Tool 개수 9개", len(TOOL_DEFINITIONS) == 9, f"got {len(TOOL_DEFINITIONS)}")
tool_names = [t["name"] for t in TOOL_DEFINITIONS]
test("필수 Tool 포함", all(n in tool_names for n in [
    "daily_checkin", "emergency_detect", "family_report",
    "daily_care_widget", "health_log", "reminiscence_chat", "family_report_widget",
    "health_facility", "build_care_safety_plan"
]), f"got {tool_names}")
registered_tools = asyncio.run(server.mcp.list_tools())
test("공식 FastMCP Tool 9개 등록", len(registered_tools) == 9, str([t.name for t in registered_tools]))
for registered in registered_tools:
    annotations = registered.annotations
    test(f"{registered.name} annotations 있음", annotations is not None)
    test(f"{registered.name} annotations.title 있음", bool(annotations and annotations.title))
    test(
        f"{registered.name} annotations 5개 완성",
        annotations is not None
        and annotations.readOnlyHint is not None
        and annotations.destructiveHint is not None
        and annotations.idempotentHint is not None
        and annotations.openWorldHint is not None,
        str(annotations),
    )
    test(
        f"{registered.name} 영문 설명에 서비스명 포함",
        "CareTalk(돌봄톡)" in (registered.description or ""),
        registered.description or "",
    )

# === 2. daily_checkin ===
print("\n[2] daily_checkin")
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"initiate","nickname":"순자"})
test("initiate status=initiated", r.get("status") == "initiated", str(r)[:100])
test("initiate greeting 있음", bool(r.get("greeting")), str(r)[:100])
test("initiate quick_replies 3개", len(r.get("quick_replies",[])) == 3, str(r)[:100])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"오늘 기분이 좋아요! 산책도 했어요"})
test("analyze(positive) sentiment=positive", r.get("sentiment") == "positive", str(r)[:100])
test("analyze(positive) status=normal", r.get("status") == "normal", str(r)[:100])
from db.schema import get_checkin_stats
stats = get_checkin_stats("senior_001", days=1, db_path=DB_PATH)
test("analyze 후 응답률 100%", stats.get("response_rate") == 100.0, str(stats)[:100])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"무릎이 너무 아파요. 어지러워서 쓰러질 것 같아요"})
test("analyze(danger) sentiment=negative", r.get("sentiment") == "negative", str(r)[:100])
test("analyze(danger) 위험키워드 감지", len(r.get("danger_keywords_detected",[])) > 0, str(r)[:100])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"no_response"})
test("no_response 응답 있음", "status" in r, str(r)[:100])

# === 3. emergency_detect ===
print("\n[3] emergency_detect")
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"쓰러졌어요. 숨이 안 쉬어져요. 살려주세요"})
test("detect(RED) risk=red", r.get("risk_level") == "red", str(r)[:100])
test("detect(RED) 119 안내", r.get("emergency_contact") == "119", str(r)[:100])
test("detect(RED) 출동 완료로 오인시키지 않음", r.get("dispatch_performed") is False, str(r)[:100])

r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"아이고 방금 넘어졌어 너무 아파서 못 일어나겠어"})
test("detect(fall cannot get up) risk=red", r.get("risk_level") == "red", str(r)[:100])

r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"어제 TV에서 누가 쓰러졌다는 뉴스를 봤어요"})
test("detect(safe) context_safe=True", r.get("context_safe") == True, str(r)[:100])
test("detect(safe) 위험 하향", r.get("risk_level") in ["yellow","none"], str(r)[:100])

r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"오늘 날씨 좋네요"})
test("detect(normal) risk=none", r.get("risk_level") == "none", str(r)[:100])

r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"silence"})
test("silence 응답 있음", "risk_level" in r, str(r)[:100])

# === 4. family_report ===
print("\n[4] family_report")
r = execute_tool("family_report", {"senior_user_id":"senior_001","report_type":"weekly"})
test("weekly report_json 있음", "report_json" in r, str(r)[:100])
test("weekly summary_text 있음", bool(r.get("summary_text")), str(r)[:100])
test("weekly alert_items 있음", "alert_items" in r, str(r)[:100])
test("weekly mock_mode=True", r.get("mock_mode") == True, str(r)[:100])

r = execute_tool("family_report", {"senior_user_id":"senior_001","report_type":"daily"})
test("daily summary_line 있음", bool(r.get("summary_line")), str(r)[:100])

# === 5. daily_care_widget ===
print("\n[5] daily_care_widget (Widget A)")
r = execute_tool("daily_care_widget", {"user_id":"senior_001","nickname":"순자","sentiment":"positive"})
outputs = r.get("template",{}).get("outputs",[])
test("Widget A version=2.0", r.get("version") == "2.0", str(r)[:100])
test("Widget A simpleText 있음", len(outputs) > 0 and "simpleText" in outputs[0], str(r)[:100])
test("Widget A quickReplies 3개", len(r.get("template",{}).get("quickReplies",[])) == 3, str(r)[:100])

# === 6. health_log ===
print("\n[6] health_log")
r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"systolic","value":110,"nickname":"순자"})
test("log(110) status=normal", r.get("status") == "normal", str(r)[:100])
test("log(110) label=수축기 혈압", r.get("label") == "수축기 혈압", str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"systolic","value":185,"nickname":"순자"})
test("log(185) status=danger", r.get("status") == "danger", str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"blood_sugar","value":95,"nickname":"순자"})
test("log(혈당95) status=normal", r.get("status") == "normal", str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"parse","message":"오늘 혈압 135/85요","nickname":"순자"})
test("parse(135/85) parsed 있음", "parsed" in r and r.get("parsed"), str(r)[:100])
test("parse(135/85) systolic=135", r.get("parsed",{}).get("systolic") == 135.0, str(r)[:100])
test("parse(135/85) diastolic=85", r.get("parsed",{}).get("diastolic") == 85.0, str(r)[:100])
test("parse(135/85) systolic warning", r.get("results",[{}])[0].get("status") == "warning", str(r)[:100])
test("parse(135/85) diastolic warning", r.get("results",[{},{}])[1].get("status") == "warning", str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"parse","message":"오늘 혈압 135 85요","nickname":"순자"})
test("parse(135 85) systolic=135", r.get("parsed",{}).get("systolic") == 135.0, str(r)[:100])
test("parse(135 85) diastolic=85", r.get("parsed",{}).get("diastolic") == 85.0, str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"systolic","value":140,"nickname":"순자"})
test("log(140) 단일 측정은 warning", r.get("status") == "warning", str(r)[:100])
test("혈압 재측정 맥락 안내", "두 번" in r.get("measurement_context", ""), str(r)[:160])

r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"weight","value":160,"nickname":"순자"})
test(
    "체중 절대값은 위험 판정하지 않음",
    r.get("status") == "recorded" and "변화 추세" in r.get("normal_range", "") and not r.get("reference_basis"),
    str(r)[:160],
)

execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"weight","value":140,"nickname":"순자"})
r = execute_tool("health_log", {"user_id":"senior_001","action":"analyze","data_type":"weight","days":14})
weight_pattern = next((item for item in r.get("patterns", []) if item.get("data_type") == "weight"), {})
test("체중은 절대값 대신 변화 추세 분석", weight_pattern.get("pattern") == "meaningful_change", str(r)[:160])

r = execute_tool("health_log", {"user_id":"senior_001","action":"parse","message":"혈당 180이에요","nickname":"순자"})
results = r.get("results",[])
if results:
    test("parse(혈당180) status=warning", results[0].get("status") == "warning", str(r)[:100])
else:
    test("parse(혈당180) 결과 있음", False, str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"query","days":7})
test("query total>0", r.get("total_count",0) > 0, str(r)[:100])
test("query type_counts 있음", len(r.get("type_counts",{})) > 0, str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"analyze","days":14})
test("analyze overall_status 있음", "overall_status" in r, str(r)[:100])
test("analyze recommendation 있음", bool(r.get("recommendation")), str(r)[:100])

# === 7. reminiscence_chat ===
print("\n[7] reminiscence_chat")
r = execute_tool("reminiscence_chat", {"user_id":"senior_001","action":"chat","message":"옛날 고향 생각이 나네요","sentiment":"positive","nickname":"순자"})
test("chat(+) response_text 있음", bool(r.get("response_text")), str(r)[:100])
test("chat(+) suggested_topic 있음", bool(r.get("suggested_topic")), str(r)[:100])
test("chat(+) mock_mode=True", r.get("mock_mode") == True, str(r)[:100])

r = execute_tool("reminiscence_chat", {"user_id":"senior_001","action":"chat","message":"외롭고 슬퍼요. 남편이 보고 싶어요","sentiment":"negative","nickname":"순자"})
test("chat(-) response_text 있음", bool(r.get("response_text")), str(r)[:100])
test("chat(-) 부정 감정에 공감", "힘드" in r.get("response_text","") or "감사" in r.get("response_text",""), r.get("response_text","")[:80])

r = execute_tool("reminiscence_chat", {"user_id":"senior_001","action":"suggest_topic","sentiment":"neutral","nickname":"순자"})
test("suggest_topic 있음", bool(r.get("topic")), str(r)[:100])
test("suggest_topic prompt 있음", bool(r.get("prompt")), str(r)[:100])

# === 8. family_report_widget (Widget B) ===
print("\n[8] family_report_widget (Widget B)")
r = execute_tool("family_report_widget", {"user_id":"senior_001","nickname":"순자","days":7})
outputs = r.get("template",{}).get("outputs",[])
test("Widget B version=2.0", r.get("version") == "2.0", str(r)[:100])
test("Widget B outputs 2개 (BasicCard+ListCard)", len(outputs) == 2, str(r)[:100])
test("Widget B basicCard 있음", len(outputs) > 0 and "basicCard" in outputs[0], str(r)[:100])
test("Widget B listCard 있음", len(outputs) > 1 and "listCard" in outputs[1], str(r)[:100])

# === 8-1. build_care_safety_plan ===
print("\n[8-1] build_care_safety_plan")
r = execute_tool("build_care_safety_plan", {
    "user_id": "senior_001",
    "nickname": "순자",
    "checkin_time": "09:00",
    "response_window_hours": 2,
    "contact_roles": "딸, 복지사",
    "accessibility_needs": "글씨가 작으면 읽기 어려움",
    "senior_consented": False,
})
test("동의 전 안전계획은 draft", r.get("status") == "draft_requires_senior_consent", str(r)[:120])
test("단계적 사람 확인 4단계", len(r.get("escalation_steps", [])) == 4, str(r)[:120])
test("큰 글씨 접근성 반영", any("큰 글씨" in item for item in r.get("accessibility_design", [])), str(r)[:160])
test("119 자동신고 없음 명시", "119 신고를 수행하지 않습니다" in r.get("limitations", ""), str(r)[:160])
test("안전계획 카드 2개", len(r.get("kakao_cards", [])) == 2, str(r)[:120])

phone_result = execute_tool("build_care_safety_plan", {
    "user_id": "senior_001",
    "contact_roles": "딸 010-1234-5678",
})
address_result = execute_tool("build_care_safety_plan", {
    "user_id": "senior_001",
    "contact_roles": "딸 서울시 행복로 12",
})
test(
    "연락처·주소 입력 차단",
    "전화번호" in phone_result.get("error", "") and "개인정보" in address_result.get("error", ""),
    f"phone={phone_result}, address={address_result}"[:180],
)

r = execute_tool("build_care_safety_plan", {
    "user_id": "senior_001",
    "senior_consented": True,
})
test("동의 표시 후에도 사람 검토 상태", r.get("status") == "ready_for_human_review", str(r)[:120])

# === 9. 에러 처리 ===
print("\n[9] 에러 처리")
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"initiate"})
test("initiate nickname 누락 → 에러", "error" in r, str(r)[:100])

r = execute_tool("daily_checkin", {"action":"initiate","nickname":"순자"})
test("user_id 누락 → 에러", "error" in r, str(r)[:100])

r = execute_tool("unknown_tool", {})
test("알 수 없는 Tool → 에러", "error" in r, str(r)[:100])

r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"systolic"})
test("log value 누락 → 에러", "error" in r, str(r)[:100])

# === 10. 회귀: 감정 부정어 처리 ===
print("\n[10] 회귀: 감정 부정어 처리")
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"몸이 안 좋아요"})
test("'몸이 안 좋아요' → negative", r.get("sentiment") == "negative", str(r)[:100])
test("'몸이 안 좋아요' → concern", r.get("status") == "concern", str(r)[:100])
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"기분이 안 좋아"})
test("'기분이 안 좋아' → negative", r.get("sentiment") == "negative", str(r)[:100])
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"잘 지내요, 기분 좋아요"})
test("'기분 좋아요' → positive 유지", r.get("sentiment") == "positive", str(r)[:100])

# === 11. 회귀: 응급 감지 오탐/과소판정 ===
print("\n[11] 회귀: 응급 감지 오탐/과소판정")
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"혼자 살려고 이사왔어"})
test("'혼자 살려고' → none (오탐 방지)", r.get("risk_level") == "none", str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"아들이 회사에서 구조조정 당했대"})
test("'구조조정' → none (오탐 방지)", r.get("risk_level") == "none", str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"어제 응급실 다녀왔어"})
test("'어제 응급실 다녀왔어' → none (과거)", r.get("risk_level") == "none", str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"숨이 안 쉬어져... 그래도 괜찮아지겠지"})
test("'숨이 안 쉬어져+괜찮아지겠지' → red 유지", r.get("risk_level") == "red", str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"살려주세요 가슴이 너무 아파요"})
test("'살려주세요' → red 유지", r.get("risk_level") == "red", str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"숨을 못 쉬겠어요"})
test("'숨을 못 쉬겠어요' → red", r.get("risk_level") == "red", str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect","message":"어지러워도 괜찮아 걱정 마"})
test("'어지러워+괜찮아' → none (안심 표현 YELLOW 하향)", r.get("risk_level") == "none", str(r)[:100])

# === 12. 회귀: 시간대 (무응답 경보) ===
print("\n[12] 회귀: 시간대(UTC/로컬) 무응답 계산")
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"silence"})
test("방금 응답 직후 hours_silent < 2", r.get("hours_silent", 99) < 2, str(r)[:100])
test("방금 응답 직후 risk=none", r.get("risk_level") == "none", str(r)[:100])

# === 13. 회귀: 건강 danger → 가족 알림 연결 ===
print("\n[13] 회귀: 건강 danger → alerts/리포트 연결")
from db.schema import get_alerts_by_user
alerts = get_alerts_by_user("senior_001", days=1, db_path=DB_PATH)
test("danger 수치 → alerts 기록됨", len(alerts) > 0, f"alerts={len(alerts)}")
r = execute_tool("family_report", {"senior_user_id":"senior_001","report_type":"weekly"})
health_ab = r.get("aggregated_data",{}).get("health_abnormal_events",[])
test("주간 리포트에 건강 이상 수치 포함", len(health_ab) > 0, str(r.get("aggregated_data",{}))[:100])
card_desc = r["report_json"]["template"]["outputs"][0]["basicCard"]["description"]
test("BasicCard에 건강 이상 표시", "건강 수치 이상" in card_desc, card_desc[:120])

# === 14. 회귀: 응답률 날짜 기준 ===
print("\n[14] 회귀: 응답률 날짜 기준")
# 같은 날 initiate를 한 번 더 해도 (총 2회) 날짜 기준이라 응답률이 50%로 떨어지면 안 됨
execute_tool("daily_checkin", {"user_id":"senior_002","action":"initiate","nickname":"영감"})
execute_tool("daily_checkin", {"user_id":"senior_002","action":"initiate","nickname":"영감"})
execute_tool("daily_checkin", {"user_id":"senior_002","action":"analyze","message":"좋아요"})
stats2 = get_checkin_stats("senior_002", days=1, db_path=DB_PATH)
test("하루 2회 initiate+1응답 → 응답률 100%", stats2.get("response_rate") == 100.0, str(stats2)[:100])

# === 15. health_facility (무료 건강 서비스 안내) ===
print("\n[15] health_facility")
r = execute_tool("health_facility", {"user_id":"senior_001","action":"search","region":"마포"})
test("search(마포) 결과 있음", r.get("count",0) > 0, str(r)[:100])
test("search(마포) 무료 서비스 포함", any("무료" in s for f in r.get("facilities",[]) for s in f.get("free_services",[])), str(r)[:100])

r = execute_tool("health_facility", {"user_id":"senior_001","action":"search","region":"마포","facility_type":"치매안심센터"})
test("search(마포+치매안심센터) 타입 필터", all(f["type"]=="치매안심센터" for f in r.get("facilities",[])) and r.get("count",0) > 0, str(r)[:100])

r = execute_tool("health_facility", {"user_id":"senior_001","action":"programs"})
test("programs 목록 있음", r.get("count",0) >= 5, str(r)[:100])
test("programs 독감접종 포함", any("독감" in p["name"] for p in r.get("programs",[])), str(r)[:100])

# senior_001은 위에서 혈압 185(danger) 기록됨 → 고혈압 관리 추천이 나와야 함
r = execute_tool("health_facility", {"user_id":"senior_001","action":"recommend","region":"마포"})
test("recommend 이상 수치 기반", "systolic" in r.get("based_on",[]), str(r)[:100])
test("recommend 고혈압 프로그램", any("고혈압" in rec["recommended_program"] for rec in r.get("recommendations",[])), str(r)[:100])
test("recommend 지역 시설 포함", len(r.get("facilities",[])) > 0, str(r)[:100])

r = execute_tool("health_facility", {"user_id":"senior_001","action":"notify","nickname":"순자","region":"마포"})
test("notify message_json v2.0", r.get("message_json",{}).get("version") == "2.0", str(r)[:100])
test("notify 텍스트에 무료 안내", "무료" in r.get("text",""), str(r)[:100])

r = execute_tool("health_facility", {"action":"search"})
test("user_id 누락 → 에러", "error" in r, str(r)[:100])

# === 16. health_log 입력 경로(source) ===
print("\n[16] health_log 입력 경로(source)")
r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"systolic","value":118,"source":"device"})
test("기기 연동 입력 source=device", r.get("source") == "device", str(r)[:100])
r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"systolic","value":150})
test("이상 수치 → 보건소 안내(facility_tip)", "보건소" in r.get("facility_tip",""), str(r)[:100])

# === 17. 회귀: 배포/안전 하드닝 ===
print("\n[17] 회귀: 배포/안전 하드닝")
old_key = os.environ.pop("OPENAI_API_KEY", None)
set_mock_mode(False)
r = execute_tool("emergency_detect", {"user_id":"senior_safe","action":"detect","message":"숨을 못 쉬겠어요 살려주세요"})
test("OpenAI 키 없음에도 RED 유지", r.get("risk_level") == "red", str(r)[:140])
test("OpenAI 키 없음은 rules 폴백 표시", r.get("analysis_source") == "rules" and r.get("mock_mode") is True, str(r)[:140])

import tools.emergency_detect as emergency_module
original_context_check = emergency_module._gpt_context_check
emergency_module._gpt_context_check = lambda message, context=None: {
    "is_real_emergency": False,
    "adjusted_level": "none",
    "explanation": "테스트용 하향 응답",
    "analysis_available": True,
}
r = execute_tool("emergency_detect", {"user_id":"senior_safe","action":"detect","message":"의식을 잃고 숨을 못 쉬어요"})
test("LLM이 내려도 명시적 RED 안전 하한 유지", r.get("risk_level") == "red", str(r)[:140])
emergency_module._gpt_context_check = original_context_check
set_mock_mode(True)
if old_key is not None:
    os.environ["OPENAI_API_KEY"] = old_key

r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"temperature","value":float("nan")})
test("건강 수치 NaN 저장 차단", "error" in r, str(r)[:100])
r = execute_tool("health_log", {"user_id":"senior_001","action":"log","data_type":"temperature","value":100})
test("건강 수치 명백한 단위 오류 차단", "error" in r, str(r)[:100])
r = execute_tool("emergency_detect", {"user_id":"senior_001","action":"detect"})
test("응급 detect 메시지 누락 차단", "error" in r, str(r)[:100])
r = execute_tool("daily_checkin", {"user_id":"x" * 129,"action":"no_response"})
test("과도하게 긴 사용자 ID 차단", "error" in r, str(r)[:100])

# === 18. 회귀: 공개 API 사용량·비밀값 보호 ===
print("\n[18] 회귀: 공개 API 사용량·비밀값 보호")
from services import usage_guard

guard_keys = (
    "MOCK_MODE", "LIVE_API_ENABLED", "CARETALK_USAGE_DB_PATH",
    "OPENAI_DAILY_LIMIT", "OPENAI_RATE_LIMIT_PER_MINUTE",
    "OPENAI_MAX_CONCURRENCY", "OPENAI_TIMEOUT_SECONDS", "OPENAI_API_KEY",
)
guard_original = {key: os.environ.get(key) for key in guard_keys}
try:
    set_mock_mode(False)
    os.environ.pop("LIVE_API_ENABLED", None)
    os.environ["OPENAI_API_KEY"] = "test-openai-secret"
    test("키만 있어서는 실시간 API 비활성", usage_guard.live_api_enabled() is False)

    import tools.emergency_detect as emergency_guard_module
    fallback = emergency_guard_module._gpt_context_check("숨을 못 쉬겠어요")
    test("opt-in 없으면 네트워크 전 규칙 폴백", fallback.get("analysis_available") is False, str(fallback))
    from services.gpt_service import GPTService
    legacy_fallback = GPTService(mock_mode=False).analyze_sentiment("기분이 좋아요")
    test("기존 GPTService도 opt-in 없으면 규칙 폴백", legacy_fallback.get("sentiment") == "positive", str(legacy_fallback))

    os.environ["LIVE_API_ENABLED"] = "true"
    os.environ["CARETALK_USAGE_DB_PATH"] = os.path.join(tmp_dir, "usage.db")
    os.environ["OPENAI_DAILY_LIMIT"] = "2"
    os.environ["OPENAI_RATE_LIMIT_PER_MINUTE"] = "100"
    os.environ["OPENAI_MAX_CONCURRENCY"] = "1"
    usage_guard.reset_usage_guard()

    first = usage_guard.reserve_openai_call()
    second_while_busy = usage_guard.reserve_openai_call()
    test("OpenAI 첫 호출 슬롯 허용", first is None, str(first))
    test("OpenAI 동시 호출 1회 초과 차단", second_while_busy is not None, str(second_while_busy))
    usage_guard.release_openai_call()

    second = usage_guard.reserve_openai_call()
    usage_guard.release_openai_call()
    third = usage_guard.reserve_openai_call()
    test("OpenAI 일일 2회까지 허용", second is None, str(second))
    test("OpenAI 일일 3회차 차단", third is not None and "한도" in third, str(third))

    usage_guard.reset_usage_guard()
    os.environ["OPENAI_DAILY_LIMIT"] = "10"
    os.environ["OPENAI_RATE_LIMIT_PER_MINUTE"] = "1"
    minute_first = usage_guard.reserve_openai_call()
    usage_guard.release_openai_call()
    minute_second = usage_guard.reserve_openai_call()
    test(
        "OpenAI 분당 1회 초과 차단",
        minute_first is None and minute_second is not None and "1분" in minute_second,
        str(minute_second),
    )

    os.environ["OPENAI_TIMEOUT_SECONDS"] = "99"
    test("OpenAI 타임아웃 2.5초 상한", usage_guard.openai_timeout() == 2.5)
    secret_text = usage_guard.redact_secrets(
        "https://example.test?a=1&api_key=test-openai-secret Authorization: Bearer bearer-secret"
    )
    test(
        "API 키와 Bearer 토큰 마스킹",
        "test-openai-secret" not in secret_text and "bearer-secret" not in secret_text,
        secret_text,
    )
finally:
    usage_guard.release_openai_call()
    usage_guard.reset_usage_guard()
    for key, value in guard_original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    set_mock_mode(True)

# === 19. 정리 ===
print("\n" + "=" * 60)
print(f"결과: ✅ {passed}개 통과 / ❌ {failed}개 실패")
print("=" * 60)

# 임시 DB 정리
try:
    shutil.rmtree(tmp_dir)
except:
    pass

sys.exit(0 if failed == 0 else 1)
