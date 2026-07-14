# -*- coding: utf-8 -*-
"""돌봄톡(CareTalk) 공식 FastMCP 서버 엔트리포인트."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from services.usage_guard import live_api_enabled

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
_DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "db", "caretalk.db")


def _load_env_files() -> None:
    """Load project-local .env files while keeping real process env precedence."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    existing_env = dict(os.environ)
    for env_name in (".env", ".env.local"):
        env_path = os.path.join(PROJECT_ROOT, env_name)
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)

    for key, value in existing_env.items():
        os.environ[key] = value


_PLACEHOLDER_ENV_VALUES = {
    "placeholder-openai-key",
    "your-kakao-rest-api-key-here",
    "your-kakao-client-secret-here",
    "your-kakao-biz-api-key-here",
    "your-kakao-sender-key-here",
}


def _is_placeholder_value(value: Optional[str]) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    return (
        stripped in _PLACEHOLDER_ENV_VALUES
        or stripped.startswith("your-")
        or "your-openai-api-key" in stripped
    )


def _sanitize_placeholder_env() -> None:
    for key in (
        "OPENAI_API_KEY",
        "KAKAO_REST_API_KEY",
        "KAKAO_CLIENT_SECRET",
        "KAKAO_BIZ_API_KEY",
        "KAKAO_SENDER_KEY",
        "CARETALK_DELIVERY_WEBHOOK_URL",
        "CARETALK_DELIVERY_WEBHOOK_SECRET",
    ):
        if _is_placeholder_value(os.environ.get(key)):
            os.environ.pop(key, None)


def _env_configured(key: str) -> bool:
    value = os.environ.get(key, "").strip()
    return bool(value) and not _is_placeholder_value(value)


def _api_key_status() -> Dict[str, Dict[str, Any]]:
    return {
        "openai": {"env": "OPENAI_API_KEY", "configured": _env_configured("OPENAI_API_KEY")},
        "kakao_login": {
            "env": "KAKAO_REST_API_KEY",
            "configured": _env_configured("KAKAO_REST_API_KEY"),
        },
        "delivery_gateway": {
            "env": "CARETALK_DELIVERY_WEBHOOK_URL + CARETALK_DELIVERY_WEBHOOK_SECRET",
            "configured": (
                _env_configured("CARETALK_DELIVERY_WEBHOOK_URL")
                and _env_configured("CARETALK_DELIVERY_WEBHOOK_SECRET")
            ),
        },
    }


_load_env_files()
_sanitize_placeholder_env()


def _resolve_db_path() -> str:
    configured = os.environ.get("CARETALK_DB_PATH", "").strip()
    if not configured:
        return _DEFAULT_DB_PATH
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path(PROJECT_ROOT) / path
    return str(path.resolve())


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_port() -> int:
    raw = os.environ.get("MCP_PORT") or os.environ.get("PORT") or "9000"
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return 9000
    return port if 1 <= port <= 65535 else 9000


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def _transport_security() -> TransportSecuritySettings:
    endpoint_host = os.environ.get(
        "PLAYMCP_ENDPOINT_HOST",
        "caretalk-mcp.playmcp-endpoint.kakaocloud.io",
    ).strip()
    allowed_hosts = [
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
        "caretalk-mcp",
        "caretalk-mcp:*",
    ]
    allowed_origins = [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
        "https://playmcp.kakaocloud.io",
        "https://playmcp.kakao.com",
    ]
    if endpoint_host:
        allowed_hosts.extend([endpoint_host, f"{endpoint_host}:*"])
        allowed_origins.extend(
            [f"https://{endpoint_host}", f"https://{endpoint_host}:*"]
        )
    allowed_hosts.extend(_csv_env("MCP_ALLOWED_HOSTS"))
    allowed_origins.extend(_csv_env("MCP_ALLOWED_ORIGINS"))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )


DB_PATH = _resolve_db_path()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("caretalk-server")

MOCK_MODE = _env_bool("MOCK_MODE", True)

def set_mock_mode(enabled: bool):
    global MOCK_MODE
    MOCK_MODE = bool(enabled)
    os.environ["MOCK_MODE"] = "true" if MOCK_MODE else "false"
    if MOCK_MODE:
        os.environ["LIVE_API_ENABLED"] = "false"

# Utility
NL = chr(10)

def _get_time_greeting():
    from datetime import datetime
    h = datetime.now().hour
    if 5 <= h < 12: return "좋은 아침입니다"
    elif 12 <= h < 17: return "좋은 오후입니다"
    elif 17 <= h < 21: return "좋은 저녁입니다"
    else: return "편안한 밤 되세요"

def _get_mock_weather():
    return {"condition": "맑음", "temp": 24, "advice": "산책하기 좋은 날씨입니다"}

