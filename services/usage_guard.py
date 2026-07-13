"""공개 MCP 배포에서 유료 외부 API 호출량과 비밀값 노출을 제한한다."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DB_LOCK = threading.RLock()
_STATE_LOCK = threading.Lock()
_RATE_WINDOW: deque[float] = deque()
_OPENAI_INFLIGHT = 0


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def live_api_enabled() -> bool:
    """실시간 API는 Mock 해제와 별도 opt-in이 모두 있어야 활성화된다."""
    return not env_bool("MOCK_MODE", True) and env_bool("LIVE_API_ENABLED", False)


def openai_timeout() -> float:
    """PlayMCP p99 3초 조건을 위해 네트워크 대기를 최대 2.5초로 제한한다."""
    try:
        return min(2.5, max(0.5, float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "2.2"))))
    except (TypeError, ValueError):
        return 2.2


def max_output_tokens(requested: int) -> int:
    try:
        configured = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "800"))
    except (TypeError, ValueError):
        configured = 800
    return min(max(32, requested), max(32, min(configured, 1200)))


_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|client[_-]?secret)=)([^&\s'\"<>]+)"
)
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)")


def redact_secrets(value: Any, limit: int = 500) -> str:
    text = _SECRET_RE.sub(r"\1[REDACTED]", str(value))
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    for env_name in (
        "OPENAI_API_KEY",
        "KAKAO_REST_API_KEY",
        "KAKAO_CLIENT_SECRET",
        "KAKAO_BIZ_API_KEY",
        "KAKAO_SENDER_KEY",
    ):
        secret = os.environ.get(env_name, "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[:limit]


def _usage_db_path() -> Path:
    configured = (
        os.environ.get("CARETALK_USAGE_DB_PATH", "").strip()
        or os.environ.get("CARETALK_DB_PATH", "").strip()
    )
    path = Path(configured).expanduser() if configured else _PROJECT_ROOT / "db" / "caretalk.db"
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def _today_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _daily_limit() -> int:
    try:
        return max(1, int(os.environ.get("OPENAI_DAILY_LIMIT", "100")))
    except (TypeError, ValueError):
        return 100


def _minute_limit() -> int:
    try:
        return max(1, int(os.environ.get("OPENAI_RATE_LIMIT_PER_MINUTE", "10")))
    except (TypeError, ValueError):
        return 10


def _concurrency_limit() -> int:
    try:
        return max(1, int(os.environ.get("OPENAI_MAX_CONCURRENCY", "2")))
    except (TypeError, ValueError):
        return 2


def _ensure_usage_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS api_daily_usage (
               usage_date TEXT NOT NULL,
               scope TEXT NOT NULL,
               call_count INTEGER NOT NULL,
               PRIMARY KEY (usage_date, scope)
           )"""
    )


def _consume_daily_quota() -> str | None:
    if not env_bool("DAILY_QUOTA_ENABLED", True):
        return None
    db_path = _usage_db_path()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with _DB_LOCK, sqlite3.connect(str(db_path), timeout=0.15) as conn:
            conn.execute("PRAGMA busy_timeout = 150")
            _ensure_usage_table(conn)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            today = _today_kst()
            row = conn.execute(
                "SELECT call_count FROM api_daily_usage WHERE usage_date = ? AND scope = 'openai'",
                (today,),
            ).fetchone()
            used = int(row[0]) if row else 0
            limit = _daily_limit()
            if used >= limit:
                conn.rollback()
                return f"오늘의 AI API 사용 한도에 도달했습니다. ({limit}회/일)"
            conn.execute(
                """INSERT INTO api_daily_usage (usage_date, scope, call_count)
                   VALUES (?, 'openai', 1)
                   ON CONFLICT(usage_date, scope)
                   DO UPDATE SET call_count = call_count + 1""",
                (today,),
            )
            conn.commit()
    except (OSError, sqlite3.Error):
        return "API 사용량 보호 상태를 확인할 수 없어 실시간 호출을 중단했습니다."
    return None


def reserve_openai_call() -> str | None:
    """대기 없이 동시·분당·일일 제한을 예약하며, 거절 시 네트워크를 호출하지 않는다."""
    global _OPENAI_INFLIGHT
    if not live_api_enabled():
        return "실시간 AI API 호출이 비활성화되어 있습니다."

    now = time.monotonic()
    with _STATE_LOCK:
        while _RATE_WINDOW and now - _RATE_WINDOW[0] >= 60.0:
            _RATE_WINDOW.popleft()
        if _OPENAI_INFLIGHT >= _concurrency_limit():
            return "AI 요청이 처리 중입니다. 잠시 후 다시 시도해주세요."
        if len(_RATE_WINDOW) >= _minute_limit():
            return "AI 요청이 몰려 잠시 제한 중입니다. 약 1분 후 다시 시도해주세요."
        _OPENAI_INFLIGHT += 1
        _RATE_WINDOW.append(now)

    denied = _consume_daily_quota()
    if denied:
        release_openai_call()
        return denied
    return None


def release_openai_call() -> None:
    global _OPENAI_INFLIGHT
    with _STATE_LOCK:
        _OPENAI_INFLIGHT = max(0, _OPENAI_INFLIGHT - 1)


def reset_usage_guard() -> None:
    """테스트용으로 메모리 제한과 오늘의 OpenAI 사용량을 초기화한다."""
    global _OPENAI_INFLIGHT
    with _STATE_LOCK:
        _OPENAI_INFLIGHT = 0
        _RATE_WINDOW.clear()

    db_path = _usage_db_path()
    if not db_path.exists():
        return
    with _DB_LOCK, sqlite3.connect(str(db_path), timeout=0.15) as conn:
        _ensure_usage_table(conn)
        conn.execute(
            "DELETE FROM api_daily_usage WHERE usage_date = ? AND scope = 'openai'",
            (_today_kst(),),
        )
        conn.commit()
