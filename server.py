# -*- coding: utf-8 -*-
"""
돌봄톡 (CareTalk) - MCP 서버 메인 엔트리포인트
순수 Python http.server 기반 MCP JSON-RPC 2.0 서버.
"""
import sys, os, argparse, json, logging, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
DB_PATH = os.path.join(PROJECT_ROOT, "db", "caretalk.db")


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
    "sk-your-openai-api-key-here",
    "your-kakao-rest-api-key-here",
    "your-kakao-client-secret-here",
    "your-kakao-biz-api-key-here",
    "your-kakao-sender-key-here",
}


def _is_placeholder_value(value: Optional[str]) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    return stripped in _PLACEHOLDER_ENV_VALUES or stripped.startswith("your-")


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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("caretalk-server")

MOCK_MODE = False

def set_mock_mode(enabled: bool):
    global MOCK_MODE
    MOCK_MODE = enabled

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
    {"name": "daily_checkin", "description": "매일 안부 확인. action: initiate(안부 메시지 발송), analyze(응답 감정 분석), no_response(무응답 확인)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["initiate", "analyze", "no_response"]}, "message": {"type": "string"}, "nickname": {"type": "string"}}, "required": ["user_id"]}},
    {"name": "emergency_detect", "description": "위험 신호 실시간 감지. action: detect(메시지 위험 판정), silence(무응답 경보)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "message": {"type": "string"}, "action": {"type": "string", "enum": ["detect", "silence"]}}, "required": ["user_id"]}},
    {"name": "family_report", "description": "가족용 주간/일일 돌봄 리포트 생성. report_type: weekly(주간), daily(일일)", "inputSchema": {"type": "object", "properties": {"senior_user_id": {"type": "string"}, "report_type": {"type": "string", "enum": ["weekly", "daily"]}}, "required": ["senior_user_id"]}},
    {"name": "daily_care_widget", "description": "노인용 '오늘의 돌봄' Widget A 렌더 (SimpleText + quickReplies)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}}, "required": ["user_id"]}},
    {"name": "health_log", "description": "건강 데이터(혈압·혈당·체중·체온·맥박) 기록 및 추세 분석. action: log(기록), query(조회), analyze(추세 분석), parse(자연어 파싱 기록). source로 입력 경로(직접/기기/사진) 구분", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["log", "query", "analyze", "parse"]}, "data_type": {"type": "string", "enum": ["systolic", "diastolic", "blood_sugar", "weight", "temperature", "heart_rate"]}, "value": {"type": "number"}, "message": {"type": "string"}, "nickname": {"type": "string"}, "days": {"type": "integer"}, "source": {"type": "string", "enum": ["manual", "device", "ocr"], "description": "입력 경로: manual=직접 입력, device=혈압계 등 기기 연동, ocr=측정기 사진 판독"}}, "required": ["user_id"]}},
    {"name": "reminiscence_chat", "description": "추억 회상 기반 정서 지원 대화. action: chat(대화 응답), suggest_topic(주제 추천)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["chat", "suggest_topic"]}, "message": {"type": "string"}, "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]}, "nickname": {"type": "string"}}, "required": ["user_id"]}},
    {"name": "family_report_widget", "description": "가족용 '주간 돌봄 리포트' Widget B 렌더 (BasicCard + ListCard)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "nickname": {"type": "string"}, "days": {"type": "integer"}}, "required": ["user_id"]}},
    {"name": "health_facility", "description": "어르신 무료 건강 서비스(보건소·치매안심센터) 안내. action: search(지역 검색), programs(무료 프로그램 목록), recommend(건강 기록 기반 맞춤 추천), notify(알림 메시지 생성)", "inputSchema": {"type": "object", "properties": {"user_id": {"type": "string"}, "action": {"type": "string", "enum": ["search", "programs", "recommend", "notify"]}, "region": {"type": "string", "description": "지역명 일부 (예: 마포, 수원)"}, "facility_type": {"type": "string", "enum": ["보건소", "치매안심센터"]}, "nickname": {"type": "string"}, "days": {"type": "integer"}}, "required": ["user_id"]}}
]