TOOL_DEFINITIONS = [
    {"name": "care_guide", "description": "첫 사용 목적·사용법·추천 답변·접근성·FAQ·개인정보 안내. action: start, examples, faq, accessibility, privacy", "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["start", "examples", "faq", "accessibility", "privacy"]}, "question": {"type": "string"}, "audience": {"type": "string", "enum": ["senior", "family", "helper"]}}}},
    {"name": "care_circle", "description": "어르신 동의 기반 가족 계정 연결·권한·해제. action: create_invite, join, list, update_permissions, revoke", "inputSchema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["create_invite", "join", "list", "update_permissions", "revoke"]}, "requester_user_id": {"type": "string"}, "senior_user_id": {"type": "string"}, "nickname": {"type": "string"}, "invite_code": {"type": "string"}, "role": {"type": "string", "enum": ["family", "guardian", "helper"]}, "permissions": {"type": "string", "description": "쉼표 구분: view_summary, receive_inactivity_alerts, receive_emergency_alerts, manage_schedule"}, "target_user_id": {"type": "string"}, "circle_name": {"type": "string"}, "senior_consented": {"type": "boolean"}, "invite_hours": {"type": "integer", "minimum": 1, "maximum": 72}}, "required": ["action", "requester_user_id"]}},
    {
        "name": "care_routine",
        "description": (
            "예약 안부·가족 요약·휴대폰 활동 부재 확인·가족 응답·동의 철회와 "
            "휴대폰·웨어러블 연결 관리"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "configure", "record_activity", "run_due", "acknowledge",
                        "status", "pause", "create_device_pairing", "list_devices",
                        "revoke_device",
                    ],
                },
                "requester_user_id": {"type": "string"},
                "senior_user_id": {"type": "string"},
                "prompt_times": {"type": "string", "description": "쉼표로 구분한 HH:MM"},
                "digest_times": {"type": "string", "description": "쉼표로 구분한 HH:MM"},
                "timezone_name": {"type": "string"},
                "response_window_minutes": {"type": "integer"},
                "inactivity_hours": {"type": "integer"},
                "escalation_hours": {"type": "integer"},
                "inactivity_grace_minutes": {"type": "integer"},
                "inactivity_mode": {"type": "string", "enum": ["both", "either"]},
                "quiet_start": {"type": "string"},
                "quiet_end": {"type": "string"},
                "phone_activity_enabled": {"type": "boolean"},
                "wearable_enabled": {"type": "boolean"},
                "senior_consented": {"type": "boolean"},
                "event_type": {
                    "type": "string",
                    "enum": ["screen_unlock", "app_open", "manual_confirm", "device_motion", "wearable_sync"],
                },
                "source": {"type": "string", "enum": ["phone", "wearable", "manual", "demo"]},
                "occurred_at": {"type": "string"},
                "event_id": {"type": "string"},
                "now": {"type": "string"},
                "outbox_id": {"type": "integer"},
                "response": {
                    "type": "string",
                    "enum": ["확인했어요", "전화해볼게요", "방문 확인할게요", "해결됐어요", "도움이 더 필요해요"],
                },
                "device_type": {"type": "string", "enum": ["phone", "wearable"]},
                "device_label": {"type": "string"},
                "device_id": {"type": "string"},
                "pairing_minutes": {"type": "integer", "minimum": 5, "maximum": 30},
            },
            "required": ["action", "requester_user_id", "senior_user_id"],
        },
    },
    {"name": "daily_checkin", "description": "매일 안부 확인. action: initiate(안부 메시지 생성), analyze(응답 감정 분석), no_response(무응답 확인)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["initiate", "analyze", "no_response"]}, "message": {"type": "string"}, "nickname": {"type": "string"}}, "required": ["user_id"]}},
    {"name": "emergency_detect", "description": "위험 신호 실시간 감지. action: detect(메시지 위험 판정), silence(무응답 경보)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "message": {"type": "string"}, "action": {"type": "string", "enum": ["detect", "silence"]}}, "required": ["user_id"]}},
    {"name": "family_report", "description": "가족용 주간/일일 돌봄 리포트 생성. report_type: weekly(주간), daily(일일)", "inputSchema": {"type": "object", "properties": {"senior_user_id": {"type": "string"}, "report_type": {"type": "string", "enum": ["weekly", "daily"]}}, "required": ["senior_user_id"]}},
    {"name": "daily_care_widget", "description": "노인용 '오늘의 돌봄' Widget A 렌더 (SimpleText + quickReplies)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}}, "required": ["user_id"]}},
    {"name": "health_log", "description": "건강 데이터(혈압·혈당·체중·체온·맥박) 기록 및 추세 분석. action: log(기록), query(조회), analyze(추세 분석), parse(자연어 파싱 기록). source로 입력 경로(직접/기기/사진) 구분", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["log", "query", "analyze", "parse"]}, "data_type": {"type": "string", "enum": ["systolic", "diastolic", "blood_sugar", "weight", "temperature", "heart_rate"]}, "value": {"type": "number"}, "message": {"type": "string"}, "nickname": {"type": "string"}, "days": {"type": "integer"}, "source": {"type": "string", "enum": ["manual", "device", "ocr"], "description": "입력 경로: manual=직접 입력, device=혈압계 등 기기 연동, ocr=측정기 사진 판독"}}, "required": ["user_id"]}},
    {"name": "reminiscence_chat", "description": "추억 회상 기반 정서 지원 대화. action: chat(대화 응답), suggest_topic(주제 추천)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["chat", "suggest_topic"]}, "message": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}, "nickname": {"type": "string"}}, "required": ["user_id"]}},
    {"name": "family_report_widget", "description": "가족용 '주간 돌봄 리포트' Widget B 렌더 (BasicCard + ListCard)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "days": {"type": "integer"}}, "required": ["user_id"]}},
    {"name": "health_facility", "description": "어르신 무료 건강 서비스(보건소·치매안심센터) 안내. action: search(지역 검색), programs(무료 프로그램 목록), recommend(건강 기록 기반 맞춤 추천), notify(알림 메시지 생성)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["search", "programs", "recommend", "notify"]}, "region": {"type": "string", "description": "지역명 일부 (예: 마포, 수원)"}, "facility_type": {"type": "string", "enum": ["보건소", "치매안심센터"]}, "nickname": {"type": "string"}, "days": {"type": "integer"}}, "required": ["user_id"]}},
    {"name": "build_care_safety_plan", "description": "당사자 동의와 접근성, 단계적 사람 확인을 반영한 돌봄 안전계획 초안 생성. 실제 메시지·전화·119 신고는 수행하지 않음", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "checkin_time": {"type": "string", "description": "24시간제 HH:MM"}, "response_window_hours": {"type": "integer", "minimum": 1, "maximum": 24}, "contact_roles": {"type": "string", "description": "전화번호가 아닌 관계 역할 (예: 딸, 복지사)"}, "accessibility_needs": {"type": "string"}, "senior_consented": {"type": "boolean"}}, "required": ["user_id"]}}
]

