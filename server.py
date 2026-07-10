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
from starlette.requests import Request
from starlette.responses import JSONResponse

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
    ):
        if _is_placeholder_value(os.environ.get(key)):
            os.environ.pop(key, None)


def _env_configured(key: str) -> bool:
    value = os.environ.get(key, "").strip()
    return bool(value) and not _is_placeholder_value(value)


def _api_key_status() -> Dict[str, Dict[str, Any]]:
    return {
        "openai": {"env": "OPENAI_API_KEY", "configured": _env_configured("OPENAI_API_KEY")},
        "kakao_rest": {"env": "KAKAO_REST_API_KEY", "configured": _env_configured("KAKAO_REST_API_KEY")},
        "kakao_client_secret": {"env": "KAKAO_CLIENT_SECRET", "configured": _env_configured("KAKAO_CLIENT_SECRET")},
        "kakao_biz": {"env": "KAKAO_BIZ_API_KEY", "configured": _env_configured("KAKAO_BIZ_API_KEY")},
        "kakao_sender": {"env": "KAKAO_SENDER_KEY", "configured": _env_configured("KAKAO_SENDER_KEY")},
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


DB_PATH = _resolve_db_path()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("caretalk-server")

MOCK_MODE = _env_bool("MOCK_MODE")

def set_mock_mode(enabled: bool):
    global MOCK_MODE
    MOCK_MODE = bool(enabled)

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
    {"name": "daily_checkin", "description": "매일 안부 확인. action: initiate(안부 메시지 생성), analyze(응답 감정 분석), no_response(무응답 확인)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["initiate", "analyze", "no_response"]}, "message": {"type": "string"}, "nickname": {"type": "string"}}, "required": ["user_id"]}},
    {"name": "emergency_detect", "description": "위험 신호 실시간 감지. action: detect(메시지 위험 판정), silence(무응답 경보)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "message": {"type": "string"}, "action": {"type": "string", "enum": ["detect", "silence"]}}, "required": ["user_id"]}},
    {"name": "family_report", "description": "가족용 주간/일일 돌봄 리포트 생성. report_type: weekly(주간), daily(일일)", "inputSchema": {"type": "object", "properties": {"senior_user_id": {"type": "string"}, "report_type": {"type": "string", "enum": ["weekly", "daily"]}}, "required": ["senior_user_id"]}},
    {"name": "daily_care_widget", "description": "노인용 '오늘의 돌봄' Widget A 렌더 (SimpleText + quickReplies)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}}, "required": ["user_id"]}},
    {"name": "health_log", "description": "건강 데이터(혈압·혈당·체중·체온·맥박) 기록 및 추세 분석. action: log(기록), query(조회), analyze(추세 분석), parse(자연어 파싱 기록). source로 입력 경로(직접/기기/사진) 구분", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["log", "query", "analyze", "parse"]}, "data_type": {"type": "string", "enum": ["systolic", "diastolic", "blood_sugar", "weight", "temperature", "heart_rate"]}, "value": {"type": "number"}, "message": {"type": "string"}, "nickname": {"type": "string"}, "days": {"type": "integer"}, "source": {"type": "string", "enum": ["manual", "device", "ocr"], "description": "입력 경로: manual=직접 입력, device=혈압계 등 기기 연동, ocr=측정기 사진 판독"}}, "required": ["user_id"]}},
    {"name": "reminiscence_chat", "description": "추억 회상 기반 정서 지원 대화. action: chat(대화 응답), suggest_topic(주제 추천)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["chat", "suggest_topic"]}, "message": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}, "nickname": {"type": "string"}}, "required": ["user_id"]}},
    {"name": "family_report_widget", "description": "가족용 '주간 돌봄 리포트' Widget B 렌더 (BasicCard + ListCard)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "days": {"type": "integer"}}, "required": ["user_id"]}},
    {"name": "health_facility", "description": "어르신 무료 건강 서비스(보건소·치매안심센터) 안내. action: search(지역 검색), programs(무료 프로그램 목록), recommend(건강 기록 기반 맞춤 추천), notify(알림 메시지 생성)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["search", "programs", "recommend", "notify"]}, "region": {"type": "string", "description": "지역명 일부 (예: 마포, 수원)"}, "facility_type": {"type": "string", "enum": ["보건소", "치매안심센터"]}, "nickname": {"type": "string"}, "days": {"type": "integer"}}, "required": ["user_id"]}}
]

_TEXT_LIMITS = {
    "user_id": 128,
    "senior_user_id": 128,
    "nickname": 40,
    "message": 4000,
    "region": 80,
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

    if name == "daily_checkin":
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
        "응급 판정은 보조 신호이며 실제 위급 상황에서는 즉시 119에 연락해야 합니다."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
)


@mcp.tool()
def daily_checkin(
    user_id: str,
    action: Literal["initiate", "analyze", "no_response"] = "initiate",
    message: str = "",
    nickname: str = "",
) -> Dict[str, Any]:
    """매일 안부를 시작하거나 응답을 분석하고 장기 무응답 여부를 확인합니다."""
    return _tool_result("daily_checkin", locals())


@mcp.tool()
def emergency_detect(
    user_id: str,
    action: Literal["detect", "silence"] = "detect",
    message: str = "",
) -> Dict[str, Any]:
    """현재 메시지의 응급 신호를 보수적으로 판정하거나 무응답 경보를 확인합니다."""
    return _tool_result("emergency_detect", locals())


@mcp.tool()
def family_report(
    senior_user_id: str,
    report_type: Literal["weekly", "daily"] = "weekly",
) -> Dict[str, Any]:
    """가족에게 전달할 주간 또는 일일 돌봄 리포트를 생성합니다."""
    return _tool_result("family_report", locals())


@mcp.tool()
def daily_care_widget(
    user_id: str,
    nickname: str = "어르신",
    sentiment: Literal["positive", "neutral", "negative"] = "neutral",
) -> Dict[str, Any]:
    """어르신용 오늘의 돌봄 카카오 응답 위젯을 생성합니다."""
    return _tool_result("daily_care_widget", locals())


@mcp.tool()
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


@mcp.tool()
def reminiscence_chat(
    user_id: str,
    action: Literal["chat", "suggest_topic"] = "chat",
    message: str = "",
    sentiment: Literal["positive", "neutral", "negative"] = "neutral",
    nickname: str = "",
) -> Dict[str, Any]:
    """감정 상태에 맞춰 추억 회상 대화를 이어가거나 대화 주제를 추천합니다."""
    return _tool_result("reminiscence_chat", locals())


@mcp.tool()
def family_report_widget(
    user_id: str,
    nickname: str = "",
    days: int = 7,
) -> Dict[str, Any]:
    """가족용 주간 돌봄 리포트 카카오 응답 위젯을 생성합니다."""
    return _tool_result("family_report_widget", locals())


@mcp.tool()
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


def _server_info() -> Dict[str, Any]:
    openai_ready = _env_configured("OPENAI_API_KEY")
    return {
        "server": "caretalk",
        "version": "2.2.0",
        "status": "ok",
        "mode": "mock" if MOCK_MODE else ("live" if openai_ready else "rules_fallback"),
        "mock_mode": MOCK_MODE,
        "tools": [item["name"] for item in TOOL_DEFINITIONS],
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "api_keys": _api_key_status(),
    }


@mcp.custom_route("/", methods=["GET"])
async def root_status(_request: Request) -> JSONResponse:
    return JSONResponse(_server_info())


@mcp.custom_route("/health", methods=["GET"])
async def health_status(_request: Request) -> JSONResponse:
    return JSONResponse(_server_info())

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
    set_mock_mode(_env_bool("MOCK_MODE") if args.mock is None else args.mock)
    # 시작 시 스키마 보장 (테이블 없으면 생성) — 단일 진실원천: db/schema.py
    from db.schema import ensure_schema
    ensure_schema(DB_PATH)
    if args.init_db:
        logger.info("DB 초기화(스키마 보장) 완료: " + DB_PATH)
    logger.info("돌봄톡 MCP 서버 시작 - http://%s:%s/mcp", args.host, args.port)
    logger.info("실행 모드: %s", _server_info()["mode"])
    logger.info("등록된 Tool: " + str([t["name"] for t in TOOL_DEFINITIONS]))
    import uvicorn

    uvicorn.run(mcp.streamable_http_app(), host=args.host, port=args.port, log_level="debug" if args.debug else "info")

if __name__ == "__main__":
    main()
