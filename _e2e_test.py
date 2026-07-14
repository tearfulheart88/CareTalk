#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
돌봄톡 E2E 직접 함수 호출 검증 (서버 기동 불필요)
서버의 execute_tool 함수를 직접 import하여 모든 tool을 검증한다.
DB 충돌을 피하기 위해 임시 DB 경로를 사용한다.
"""
import asyncio, os, sys, json, tempfile, shutil, sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
test("Tool 개수 12개", len(TOOL_DEFINITIONS) == 12, f"got {len(TOOL_DEFINITIONS)}")
tool_names = [t["name"] for t in TOOL_DEFINITIONS]
test("필수 Tool 포함", all(n in tool_names for n in [
    "care_guide", "care_circle", "care_routine", "daily_checkin", "emergency_detect", "family_report",
    "daily_care_widget", "health_log", "reminiscence_chat", "family_report_widget",
    "health_facility", "build_care_safety_plan"
]), f"got {tool_names}")
routine_schema = next(item for item in TOOL_DEFINITIONS if item["name"] == "care_routine")["inputSchema"]
routine_actions = routine_schema["properties"]["action"]["enum"]
test("care_routine 기기 연결 action 공개", all(action in routine_actions for action in ["create_device_pairing", "list_devices", "revoke_device"]), str(routine_actions))
registered_tools = asyncio.run(server.mcp.list_tools())
test("공식 FastMCP Tool 12개 등록", len(registered_tools) == 12, str([t.name for t in registered_tools]))
transport_security = server.mcp.settings.transport_security
test("MCP DNS rebinding 보호 활성", transport_security is not None and transport_security.enable_dns_rebinding_protection is True)
test(
    "PlayMCP 공개 Host 허용",
    transport_security is not None
    and "caretalk-mcp.playmcp-endpoint.kakaocloud.io" in transport_security.allowed_hosts,
)
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

# === 2. care_guide ===
print("\n[2] care_guide")
r = execute_tool("care_guide", {"action":"start","audience":"senior"})
test("guide source=built_in_guide", r.get("source") == "built_in_guide", str(r)[:100])
test("guide 원터치 답변 5개", len(r.get("quick_replies",[])) == 5, str(r)[:100])
test("guide 자동신고 안 함 명시", r.get("accessibility",{}).get("automatic_emergency_dispatch") is False, str(r)[:140])
test("guide Kakao message_json", len(r.get("message_json",{}).get("template",{}).get("quickReplies",[])) == 5, str(r)[:100])

r = execute_tool("care_guide", {"action":"faq","question":"119에 자동 신고하나요?"})
test("guide 질문별 FAQ", bool(r.get("kakao_cards")) and "자동" in r["kakao_cards"][0].get("description", ""), str(r)[:160])
r = execute_tool("care_guide", {"action":"faq","question":"웨어러블을 꼭 차야 하나요?"})
test("guide 웨어러블은 선택 기능", bool(r.get("kakao_cards")) and "웨어러블 없이도" in r["kakao_cards"][0].get("description", ""), str(r)[:180])

r = execute_tool("care_guide", {"action":"accessibility"})
test("guide 접근성 안내", r.get("accessibility",{}).get("large_text_recommended") is True, str(r)[:120])

# === 2-1. care_circle + care_routine ===
print("\n[2-1] 가족 연결망 + 예약 돌봄 + 휴대폰 활동")
circle_senior = "senior_circle_001"
circle_family = "family_circle_001"

r = execute_tool("care_circle", {
    "action":"create_invite", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "nickname":"순자", "senior_consented":False,
})
test("가족 연결은 어르신 동의 필수", "error" in r, str(r)[:140])

invite = execute_tool("care_circle", {
    "action":"create_invite", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "nickname":"순자", "circle_name":"순자님 돌봄",
    "senior_consented":True,
})
invite_code = invite.get("invite_code", "")
test("일회용 가족 초대 생성", invite.get("status") == "invite_created" and len(invite_code) >= 10, str(invite)[:180])
test("초대코드 평문 미저장 명시", invite.get("one_time_secret") is True and invite.get("stored_in_plaintext") is False, str(invite)[:160])

conn = sqlite3.connect(DB_PATH)
stored_hash = conn.execute("SELECT token_hash FROM care_invites LIMIT 1").fetchone()[0]
conn.close()
test("DB에 초대코드 해시만 저장", invite_code not in stored_hash and len(stored_hash) == 64, stored_hash)

r = execute_tool("care_circle", {
    "action":"join", "requester_user_id":circle_family,
    "invite_code":"WRONG-CODE", "nickname":"딸",
})
test("잘못된 초대코드 거절", "error" in r, str(r)[:120])

joined = execute_tool("care_circle", {
    "action":"join", "requester_user_id":circle_family,
    "invite_code":invite_code, "nickname":"딸",
})
test("지정 가족 계정 연결", joined.get("status") == "connected", str(joined)[:160])
test("가족 기본 권한 최소 부여", "view_summary" in joined.get("permissions",[]) and "manage_schedule" not in joined.get("permissions",[]), str(joined)[:160])

r = execute_tool("care_circle", {
    "action":"join", "requester_user_id":"another_family",
    "invite_code":invite_code, "nickname":"아들",
})
test("초대코드 재사용 차단", "error" in r, str(r)[:120])

circle = execute_tool("care_circle", {
    "action":"list", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
test("연결 가족 목록 조회", circle.get("connected_family_count") == 1 and circle.get("requester_role") == "family", str(circle)[:180])
test("목록에서 계정 ID 마스킹", all("account_hint" in m and "account_user_id" not in m for m in circle.get("members",[])), str(circle)[:180])

r = execute_tool("care_circle", {
    "action":"list", "requester_user_id":"stranger",
    "senior_user_id":circle_senior,
})
test("미연결 계정 조회 차단", "error" in r, str(r)[:120])

r = execute_tool("care_circle", {
    "action":"update_permissions", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "target_user_id":circle_family,
    "permissions":"view_summary,manage_schedule",
})
test("가족의 자기 권한 상승 차단", "error" in r, str(r)[:120])

r = execute_tool("care_routine", {
    "action":"configure", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "senior_consented":False,
})
test("예약·활동 확인도 별도 동의 필수", "error" in r, str(r)[:120])

demo_now = datetime.now(ZoneInfo("Asia/Seoul")).replace(second=0, microsecond=0)
demo_slot = demo_now.strftime("%H:%M")
configured = execute_tool("care_routine", {
    "action":"configure", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "prompt_times":demo_slot,
    "digest_times":demo_slot, "inactivity_hours":8, "escalation_hours":12,
    "inactivity_grace_minutes":30, "quiet_start":"00:00", "quiet_end":"00:00",
    "senior_consented":True,
})
test("예약 질문·가족 요약 설정", configured.get("status") == "configured" and configured.get("settings",{}).get("prompt_times") == [demo_slot], str(configured)[:180])
test("위치·화면·원시센서 미수집", all(configured.get("privacy",{}).get(k) is False for k in ["exact_location_collected","screen_content_collected","raw_motion_collected"]), str(configured)[:180])

r = execute_tool("care_routine", {
    "action":"configure", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "prompt_times":"25:00", "digest_times":demo_slot,
    "senior_consented":True,
})
test("잘못된 예약 시각 차단", "error" in r, str(r)[:120])
from tools.care_routine import _time_list
test("한 자리 예약 시각을 HH:MM으로 정규화", _time_list("9:05", minimum=1, maximum=6, field="prompt_times") == ["09:05"])

r = execute_tool("care_routine", {
    "action":"record_activity", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "event_type":"screen_unlock",
})
test("가족의 휴대폰 활동 위조 차단", "error" in r, str(r)[:120])
r = execute_tool("care_routine", {
    "action":"record_activity", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "event_type":"screen_unlock", "source":"phone",
})
test("MCP에서 인증 없는 실제 기기 신호 위조 차단", "error" in r and "/device/activity" in r.get("error", ""), str(r)[:160])

past_activity = (demo_now - timedelta(hours=9)).isoformat()
screen = execute_tool("care_routine", {
    "action":"record_activity", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "event_type":"screen_unlock",
    "source":"demo", "occurred_at":past_activity, "event_id":"screen-event-1",
})
motion = execute_tool("care_routine", {
    "action":"record_activity", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "event_type":"device_motion",
    "source":"demo", "occurred_at":past_activity, "event_id":"motion-event-1",
})
test("화면 사용·휴대폰 이동 시각 기록", screen.get("status") == "recorded" and motion.get("status") == "recorded", str((screen,motion))[:180])
test("활동 이벤트에 위치·원시센서 없음", screen.get("exact_location_collected") is False and motion.get("raw_sensor_data_collected") is False, str((screen,motion))[:160])

duplicate = execute_tool("care_routine", {
    "action":"record_activity", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "event_type":"screen_unlock",
    "source":"demo", "occurred_at":past_activity, "event_id":"screen-event-1",
})
test("활동 이벤트 중복 수집 방지", duplicate.get("status") == "duplicate_ignored", str(duplicate)[:120])

first_tick = execute_tool("care_routine", {
    "action":"run_due", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "now":demo_now.isoformat(),
})
first_types = [event.get("event_type") for event in first_tick.get("queued_events",[])]
test("예약 안부 질문 대기열 생성", "scheduled_checkin" in first_types, str(first_tick)[:220])
test("권한 있는 가족 요약 대기열 생성", "family_digest" in first_types, str(first_tick)[:220])
test("활동 부재 시 어르신 먼저 확인", first_tick.get("inactivity_status") == "senior_confirmation_queued" and "activity_check" in first_types, str(first_tick)[:220])
test("도구가 실제 메시지 발송으로 오인시키지 않음", first_tick.get("delivery_performed") is False and first_tick.get("delivery_mode") == "persistent_outbox", str(first_tick)[:180])

same_tick = execute_tool("care_routine", {
    "action":"run_due", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "now":demo_now.isoformat(),
})
test("같은 예약 실행 중복 생성 방지", same_tick.get("queued_count") == 0 and same_tick.get("inactivity_status") == "awaiting_senior_confirmation", str(same_tick)[:180])

later_tick = execute_tool("care_routine", {
    "action":"run_due", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "now":(demo_now + timedelta(minutes=31)).isoformat(),
})
later_types = [event.get("event_type") for event in later_tick.get("queued_events",[])]
test("유예시간 후 가족 활동 부재 안내", later_tick.get("inactivity_status") == "family_notice_queued" and later_types == ["inactivity_notice"], str(later_tick)[:220])
test("활동 부재를 응급 확정으로 표현하지 않음", "응급 상황으로 확정된 것은 아닙니다" in later_tick.get("queued_events",[{}])[0].get("preview",""), str(later_tick)[:220])

status = execute_tool("care_routine", {
    "action":"status", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "now":(demo_now + timedelta(minutes=31)).isoformat(),
})
test("가족용 상태에 요약·활동만 제공", status.get("status") == "active" and "user_message" not in status.get("today_summary",{}), str(status)[:220])
test("가족 상태에도 원문·정확한 위치 없음", status.get("privacy",{}).get("family_receives_raw_chat") is False and status.get("privacy",{}).get("exact_location_collected") is False, str(status)[:180])
test("중간 요약이 당일 질문·응답 수를 집계", status.get("today_summary",{}).get("scheduled_count") == 1 and status.get("today_summary",{}).get("responded_count") == 0 and status.get("today_summary",{}).get("raw_messages_shared") is False, str(status.get("today_summary"))[:220])

late_old_signal = execute_tool("care_routine", {
    "action":"record_activity", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "event_type":"app_open",
    "source":"demo", "occurred_at":past_activity, "event_id":"late-old-event",
})
status_after_late_signal = execute_tool("care_routine", {
    "action":"status", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
test("늦게 도착한 과거 신호는 현재 알림을 취소하지 않음", late_old_signal.get("status") == "recorded" and any(item.get("event_type") == "inactivity_notice" for item in status_after_late_signal.get("pending_notifications",[])), str(status_after_late_signal)[:220])

notice_id = later_tick.get("queued_events",[{}])[0].get("outbox_id", 0)
ack = execute_tool("care_routine", {
    "action":"acknowledge", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "outbox_id":notice_id,
    "response":"전화해볼게요",
})
test("가족의 전화 확인 응답 수취", ack.get("status") == "acknowledged" and ack.get("response") == "calling", str(ack)[:160])
status_after_ack = execute_tool("care_routine", {
    "action":"status", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
test("가족 후속 행동을 연결망에 기록", any(item.get("response") == "calling" for item in status_after_ack.get("recent_family_actions",[])), str(status_after_ack)[:220])
test("확인한 가족 알림은 대기 목록에서 제거", not any(item.get("event_type") == "inactivity_notice" for item in status_after_ack.get("pending_notifications",[])), str(status_after_ack)[:220])

# 안부 응답은 휴대폰 상호작용으로 간주되어 아직 발송되지 않은 활동 부재 안내를 취소한다.
execute_tool("daily_checkin", {"user_id":circle_senior,"action":"analyze","message":"저는 괜찮아요"})
execute_tool("daily_checkin", {"user_id":circle_senior,"action":"initiate","nickname":"순자"})
status_after_reply = execute_tool("care_routine", {
    "action":"status", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
pending_types = [item.get("event_type") for item in status_after_reply.get("pending_notifications",[])]
test("안부 응답 시 활동 부재 대기 알림 취소", "activity_check" not in pending_types and "inactivity_notice" not in pending_types, str(status_after_reply)[:220])
test("새 미응답이 있어도 앞선 응답을 하루 요약에 유지", status_after_reply.get("today_summary",{}).get("scheduled_count") == 2 and status_after_reply.get("today_summary",{}).get("responded_count") == 1 and status_after_reply.get("today_summary",{}).get("status") == "partial", str(status_after_reply.get("today_summary"))[:220])

schedule_grant = execute_tool("care_circle", {
    "action":"update_permissions", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "target_user_id":circle_family,
    "permissions":"view_summary,receive_inactivity_alerts,receive_emergency_alerts,manage_schedule",
})
test("어르신이 가족에게 일정 관리 권한 부여", schedule_grant.get("status") == "permissions_updated" and "manage_schedule" in schedule_grant.get("permissions",[]), str(schedule_grant)[:160])
family_schedule_update = execute_tool("care_routine", {
    "action":"configure", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "prompt_times":demo_slot,
    "digest_times":demo_slot, "inactivity_hours":2, "escalation_hours":3,
    "phone_activity_enabled":False, "wearable_enabled":True,
})
family_settings = family_schedule_update.get("settings", {})
test("가족 일정 권한은 활동·웨어러블 동의 설정을 바꾸지 않음", family_schedule_update.get("status") == "configured" and family_settings.get("inactivity_hours") == 8 and family_settings.get("phone_activity_enabled") is True and family_settings.get("wearable_enabled") is False, str(family_schedule_update)[:220])

print("\n[2-2] 인증된 휴대폰·웨어러블 연결")
family_pair = execute_tool("care_routine", {
    "action":"create_device_pairing", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "device_type":"phone", "senior_consented":True,
})
test("가족 계정의 기기 연결 코드 생성 차단", "error" in family_pair, str(family_pair)[:160])
no_consent_pair = execute_tool("care_routine", {
    "action":"create_device_pairing", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "device_type":"phone", "senior_consented":False,
})
test("기기 연결도 어르신 명시적 동의 필수", "error" in no_consent_pair, str(no_consent_pair)[:160])
wearable_pair = execute_tool("care_routine", {
    "action":"create_device_pairing", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "device_type":"wearable", "senior_consented":True,
})
test("꺼진 웨어러블의 연결 코드 생성 차단", "error" in wearable_pair, str(wearable_pair)[:160])

phone_pair = execute_tool("care_routine", {
    "action":"create_device_pairing", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "device_type":"phone", "device_label":"순자님 휴대폰",
    "pairing_minutes":10, "senior_consented":True,
})
pairing_code = phone_pair.get("pairing_code", "")
test("일회용 휴대폰 연결 코드 생성", phone_pair.get("status") == "pairing_created" and len(pairing_code) == 12, str(phone_pair)[:180])
conn = sqlite3.connect(DB_PATH)
pairing_hash = conn.execute(
    "SELECT code_hash FROM care_device_pairings WHERE senior_user_id = ? ORDER BY created_at DESC LIMIT 1",
    (circle_senior,),
).fetchone()[0]
conn.close()
test("기기 연결 코드도 해시만 저장", pairing_code not in pairing_hash and len(pairing_hash) == 64, pairing_hash)

from services.device_bridge import (
    DeviceBridgeError,
    exchange_device_pairing,
    ingest_device_activity,
    ingest_device_health,
)
linked_device = exchange_device_pairing(pairing_code, db_path=DB_PATH)
device_token = linked_device.get("device_token", "")
test("연결 코드로 기기 토큰 1회 발급", linked_device.get("status") == "paired" and device_token.startswith("ctd_") and linked_device.get("token_shown_once") is True, str(linked_device)[:180])
conn = sqlite3.connect(DB_PATH)
stored_device_hash = conn.execute(
    "SELECT token_hash FROM care_devices WHERE device_id = ?", (linked_device.get("device_id"),)
).fetchone()[0]
conn.close()
test("기기 토큰 평문 미저장", device_token not in stored_device_hash and len(stored_device_hash) == 64, stored_device_hash)
try:
    exchange_device_pairing(pairing_code, db_path=DB_PATH)
    pairing_reused = True
except DeviceBridgeError:
    pairing_reused = False
test("기기 연결 코드 재사용 차단", pairing_reused is False)

device_activity = ingest_device_activity(
    device_token, "screen_unlock", event_id="device-activity-1", db_path=DB_PATH
)
duplicate_device_activity = ingest_device_activity(
    device_token, "screen_unlock", event_id="device-activity-1", db_path=DB_PATH
)
test("인증된 기기의 최소 활동 신호 수취", device_activity.get("status") == "recorded" and device_activity.get("authenticated_device") is True, str(device_activity)[:180])
test("기기 활동 이벤트 중복 방지", duplicate_device_activity.get("status") == "duplicate_ignored", str(duplicate_device_activity)[:140])
test("기기 활동에도 위치·화면 내용 미수집", device_activity.get("exact_location_collected") is False and device_activity.get("screen_content_collected") is False, str(device_activity)[:160])
try:
    ingest_device_activity("ctd_" + "x" * 44, "screen_unlock", event_id="invalid", db_path=DB_PATH)
    invalid_token_rejected = False
except DeviceBridgeError as exc:
    invalid_token_rejected = exc.status_code == 401
test("잘못된 기기 토큰 차단", invalid_token_rejected)

device_health = ingest_device_health(
    device_token, "device-health-1", "heart_rate", 72, db_path=DB_PATH
)
duplicate_device_health = ingest_device_health(
    device_token, "device-health-1", "heart_rate", 72, db_path=DB_PATH
)
test("인증된 기기의 건강 수치 기록", device_health.get("status") == "normal" and device_health.get("source") == "device", str(device_health)[:180])
test("기기 건강 이벤트 중복 기록 방지", duplicate_device_health.get("status") == "duplicate_ignored", str(duplicate_device_health)[:140])

# 건강 로그 저장 직후 프로세스가 종료된 상황도 같은 event_id로 안전하게 복구한다.
from tools.health_log import log_health_data
recovery_event_id = f"{linked_device.get('device_id')}:device-health-recovery"
stale_received_at = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
conn = sqlite3.connect(DB_PATH)
conn.execute(
    """INSERT INTO device_health_events
       (event_id, device_id, senior_user_id, data_type, value, occurred_at, received_at)
       VALUES (?, ?, ?, 'heart_rate', 73, ?, ?)""",
    (
        recovery_event_id,
        linked_device.get("device_id"),
        circle_senior,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        stale_received_at,
    ),
)
conn.commit()
conn.close()
precrash_health = log_health_data(
    circle_senior,
    "heart_rate",
    73,
    db_path=DB_PATH,
    source="device",
    source_event_id=recovery_event_id,
)
conn = sqlite3.connect(DB_PATH)
health_count_before_recovery = conn.execute(
    "SELECT COUNT(*) FROM health_logs WHERE source_event_id = ?", (recovery_event_id,)
).fetchone()[0]
conn.close()
recovered_health = ingest_device_health(
    device_token, "device-health-recovery", "heart_rate", 73, db_path=DB_PATH
)
conn = sqlite3.connect(DB_PATH)
recovered_row = conn.execute(
    """SELECT health_log_id FROM device_health_events WHERE event_id = ?""",
    (recovery_event_id,),
).fetchone()
health_count_after_recovery = conn.execute(
    "SELECT COUNT(*) FROM health_logs WHERE source_event_id = ?", (recovery_event_id,)
).fetchone()[0]
conn.close()
test(
    "중단된 기기 건강 기록을 중복 없이 복구",
    health_count_before_recovery == 1
    and health_count_after_recovery == 1
    and recovered_health.get("log_id") == precrash_health.get("log_id")
    and recovered_row
    and recovered_row[0] == precrash_health.get("log_id"),
    str(recovered_health)[:180],
)
try:
    ingest_device_health(device_token, "device-health-nan", "heart_rate", float("nan"), db_path=DB_PATH)
    invalid_device_number_rejected = False
except DeviceBridgeError:
    invalid_device_number_rejected = True
test("기기 건강 수치 NaN 차단", invalid_device_number_rejected)

device_list = execute_tool("care_routine", {
    "action":"list_devices", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior,
})
test("어르신이 연결 기기 목록 조회", device_list.get("active_count") == 1 and device_list.get("tokens_returned") is False, str(device_list)[:180])
family_device_list = execute_tool("care_routine", {
    "action":"list_devices", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
test("가족의 기기 목록 조회 차단", "error" in family_device_list, str(family_device_list)[:140])

r = execute_tool("care_routine", {
    "action":"pause", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
test("가족이 어르신 대신 활동 확인 동의를 철회하지 못함", "error" in r, str(r)[:120])
r = execute_tool("care_routine", {
    "action":"pause", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior,
})
test("어르신이 예약·활동 확인 즉시 중지", r.get("status") == "paused" and r.get("senior_consented") is False and r.get("phone_activity_enabled") is False, str(r)[:180])
test("돌봄 중지 시 연결 기기도 즉시 해제", r.get("active_devices_revoked") == 1, str(r)[:180])
try:
    ingest_device_activity(device_token, "screen_unlock", event_id="after-pause", db_path=DB_PATH)
    paused_device_rejected = False
except DeviceBridgeError as exc:
    paused_device_rejected = exc.status_code == 401
test("중지 후 기존 기기 토큰 차단", paused_device_rejected)
paused_status = execute_tool("care_routine", {
    "action":"status", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior,
})
test("중지 상태 조회도 paused·disabled로 표시", paused_status.get("status") == "paused" and paused_status.get("activity_state") == "disabled", str(paused_status)[:180])
r = execute_tool("care_routine", {
    "action":"run_due", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "now":demo_now.isoformat(),
})
test("중지 후 예약 실행 차단", "error" in r and "중지" in r.get("error",""), str(r)[:140])
r = execute_tool("care_routine", {
    "action":"configure", "requester_user_id":circle_family,
    "senior_user_id":circle_senior, "prompt_times":demo_slot,
    "digest_times":demo_slot, "senior_consented":True,
})
test("일정 권한이 있어도 가족이 철회된 동의를 다시 켜지 못함", "error" in r and "어르신" in r.get("error",""), str(r)[:160])

r = execute_tool("care_circle", {
    "action":"revoke", "requester_user_id":circle_senior,
    "senior_user_id":circle_senior, "target_user_id":circle_family,
})
test("어르신이 가족 연결 즉시 해제", r.get("status") == "revoked" and r.get("pending_notifications_cancelled") is True, str(r)[:140])
r = execute_tool("care_routine", {
    "action":"status", "requester_user_id":circle_family,
    "senior_user_id":circle_senior,
})
test("연결 해제 후 가족 조회 차단", "error" in r, str(r)[:120])

# === 3. daily_checkin ===
print("\n[2] daily_checkin")
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"initiate","nickname":"순자"})
test("initiate status=initiated", r.get("status") == "initiated", str(r)[:100])
test("initiate greeting 있음", bool(r.get("greeting")), str(r)[:100])
test("initiate 원터치 quick_replies 5개", len(r.get("quick_replies",[])) == 5, str(r)[:100])
test("initiate 도움 요청 버튼", "도움이 필요해요" in r.get("quick_replies",[]), str(r)[:140])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"오늘 기분이 좋아요! 산책도 했어요"})
test("analyze(positive) sentiment=positive", r.get("sentiment") == "positive", str(r)[:100])
test("analyze(positive) status=normal", r.get("status") == "normal", str(r)[:100])
test("analyze(positive) 후속 버튼", len(r.get("quick_replies",[])) == 4 and "밥 먹었어요" in r.get("quick_replies",[]), str(r)[:140])
from db.schema import get_checkin_stats
stats = get_checkin_stats("senior_001", days=1, db_path=DB_PATH)
test("analyze 후 응답률 100%", stats.get("response_rate") == 100.0, str(stats)[:100])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"무릎이 너무 아파요. 어지러워서 쓰러질 것 같아요"})
test("analyze(danger) sentiment=negative", r.get("sentiment") == "negative", str(r)[:100])
test("analyze(danger) 위험키워드 감지", len(r.get("danger_keywords_detected",[])) > 0, str(r)[:100])
test("analyze(danger) 직접 119 안내", "119" in r.get("response_text","") and "자동" in r.get("response_text",""), str(r)[:180])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"no_response"})
test("no_response 응답 있음", "status" in r, str(r)[:100])

r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"약 먹었어요"})
test("원터치 복약 완료 → positive", r.get("sentiment") == "positive", str(r)[:120])
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"도움이 필요해요"})
test("원터치 도움 요청 → concern", r.get("status") == "concern", str(r)[:120])
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"약을 못 먹었어요"})
test("복약 미완료 부정어 → negative", r.get("sentiment") == "negative", str(r)[:120])
r = execute_tool("daily_checkin", {"user_id":"senior_001","action":"analyze","message":"잠이 안 와요"})
test("수면 불편 버튼 → negative", r.get("sentiment") == "negative", str(r)[:120])

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
test("Widget A quickReplies 5개", len(r.get("template",{}).get("quickReplies",[])) == 5, str(r)[:100])
test("Widget A 도움 요청 버튼", any(q.get("label") == "도움이 필요해요" for q in r.get("template",{}).get("quickReplies",[])), str(r)[:140])

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

r = execute_tool("health_log", {"user_id":"senior_001","action":"parse","message":"혈압은 135에 85이고 혈당은 110이에요","nickname":"순자"})
test(
    "한 문장 복수 건강 수치 모두 기록",
    r.get("parsed", {}).get("blood_sugar") == 110.0 and len(r.get("results", [])) == 3,
    str(r)[:180],
)

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

# === 19. 영구 대기열·서명 전달 ===
print("\n[19] 영구 대기열·서명 전달")
from tools.care_routine import claim_pending_notifications, mark_notification_delivery

delivery_now = datetime.now(timezone.utc).replace(microsecond=0)
delivery_iso = delivery_now.isoformat().replace("+00:00", "Z")
conn = sqlite3.connect(DB_PATH)
conn.execute(
    """INSERT INTO care_outbox
       (senior_user_id, recipient_user_id, event_type, severity, payload_json,
        due_at, status, dedupe_key, created_at, next_attempt_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
    ("queue-senior", "opaque-family", "family_digest", "info", '{"text":"안부 요약"}',
     delivery_iso, "queue-dedupe-1", delivery_iso, delivery_iso, delivery_iso),
)
conn.commit()
conn.close()