_TEXT_LIMITS = {
    "user_id": 128,
    "senior_user_id": 128,
    "requester_user_id": 128,
    "target_user_id": 128,
    "nickname": 40,
    "message": 4000,
    "region": 80,
    "contact_roles": 120,
    "checkin_time": 5,
    "accessibility_needs": 300,
    "question": 300,
    "audience": 20,
    "invite_code": 128,
    "role": 20,
    "permissions": 200,
    "circle_name": 60,
    "prompt_times": 100,
    "digest_times": 100,
    "timezone_name": 64,
    "inactivity_mode": 10,
    "quiet_start": 5,
    "quiet_end": 5,
    "event_type": 30,
    "source": 20,
    "occurred_at": 64,
    "event_id": 128,
    "now": 64,
    "response": 80,
    "device_type": 16,
    "device_label": 40,
    "device_id": 80,
}


def _validate_arguments(arguments: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, str]]]:
    if not isinstance(arguments, dict):
        return {}, {"error": "arguments는 객체여야 합니다."}
    cleaned = dict(arguments)
    for key, limit in _TEXT_LIMITS.items():
        if key not in cleaned or cleaned[key] is None:
            continue
        value = str(cleaned[key]).strip()
        if len(value) > limit:
            return {}, {"error": f"{key}는 {limit}자 이하여야 합니다."}
        cleaned[key] = value
    return cleaned, None


def _require_arg(arguments: Dict[str, Any], key: str):
    value = arguments.get(key)
    if value in (None, ""):
        return None, {"error": key + "는 필수입니다."}
    return value, None