def _require_arg(arguments: Dict[str, Any], key: str):
    value = arguments.get(key)
    if value in (None, ""):
        return None, {"error": key + "는 필수입니다."}
    return value, None

def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
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
            return search_facilities(region, arguments.get("facility_type", ""))
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

def handle_jsonrpc(request: Dict[str, Any]) -> Dict[str, Any]:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})
    response = {"jsonrpc": "2.0", "id": req_id}
    try:
        if method == "initialize":
            response["result"] = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "caretalk", "version": "1.0.0"}}
        elif method == "tools/list":
            response["result"] = {"tools": TOOL_DEFINITIONS}
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            logger.info("tools/call: " + tool_name + "(" + json.dumps(arguments, ensure_ascii=False)[:100] + ")")
            result = execute_tool(tool_name, arguments)
            tool_result = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]}
            # MCP 표준: tool 실행 오류는 isError 플래그로 표시
            if isinstance(result, dict) and "error" in result:
                tool_result["isError"] = True
            response["result"] = tool_result
        elif method == "notifications/initialized":
            return {}
        else:
            response["error"] = {"code": -32601, "message": "Method not found: " + method}
    except Exception as e:
        logger.error("Tool error: " + traceback.format_exc())
        response["error"] = {"code": -32603, "message": "Internal error: " + str(e)}
    return response

class MCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path not in ("/mcp", "/"):
            self.send_response(404); self.end_headers(); self.wfile.write(b'{"error":"Not Found"}'); return
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            request = json.loads(body.decode("utf-8"))
            if isinstance(request, list):
                responses = []
                for r in request:
                    resp = handle_jsonrpc(r)
                    if resp:  # notifications/initialized → 빈 dict → 건너뜀
                        responses.append(resp)
                response_body = json.dumps(responses, ensure_ascii=False)
            else:
                resp = handle_jsonrpc(request)
                if not resp:
                    self.send_response(204); self.end_headers(); return
                response_body = json.dumps(resp, ensure_ascii=False)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(response_body.encode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"}}')
        except Exception as e:
            logger.error("HTTP error: " + traceback.format_exc())
            self.send_response(500); self.end_headers()
            self.wfile.write(('{"jsonrpc":"2.0","error":{"code":-32603,"message":"' + str(e) + '"}}').encode("utf-8"))
    def do_OPTIONS(self):
        self.send_response(204); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()
    def do_GET(self):
        if self.path == "/":
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            info = {"server": "caretalk", "version": "1.0.0", "mock_mode": MOCK_MODE, "tools": [t["name"] for t in TOOL_DEFINITIONS], "endpoint": "/mcp", "api_keys": _api_key_status()}
            self.wfile.write(json.dumps(info, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, format, *args):
        logger.info("HTTP " + str(args[0]) + " " + str(args[1]) + " " + str(args[2]))

def parse_args():
    parser = argparse.ArgumentParser(description="돌봄톡(CareTalk) MCP 서버")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()

def main():
    args = parse_args()
    if 8000 <= args.port <= 8999:
        print("오류: 포트 " + str(args.port) + "는 8000번대입니다. 9000번대를 사용하세요.")
        sys.exit(1)
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    set_mock_mode(args.mock)
    # 시작 시 스키마 보장 (테이블 없으면 생성) — 단일 진실원천: db/schema.py
    from db.schema import ensure_schema
    ensure_schema(DB_PATH)
    if args.init_db:
        logger.info("DB 초기화(스키마 보장) 완료: " + DB_PATH)
    # ThreadingHTTPServer: 느린 요청 하나가 전체를 블로킹하지 않게 멀티스레드 처리
    server = ThreadingHTTPServer((args.host, args.port), MCPHandler)
    logger.info("돌봄톡 MCP 서버 시작 - http://" + args.host + ":" + str(args.port))
    logger.info("Mock 모드: " + ("ON" if args.mock else "OFF"))
    logger.info("등록된 Tool: " + str([t["name"] for t in TOOL_DEFINITIONS]))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("서버 종료")
        server.shutdown()

if __name__ == "__main__":
    main()