first_claim = claim_pending_notifications(now=delivery_now, db_path=DB_PATH)
second_claim = claim_pending_notifications(now=delivery_now, db_path=DB_PATH)
test("대기 알림을 한 worker만 원자적으로 점유", len(first_claim) == 1 and len(second_claim) == 0 and first_claim[0].get("status") == "processing", str(first_claim)[:180])
test("잘못된 lease 토큰으로 완료 처리 차단", mark_notification_delivery(first_claim[0]["id"], "sent", claim_token="wrong", now=delivery_now, db_path=DB_PATH) is False)
retried = mark_notification_delivery(
    first_claim[0]["id"], "failed", claim_token=first_claim[0]["claim_token"],
    error="temporary test failure", base_delay_seconds=5, now=delivery_now, db_path=DB_PATH,
)
test("전달 실패를 재시도 대기 상태로 복구", retried is True, str(first_claim[0])[:160])
test("백오프 전 재점유 차단", len(claim_pending_notifications(now=delivery_now + timedelta(seconds=4), db_path=DB_PATH)) == 0)
retry_claim = claim_pending_notifications(now=delivery_now + timedelta(seconds=5), db_path=DB_PATH)
test("백오프 후 동일 알림 재점유", len(retry_claim) == 1 and retry_claim[0].get("attempt_count") == 2, str(retry_claim)[:180])
sent = mark_notification_delivery(
    retry_claim[0]["id"], "sent", claim_token=retry_claim[0]["claim_token"],
    provider_message_id="provider-test-1", now=delivery_now + timedelta(seconds=5), db_path=DB_PATH,
)
conn = sqlite3.connect(DB_PATH)
sent_row = conn.execute(
    "SELECT status, provider_message_id FROM care_outbox WHERE id = ?", (retry_claim[0]["id"],)
).fetchone()
conn.close()
test("성공 전달과 공급사 메시지 ID 감사 기록", sent is True and sent_row == ("sent", "provider-test-1"), str(sent_row))