def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    arguments, validation_error = _validate_arguments(arguments)
    if validation_error:
        return validation_error

    if name == "care_guide":
        from tools.care_guide import build_care_guide
        return build_care_guide(
            action=arguments.get("action", "start"),
            question=arguments.get("question", ""),
            audience=arguments.get("audience", "senior"),
        )
    elif name == "care_circle":
        from tools.care_circle import manage_care_circle
        requester_user_id, error = _require_arg(arguments, "requester_user_id")
        if error:
            return error
        return manage_care_circle(
            action=arguments.get("action", "list"),
            requester_user_id=requester_user_id,
            senior_user_id=arguments.get("senior_user_id", ""),
            nickname=arguments.get("nickname", ""),
            invite_code=arguments.get("invite_code", ""),
            role=arguments.get("role", "family"),
            permissions=arguments.get("permissions", ""),
            target_user_id=arguments.get("target_user_id", ""),
            circle_name=arguments.get("circle_name", "우리 가족 돌봄"),
            senior_consented=arguments.get("senior_consented", False),
            invite_hours=arguments.get("invite_hours", 24),
            db_path=DB_PATH,
        )
    elif name == "care_routine":
        from tools.care_routine import manage_care_routine
        requester_user_id, error = _require_arg(arguments, "requester_user_id")
        if error:
            return error
        senior_user_id, error = _require_arg(arguments, "senior_user_id")
        if error:
            return error
        return manage_care_routine(
            action=arguments.get("action", "status"),
            requester_user_id=requester_user_id,
            senior_user_id=senior_user_id,
            prompt_times=arguments.get("prompt_times", "09:00,14:00,20:00"),
            digest_times=arguments.get("digest_times", "14:30,21:00"),
            timezone_name=arguments.get("timezone_name", "Asia/Seoul"),
            response_window_minutes=arguments.get("response_window_minutes", 60),
            inactivity_hours=arguments.get("inactivity_hours", 8),
            escalation_hours=arguments.get("escalation_hours", 12),
            inactivity_grace_minutes=arguments.get("inactivity_grace_minutes", 30),
            inactivity_mode=arguments.get("inactivity_mode", "both"),
            quiet_start=arguments.get("quiet_start", "22:00"),
            quiet_end=arguments.get("quiet_end", "07:00"),
            phone_activity_enabled=arguments.get("phone_activity_enabled", True),
            wearable_enabled=arguments.get("wearable_enabled", False),
            senior_consented=arguments.get("senior_consented", False),
            event_type=arguments.get("event_type", ""),
            source=arguments.get("source", "demo"),
            occurred_at=arguments.get("occurred_at", ""),
            event_id=arguments.get("event_id", ""),
            now=arguments.get("now", ""),
            outbox_id=arguments.get("outbox_id", 0),
            response=arguments.get("response", ""),
            device_type=arguments.get("device_type", "phone"),
            device_label=arguments.get("device_label", ""),
            device_id=arguments.get("device_id", ""),
            pairing_minutes=arguments.get("pairing_minutes", 10),
            db_path=DB_PATH,
        )
    elif name == "daily_checkin":
        from tools.daily_checkin import initiate_checkin, analyze_checkin_response, check_no_response
        action = arguments.get("action", "initiate")
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        if action == "initiate":
            nickname = arguments.get("nickname", "")
            if not nickname:
                return {"error": "nickname은 initiate 모드에서 필수입니다."}
            return initiate_checkin(user_id, nickname, db_path=DB_PATH)
        elif action == "analyze":
            message = arguments.get("message", "")
            if not message:
                return {"error": "message는 analyze 모드에서 필수입니다."}
            return analyze_checkin_response(user_id, message, mock=MOCK_MODE, db_path=DB_PATH)
        elif action == "no_response":
            return check_no_response(user_id, db_path=DB_PATH)
        else:
            return {"error": "알 수 없는 action: " + action}
    elif name == "emergency_detect":
        from tools.emergency_detect import detect_emergency, check_silence_alert
        action = arguments.get("action", "detect")
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        if action == "detect":
            message = arguments.get("message", "")
            if not message:
                return {"error": "message는 detect 모드에서 필수입니다."}
            return detect_emergency(user_id, message, mock=MOCK_MODE, db_path=DB_PATH)
        elif action == "silence":
            return check_silence_alert(user_id, db_path=DB_PATH)
        else:
            return {"error": "알 수 없는 action: " + action}
    elif name == "family_report":
        from tools.family_report import generate_weekly_report, generate_daily_summary
        senior_user_id, error = _require_arg(arguments, "senior_user_id")
        if error:
            return error
        report_type = arguments.get("report_type", "weekly")
        if report_type == "weekly":
            return generate_weekly_report(senior_user_id, db_path=DB_PATH, mock=MOCK_MODE)
        elif report_type == "daily":
            return generate_daily_summary(senior_user_id, db_path=DB_PATH)
        else:
            return {"error": "알 수 없는 report_type: " + report_type}
    elif name == "daily_care_widget":
        # Widget 렌더링은 _widgets/widget_a 로 단일화 (서버는 인자만 전달)
        from _widgets.widget_a import create_daily_care_widget, get_mock_weather
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        nickname = arguments.get("nickname", "어르신")
        sentiment = arguments.get("sentiment", "neutral")
        return create_daily_care_widget(
            user_id=user_id,
            weather_info=get_mock_weather(),
            checkin_status={"sentiment": sentiment},
            nickname=nickname,
        )
    elif name == "health_log":
        from tools.health_log import log_health_data, query_health_data, analyze_health_trend, log_from_message
        action = arguments.get("action", "log")
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        source = arguments.get("source", "manual")
        if action == "log":
            data_type = arguments.get("data_type", "")
            value = arguments.get("value")
            if not data_type or value is None:
                return {"error": "data_type과 value는 log 모드에서 필수입니다."}
            nickname = arguments.get("nickname")
            return log_health_data(user_id, data_type, value, nickname, db_path=DB_PATH, source=source)
        elif action == "query":
            data_type = arguments.get("data_type")
            days = arguments.get("days", 7)
            return query_health_data(user_id, data_type, days, db_path=DB_PATH)
        elif action == "analyze":
            data_type = arguments.get("data_type")
            days = arguments.get("days", 14)
            return analyze_health_trend(user_id, data_type, days, db_path=DB_PATH)
        elif action == "parse":
            message = arguments.get("message", "")
            if not message:
                return {"error": "message는 parse 모드에서 필수입니다."}
            nickname = arguments.get("nickname")
            return log_from_message(user_id, message, nickname, db_path=DB_PATH, source=source)
        else:
            return {"error": "알 수 없는 action: " + action}
    elif name == "reminiscence_chat":
        from tools.reminiscence_chat import generate_reminiscence_response, suggest_reminiscence_topic
        action = arguments.get("action", "chat")
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        nickname = arguments.get("nickname")
        if action == "chat":
            message = arguments.get("message", "")
            if not message:
                return {"error": "message는 chat 모드에서 필수입니다."}
            sentiment = arguments.get("sentiment", "neutral")
            return generate_reminiscence_response(user_id, message, sentiment, nickname, mock=MOCK_MODE, db_path=DB_PATH)
        elif action == "suggest_topic":
            sentiment = arguments.get("sentiment", "neutral")
            return suggest_reminiscence_topic(user_id, sentiment, nickname, db_path=DB_PATH)
        else:
            return {"error": "알 수 없는 action: " + action}
    elif name == "family_report_widget":
        from _widgets.widget_b import create_family_report_widget
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        nickname = arguments.get("nickname")
        days = arguments.get("days", 7)
        return create_family_report_widget(user_id, nickname, days, db_path=DB_PATH)
    elif name == "health_facility":
        from tools.health_facility import search_facilities, list_free_programs, recommend_for_user, build_notify_message
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        action = arguments.get("action", "search")
        region = arguments.get("region", "")
        if action == "search":
            return search_facilities(region, arguments.get("facility_type") or "")
        elif action == "programs":
            return list_free_programs()
        elif action == "recommend":
            days = arguments.get("days", 14)
            return recommend_for_user(user_id, region, days, db_path=DB_PATH)
        elif action == "notify":
            nickname = arguments.get("nickname", "어르신")
            return build_notify_message(user_id, nickname, region, db_path=DB_PATH)
        else:
            return {"error": "알 수 없는 action: " + action}
    elif name == "build_care_safety_plan":
        from tools.care_safety_plan import build_care_safety_plan
        user_id, error = _require_arg(arguments, "user_id")
        if error:
            return error
        return build_care_safety_plan(
            user_id=user_id,
            nickname=arguments.get("nickname", "어르신"),
            checkin_time=arguments.get("checkin_time", "09:00"),
            response_window_hours=arguments.get("response_window_hours", 2),
            contact_roles=arguments.get("contact_roles", "가족, 복지사"),
            accessibility_needs=arguments.get("accessibility_needs", ""),
            senior_consented=arguments.get("senior_consented", False),
        )
    else:
        return {"error": "알 수 없는 Tool: " + name}