stale_time = (delivery_now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
conn = sqlite3.connect(DB_PATH)
conn.execute(
    """INSERT INTO care_outbox
       (senior_user_id, recipient_user_id, event_type, severity, payload_json,
        due_at, status, dedupe_key, created_at, next_attempt_at, claimed_at,
        claim_token, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, ?)""",
    ("queue-senior", "opaque-family", "family_digest", "info", '{}', delivery_iso,
     "queue-stale-lease", delivery_iso, delivery_iso, stale_time, "stale-token", stale_time),
)
conn.commit()
conn.close()
recovered_claim = claim_pending_notifications(now=delivery_now + timedelta(seconds=10), lease_seconds=30, db_path=DB_PATH)
test("중단된 worker의 만료 lease 자동 회수", len(recovered_claim) == 1 and recovered_claim[0].get("dedupe_key") == "queue-stale-lease", str(recovered_claim)[:180])
mark_notification_delivery(
    recovered_claim[0]["id"], "sent", claim_token=recovered_claim[0]["claim_token"],
    now=delivery_now + timedelta(seconds=10), db_path=DB_PATH,
)

conn = sqlite3.connect(DB_PATH)
conn.execute(
    """INSERT INTO care_outbox
       (senior_user_id, recipient_user_id, event_type, severity, payload_json,
        due_at, status, dedupe_key, created_at, next_attempt_at, claimed_at,
        claim_token, attempt_count, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?, 5, ?)""",
    ("queue-senior", "opaque-family", "family_digest", "info", '{}', delivery_iso,
     "queue-exhausted-lease", delivery_iso, delivery_iso, stale_time, "exhausted-token", stale_time),
)
conn.commit()
conn.close()
exhausted_claim = claim_pending_notifications(
    now=delivery_now + timedelta(seconds=20), lease_seconds=30, max_attempts=5, db_path=DB_PATH
)
conn = sqlite3.connect(DB_PATH)
exhausted_status = conn.execute(
    "SELECT status FROM care_outbox WHERE dedupe_key = 'queue-exhausted-lease'"
).fetchone()[0]
conn.close()
test("재시도 한도를 넘긴 만료 lease 종료", not exhausted_claim and exhausted_status == "failed", exhausted_status)

import hashlib, hmac
import services.notification_delivery as delivery_module
delivery_keys = (
    "CARETALK_DELIVERY_MODE", "CARETALK_DELIVERY_WEBHOOK_URL",
    "CARETALK_DELIVERY_WEBHOOK_SECRET", "CARETALK_ALLOW_INSECURE_LOCAL_WEBHOOK",
)
delivery_original = {key: os.environ.get(key) for key in delivery_keys}
original_post = delivery_module.requests.post
captured_delivery = {}

class _DeliveryResponse:
    status_code = 202
    def json(self):
        return {"provider_message_id": "signed-provider-1"}

def _fake_delivery_post(url, data, headers, timeout, allow_redirects):
    captured_delivery.update({
        "url": url, "data": data, "headers": headers,
        "timeout": timeout, "allow_redirects": allow_redirects,
    })
    return _DeliveryResponse()

try:
    os.environ["CARETALK_DELIVERY_MODE"] = "webhook"
    os.environ["CARETALK_DELIVERY_WEBHOOK_URL"] = "http://127.0.0.1:9876/caretalk"
    os.environ["CARETALK_DELIVERY_WEBHOOK_SECRET"] = "s" * 32
    os.environ["CARETALK_ALLOW_INSECURE_LOCAL_WEBHOOK"] = "true"
    delivery_module.requests.post = _fake_delivery_post
    delivery_result = delivery_module.WebhookDeliveryClient().send(retry_claim[0])
    expected_signature = "sha256=" + hmac.new(
        ("s" * 32).encode("utf-8"), captured_delivery["data"], hashlib.sha256
    ).hexdigest()
    delivery_body = json.loads(captured_delivery["data"])
    test("전달 웹훅 HMAC-SHA256 서명 검증", captured_delivery["headers"].get("X-CareTalk-Signature") == expected_signature)
    test("전달 웹훅 리다이렉트 차단", captured_delivery.get("allow_redirects") is False)
    test("전달에는 불투명 계정 ID만 포함", delivery_body.get("recipient",{}).get("identifier_type") == "opaque_linked_account" and delivery_body.get("privacy",{}).get("phone_number_included") is False, str(delivery_body)[:180])
    test("공급사 전달 ID 수취", delivery_result.provider_message_id == "signed-provider-1", str(delivery_result))
finally:
    delivery_module.requests.post = original_post
    for key, value in delivery_original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

from services.alimtalk import send_alimtalk
try:
    send_alimtalk("key", "sender", "01000000000", "template", "message", api_url="https://api.kakao.com/v2/api/talk/message/send")
    wrong_kakao_endpoint_rejected = False
except RuntimeError as exc:
    wrong_kakao_endpoint_rejected = "아닙니다" in str(exc)
test("카카오톡 메시지 API를 알림톡으로 오용 차단", wrong_kakao_endpoint_rejected)

server_status = server._server_info()
test("상태 API에 worker·대기열 공개", "worker" in server_status and "outbox" in server_status["worker"], str(server_status)[:180])
test("상태 API에 기기 개인정보 경계 공개", server_status.get("device_bridge",{}).get("exact_location_collected") is False and server_status.get("device_bridge",{}).get("token_storage") == "sha256_hash_only", str(server_status)[:180])

# === 20. 정리 ===
print("\n" + "=" * 60)
print(f"결과: ✅ {passed}개 통과 / ❌ {failed}개 실패")
print("=" * 60)

# 임시 DB 정리
try:
    shutil.rmtree(tmp_dir)
except:
    pass

sys.exit(0 if failed == 0 else 1)