def _tool_result(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    result = execute_tool(name, arguments)
    if isinstance(result, dict) and result.get("error"):
        raise ToolError(str(result["error"]))
    return result


mcp = FastMCP(
    "CareTalk",
    instructions=(
        "돌봄톡은 독거 어르신의 안부 확인, 건강 기록, 응급 신호 감지, "
        "추억 회상 대화와 가족 리포트를 제공하는 한국어 돌봄 MCP 서버입니다. "
        "처음 방문했거나 사용법·도움말·FAQ를 물으면 care_guide를 먼저 호출하고, "
        "가족 계정 연결은 care_circle에서 어르신 동의와 계정별 권한을 먼저 확인하세요. "
        "예약 질문·가족 요약·휴대폰 활동 부재 확인은 care_routine을 사용하되 위치나 화면 내용은 수집하지 마세요. "
        "안부 계획을 요청하면 build_care_safety_plan으로 당사자 동의와 사람 확인 단계를 먼저 설계하세요. "
        "응급 판정은 보조 신호이며 실제 위급 상황에서는 즉시 119에 연락해야 합니다."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
    transport_security=_transport_security(),
)


def _annotations(
    title: str,
    *,
    read_only: bool,
    idempotent: bool,
    open_world: bool,
) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=False,
        idempotentHint=idempotent,
        openWorldHint=open_world,
    )


@mcp.tool(
    title="Start CareTalk | 돌봄톡 시작 안내",
    description=(
        "사용자가 처음 방문했거나 목적·사용법·큰 글씨·추천 답변·FAQ·개인정보를 물을 때 먼저 호출합니다. "
        "Explains one-tap senior use, accessibility, safety boundaries, and FAQs for CareTalk(돌봄톡)."
    ),
    annotations=_annotations(
        "Start CareTalk | 돌봄톡 시작 안내", read_only=True, idempotent=True, open_world=False
    ),
)
def care_guide(
    action: Literal["start", "examples", "faq", "accessibility", "privacy"] = "start",
    question: str = "",
    audience: Literal["senior", "family", "helper"] = "senior",
) -> Dict[str, Any]:
    """어르신·가족·돌봄 담당자에게 첫 사용 흐름과 자주 묻는 질문을 안내합니다."""
    return _tool_result("care_guide", locals())


@mcp.tool(
    title="Connect a Consent-Based Care Circle | 가족 돌봄 연결",
    description=(
        "어르신이 지정한 가족·보호자·복지사 계정을 일회용 초대로 연결하고 공유 권한을 관리할 때 호출합니다. "
        "Creates, lists, updates, or revokes consent-based account links for CareTalk(돌봄톡); invite codes are one-time secrets."
    ),
    annotations=_annotations(
        "Connect a Consent-Based Care Circle | 가족 돌봄 연결",
        read_only=False,
        idempotent=False,
        open_world=False,
    ),
)
def care_circle(
    action: Literal["create_invite", "join", "list", "update_permissions", "revoke"],
    requester_user_id: str,
    senior_user_id: str = "",
    nickname: str = "",
    invite_code: str = "",
    role: Literal["family", "guardian", "helper"] = "family",
    permissions: str = "",
    target_user_id: str = "",
    circle_name: str = "우리 가족 돌봄",
    senior_consented: bool = False,
    invite_hours: int = 24,
) -> Dict[str, Any]:
    """여러 가족 계정을 동의와 최소 권한으로 연결하거나 즉시 해제합니다."""
    return _tool_result("care_circle", locals())


@mcp.tool(
    title="Run Scheduled Care and Phone Activity Checks | 예약 돌봄",
    description=(
        "정해진 시간의 원터치 안부 질문, 가족 중간·하루 요약, 휴대폰 화면 사용·이동 부재 확인을 설정하거나 실행할 때 호출합니다. "
        "Configures or runs scheduled check-ins, consent-limited family digests, and minimal phone-activity checks for CareTalk(돌봄톡)."
    ),
    annotations=_annotations(
        "Run Scheduled Care and Phone Activity Checks | 예약 돌봄",
        read_only=False,
        idempotent=False,
        open_world=False,
    ),
)
def care_routine(
    action: Literal[
        "configure",
        "record_activity",
        "run_due",
        "acknowledge",
        "status",
        "pause",
        "create_device_pairing",
        "list_devices",
        "revoke_device",
    ],
    requester_user_id: str,
    senior_user_id: str,
    prompt_times: str = "09:00,14:00,20:00",
    digest_times: str = "14:30,21:00",
    timezone_name: str = "Asia/Seoul",
    response_window_minutes: int = 60,
    inactivity_hours: int = 8,
    escalation_hours: int = 12,
    inactivity_grace_minutes: int = 30,
    inactivity_mode: Literal["both", "either"] = "both",
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
    phone_activity_enabled: bool = True,
    wearable_enabled: bool = False,
    senior_consented: bool = False,
    event_type: Literal["screen_unlock", "app_open", "manual_confirm", "device_motion", "wearable_sync", ""] = "",
    source: Literal["phone", "wearable", "manual", "demo"] = "demo",
    occurred_at: str = "",
    event_id: str = "",
    now: str = "",
    outbox_id: int = 0,
    response: str = "",
    device_type: Literal["phone", "wearable"] = "phone",
    device_label: str = "",
    device_id: str = "",
    pairing_minutes: int = 10,
) -> Dict[str, Any]:
    """예약 메시지·최소 활동 신호와 동의된 기기 연결을 관리합니다."""
    return _tool_result("care_routine", locals())


@mcp.tool(
    title="Daily Check-in | 매일 안부 확인",
    description=(
        "안부를 시작하거나 답변·무응답을 확인할 때 호출합니다. Starts a daily check-in, analyzes a reply, "
        "or checks non-response for CareTalk(돌봄톡), with protected AI and rules fallback."
    ),
    annotations=_annotations(
        "Daily Check-in | 매일 안부 확인", read_only=False, idempotent=False, open_world=True
    ),
)
def daily_checkin(
    user_id: str,
    action: Literal["initiate", "analyze", "no_response"] = "initiate",
    message: str = "",
    nickname: str = "",
) -> Dict[str, Any]:
    """매일 안부를 시작하거나 응답을 분석하고 장기 무응답 여부를 확인합니다."""
    return _tool_result("daily_checkin", locals())


@mcp.tool(
    title="Detect Emergency Signals | 응급 신호 감지",
    description=(
        "현재 위급 증상이나 장기 무응답을 보수적으로 판정할 때 호출합니다. Conservatively evaluates current "
        "emergency language or silence for CareTalk(돌봄톡); it never contacts emergency services."
    ),
    annotations=_annotations(
        "Detect Emergency Signals | 응급 신호 감지", read_only=False, idempotent=False, open_world=True
    ),
)
def emergency_detect(
    user_id: str,
    action: Literal["detect", "silence"] = "detect",
    message: str = "",
) -> Dict[str, Any]:
    """현재 메시지의 응급 신호를 보수적으로 판정하거나 무응답 경보를 확인합니다."""
    return _tool_result("emergency_detect", locals())


@mcp.tool(
    title="Create Family Care Report | 가족 돌봄 리포트",
    description=(
        "가족이 오늘 또는 일주일 상태를 요약해 달라고 할 때 호출합니다. Creates and stores a daily or weekly "
        "family care report for CareTalk(돌봄톡) using protected AI or a deterministic fallback."
    ),
    annotations=_annotations(
        "Create Family Care Report | 가족 돌봄 리포트", read_only=False, idempotent=False, open_world=True
    ),
)
def family_report(
    senior_user_id: str,
    report_type: Literal["weekly", "daily"] = "weekly",
) -> Dict[str, Any]:
    """가족에게 전달할 주간 또는 일일 돌봄 리포트를 생성합니다."""
    return _tool_result("family_report", locals())


@mcp.tool(
    title="Render Daily Care Widget | 오늘의 돌봄 위젯",
    description="카카오 응답 화면이 명시적으로 필요할 때만 호출합니다. Renders the senior-facing daily response widget for CareTalk(돌봄톡).",
    annotations=_annotations(
        "Render Daily Care Widget | 오늘의 돌봄 위젯", read_only=True, idempotent=True, open_world=False
    ),
)
def daily_care_widget(
    user_id: str,
    nickname: str = "어르신",
    sentiment: Literal["positive", "neutral", "negative"] = "neutral",
) -> Dict[str, Any]:
    """어르신용 오늘의 돌봄 카카오 응답 위젯을 생성합니다."""
    return _tool_result("daily_care_widget", locals())


@mcp.tool(
    title="Manage Health Log | 건강 기록 관리",
    description=(
        "사용자가 혈압·혈당 등 건강 수치를 말하거나 추세를 물을 때 호출합니다. Records, queries, parses, or "
        "analyzes wellness measurements for CareTalk(돌봄톡); results are not medical diagnoses."
    ),
    annotations=_annotations(
        "Manage Health Log | 건강 기록 관리", read_only=False, idempotent=False, open_world=False
    ),
)
def health_log(
    user_id: str,
    action: Literal["log", "query", "analyze", "parse"] = "log",
    data_type: Optional[Literal["systolic", "diastolic", "blood_sugar", "weight", "temperature", "heart_rate"]] = None,
    value: Optional[float] = None,
    message: str = "",
    nickname: str = "",
    days: int = 7,
    source: Literal["manual", "device", "ocr"] = "manual",
) -> Dict[str, Any]:
    """건강 수치를 기록·조회·분석하거나 한국어 문장에서 수치를 추출합니다."""
    return _tool_result("health_log", locals())


@mcp.tool(
    title="Reminiscence Chat | 추억 회상 대화",
    description=(
        "사용자가 외로움이나 옛 추억을 이야기하며 대화를 원할 때 호출합니다. Continues a supportive "
        "reminiscence conversation for CareTalk(돌봄톡), then stores the conversation record."
    ),
    annotations=_annotations(
        "Reminiscence Chat | 추억 회상 대화", read_only=False, idempotent=False, open_world=True
    ),
)
def reminiscence_chat(
    user_id: str,
    action: Literal["chat", "suggest_topic"] = "chat",
    message: str = "",
    sentiment: Literal["positive", "neutral", "negative"] = "neutral",
    nickname: str = "",
) -> Dict[str, Any]:
    """감정 상태에 맞춰 추억 회상 대화를 이어가거나 대화 주제를 추천합니다."""
    return _tool_result("reminiscence_chat", locals())


@mcp.tool(
    title="Render Family Report Widget | 가족 리포트 위젯",
    description="가족용 카카오 카드 화면이 명시적으로 필요할 때만 호출합니다. Renders the family-facing report widget for CareTalk(돌봄톡).",
    annotations=_annotations(
        "Render Family Report Widget | 가족 리포트 위젯", read_only=True, idempotent=True, open_world=False
    ),
)
def family_report_widget(
    user_id: str,
    nickname: str = "",
    days: int = 7,
) -> Dict[str, Any]:
    """가족용 주간 돌봄 리포트 카카오 응답 위젯을 생성합니다."""
    return _tool_result("family_report_widget", locals())


@mcp.tool(
    title="Find Health Facilities | 건강시설 안내",
    description=(
        "사용자가 가까운 무료 보건 서비스를 물을 때 호출합니다. Finds clearly labeled demo public-health "
        "facilities for CareTalk(돌봄톡); the bundled dataset does not make a live reservation."
    ),
    annotations=_annotations(
        "Find Health Facilities | 건강시설 안내", read_only=True, idempotent=True, open_world=False
    ),
)
def health_facility(
    user_id: str,
    action: Literal["search", "programs", "recommend", "notify"] = "search",
    region: str = "",
    facility_type: Optional[Literal["보건소", "치매안심센터"]] = None,
    nickname: str = "어르신",
    days: int = 14,
) -> Dict[str, Any]:
    """데모 데이터에서 지역 건강시설과 무료 프로그램을 찾아 안내합니다."""
    return _tool_result("health_facility", locals())


@mcp.tool(
    title="Build a Consent-First Safety Plan | 돌봄 안전계획",
    description=(
        "안부 확인을 시작하기 전 또는 가족이 돌봄 방식을 정하고 싶을 때 우선 호출합니다. Builds a consent-first, "
        "accessible, human-in-the-loop safety-plan draft for CareTalk(돌봄톡) without storing contacts or sending alerts."
    ),
    annotations=_annotations(
        "Build a Consent-First Safety Plan | 돌봄 안전계획", read_only=True, idempotent=True, open_world=False
    ),
)
def build_care_safety_plan(
    user_id: str,
    nickname: str = "어르신",
    checkin_time: str = "09:00",
    response_window_hours: int = 2,
    contact_roles: str = "가족, 복지사",
    accessibility_needs: str = "",
    senior_consented: bool = False,
) -> Dict[str, Any]:
    """당사자 동의와 단계적 사람 확인을 반영한 비실행형 안전계획 초안을 만듭니다."""
    return _tool_result("build_care_safety_plan", locals())


def _server_info() -> Dict[str, Any]:
    openai_ready = _env_configured("OPENAI_API_KEY")
    if MOCK_MODE:
        mode = "mock"
    elif not live_api_enabled():
        mode = "safe_fallback"
    elif openai_ready:
        mode = "live"
    else:
        mode = "rules_fallback"
    from services.care_worker import care_worker_status

    worker_enabled = _env_bool("CARE_WORKER_ENABLED", True)
    return {
        "server": "caretalk",
        "version": "3.3.0",
        "status": "ok",
        "mode": mode,
        "mock_mode": MOCK_MODE,
        "live_api_enabled": live_api_enabled(),
        "tools": [item["name"] for item in TOOL_DEFINITIONS],
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "api_keys": _api_key_status(),
        "worker": care_worker_status(DB_PATH, enabled=worker_enabled),
        "device_bridge": {
            "pairing_endpoint": "/device/pair",
            "activity_endpoint": "/device/activity",
            "health_endpoint": "/device/health",
            "token_storage": "sha256_hash_only",
            "exact_location_collected": False,
            "screen_content_collected": False,
        },
    }


@mcp.custom_route("/", methods=["GET"])
async def root_status(_request: Request) -> JSONResponse:
    return JSONResponse(_server_info())


@mcp.custom_route("/health", methods=["GET"])
async def health_status(_request: Request) -> JSONResponse:
    return JSONResponse(_server_info())


_MAX_DEVICE_BODY_BYTES = 16 * 1024


def _device_response(data: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        data,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


async def _read_device_json(request: Request) -> tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return None, _device_response({"error": "Content-Type은 application/json이어야 합니다."}, 415)
    content_length = request.headers.get("content-length", "")
    if content_length:
        try:
            parsed_length = int(content_length)
            if parsed_length < 0:
                return None, _device_response({"error": "올바르지 않은 Content-Length입니다."}, 400)
            if parsed_length > _MAX_DEVICE_BODY_BYTES:
                return None, _device_response({"error": "요청 본문이 너무 큽니다."}, 413)
        except ValueError:
            return None, _device_response({"error": "올바르지 않은 Content-Length입니다."}, 400)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_DEVICE_BODY_BYTES:
            return None, _device_response({"error": "요청 본문이 너무 큽니다."}, 413)
    try:
        data = json.loads(bytes(body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _device_response({"error": "올바른 JSON 객체를 보내 주세요."}, 400)
    if not isinstance(data, dict):
        return None, _device_response({"error": "JSON 본문은 객체여야 합니다."}, 400)
    return data, None


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        from services.device_bridge import DeviceBridgeError

        raise DeviceBridgeError("Authorization Bearer 기기 토큰이 필요합니다.", 401)
    return token.strip()


@mcp.custom_route("/device/pair", methods=["POST"])
async def pair_device(request: Request) -> JSONResponse:
    from services.device_bridge import DeviceBridgeError, exchange_device_pairing

    data, error_response = await _read_device_json(request)
    if error_response is not None:
        return error_response
    try:
        result = exchange_device_pairing(
            str((data or {}).get("pairing_code", "")),
            db_path=DB_PATH,
        )
        return _device_response(result, 201)
    except DeviceBridgeError as exc:
        return _device_response({"error": str(exc)}, exc.status_code)


@mcp.custom_route("/device/activity", methods=["POST"])
async def receive_device_activity(request: Request) -> JSONResponse:
    from services.device_bridge import DeviceBridgeError, ingest_device_activity

    data, error_response = await _read_device_json(request)
    if error_response is not None:
        return error_response
    try:
        result = ingest_device_activity(
            _bearer_token(request),
            str((data or {}).get("event_type", "")),
            occurred_at=str((data or {}).get("occurred_at", "")),
            event_id=str((data or {}).get("event_id", "")),
            db_path=DB_PATH,
        )
        return _device_response(result)
    except DeviceBridgeError as exc:
        return _device_response({"error": str(exc)}, exc.status_code)


@mcp.custom_route("/device/health", methods=["POST"])
async def receive_device_health(request: Request) -> JSONResponse:
    from services.device_bridge import DeviceBridgeError, ingest_device_health

    data, error_response = await _read_device_json(request)
    if error_response is not None:
        return error_response
    try:
        result = ingest_device_health(
            _bearer_token(request),
            str((data or {}).get("event_id", "")),
            str((data or {}).get("data_type", "")),
            (data or {}).get("value"),
            occurred_at=str((data or {}).get("occurred_at", "")),
            db_path=DB_PATH,
        )
        response_status = {
            "duplicate_ignored": 200,
            "processing": 202,
        }.get(str(result.get("status")), 201)
        return _device_response(result, response_status)
    except DeviceBridgeError as exc:
        return _device_response({"error": str(exc)}, exc.status_code)

def parse_args():
    parser = argparse.ArgumentParser(description="돌봄톡(CareTalk) MCP 서버")
    parser.add_argument("--port", type=int, default=_env_port())
    parser.add_argument("--host", type=str, default=os.environ.get("MCP_HOST", "0.0.0.0"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mock", dest="mock", action="store_true")
    mode.add_argument("--live", dest="mock", action="store_false")
    parser.set_defaults(mock=None)
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    if args.mock is True:
        set_mock_mode(True)
    elif args.mock is False:
        set_mock_mode(False)
        os.environ["LIVE_API_ENABLED"] = "true"
    else:
        set_mock_mode(_env_bool("MOCK_MODE", True))
    # 시작 시 스키마 보장 (테이블 없으면 생성) — 단일 진실원천: db/schema.py
    from db.schema import ensure_schema
    ensure_schema(DB_PATH)
    if _env_bool("CARE_WORKER_ENABLED", True):
        from services.care_worker import start_care_worker

        start_care_worker(DB_PATH)
        logger.info("예약·전달 worker 시작")
    else:
        logger.info("예약·전달 worker 비활성화")
    if args.init_db:
        logger.info("DB 초기화(스키마 보장) 완료: " + DB_PATH)
    logger.info("돌봄톡 MCP 서버 시작 - http://%s:%s/mcp", args.host, args.port)
    logger.info("실행 모드: %s", _server_info()["mode"])
    logger.info("등록된 Tool: " + str([t["name"] for t in TOOL_DEFINITIONS]))
    import uvicorn

    uvicorn.run(mcp.streamable_http_app(), host=args.host, port=args.port, log_level="debug" if args.debug else "info")

if __name__ == "__main__":
    main()
