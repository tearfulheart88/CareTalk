"""
돌봄톡 (CareTalk) - 데이터베이스 스키마 및 CRUD 함수
=====================================================
SQLite 기반 경량 데이터베이스로 사용자, 안부 확인, 알림, 건강 로그를 관리합니다.

테이블:
  - users: 사용자 정보 (노인/가족)
  - checkins: 일일 안부 확인 기록
  - alerts: 위험 감지 알림 기록
  - health_logs: 건강 데이터 기록 (본선 확장용)

작성일: 2026-06-21
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# 데이터베이스 파일 경로 (환경 변수로 지정 가능, 기본값은 현재 디렉토리)
DB_PATH = os.environ.get("CARETALK_DB_PATH", os.path.join(os.path.dirname(__file__), "caretalk.db"))


# ═══════════════════════════════════════════════════════════════════
# 스키마 단일 진실원천 (Single Source of Truth)
# ═══════════════════════════════════════════════════════════════════
# ⚠️ 모든 테이블 정의는 여기 한 곳에만 둔다. tools/*.py 는 각자 CREATE TABLE
#    을 만들지 말고 반드시 ensure_schema(db_path) 를 호출할 것.
#    (과거 tool마다 제각각 _ensure_tables 를 두어 스키마가 어긋났던 문제를 방지)

SCHEMA_SQL = """
-- ── users: 사용자 정보 (노인/가족 공통) ──────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,
    nickname        TEXT NOT NULL,
    family_user_id  TEXT,
    user_type       TEXT DEFAULT 'senior',
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ── checkins: 일일 안부 확인 (daily_checkin) ─────────────────────
-- daily_checkin 과 schema.py CRUD 가 함께 쓰는 상위집합(superset) 스키마.
CREATE TABLE IF NOT EXISTS checkins (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT NOT NULL,
    nickname          TEXT DEFAULT '',
    checkin_date      TEXT NOT NULL DEFAULT '',
    checkin_time      TEXT NOT NULL DEFAULT '',
    timestamp         TIMESTAMP DEFAULT (datetime('now','localtime')),
    message           TEXT,
    user_message      TEXT DEFAULT '',
    sentiment         TEXT,
    health_keywords   TEXT,
    status            TEXT DEFAULT 'normal',
    follow_up_action  TEXT DEFAULT '',
    response_received INTEGER DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now','localtime'))
);

-- ── checkin_responses: 체크인 응답 상세 (daily_checkin.analyze) ──
CREATE TABLE IF NOT EXISTS checkin_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    checkin_id      INTEGER,
    user_id         TEXT NOT NULL,
    message         TEXT DEFAULT '',
    sentiment       TEXT DEFAULT '',
    health_keywords TEXT DEFAULT '[]',
    danger_detected INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- ── emergency_logs: 위험 감지 (emergency_detect) ────────────────
-- family_report 가 가족 리포트의 위험 이력을 이 테이블에서 읽는다.
CREATE TABLE IF NOT EXISTS emergency_logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL,
    message            TEXT NOT NULL,
    risk_level         TEXT NOT NULL DEFAULT 'none',
    detected_keywords  TEXT DEFAULT '[]',
    recommended_action TEXT DEFAULT '',
    notify_targets     TEXT DEFAULT '[]',
    context_safe       INTEGER DEFAULT 0,
    mock_mode          INTEGER DEFAULT 0,
    created_at         TEXT DEFAULT (datetime('now','localtime'))
);

-- ── silence_alerts: 무응답 경보 (emergency_detect.check_silence) ─
CREATE TABLE IF NOT EXISTS silence_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    alert_level  TEXT NOT NULL DEFAULT 'yellow',
    last_activity TEXT,
    hours_silent INTEGER DEFAULT 0,
    resolved     INTEGER DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);

-- ── family_reports: 가족 리포트 생성 기록 (family_report) ────────
CREATE TABLE IF NOT EXISTS family_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    senior_user_id      TEXT NOT NULL,
    report_type         TEXT NOT NULL DEFAULT 'weekly',
    report_period_start TEXT NOT NULL,
    report_period_end   TEXT NOT NULL,
    report_json         TEXT DEFAULT '{}',
    summary_text        TEXT DEFAULT '',
    alert_items         TEXT DEFAULT '[]',
    created_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- ── alerts: 위험 알림 (schema.py CRUD 호환용, 본선 확장 여지) ────
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    timestamp    TIMESTAMP DEFAULT (datetime('now','localtime')),
    risk_level   TEXT NOT NULL,
    keywords     TEXT,
    action_taken TEXT
);

-- ── health_logs: 건강 데이터 기록 (본선 확장용) ─────────────────
-- source: 입력 경로 (manual=직접 입력, device=혈압계 등 기기 연동, ocr=사진 판독)
CREATE TABLE IF NOT EXISTS health_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL,
    timestamp    TIMESTAMP DEFAULT (datetime('now','localtime')),
    data_type    TEXT NOT NULL,
    value        TEXT NOT NULL,
    normal_range INTEGER DEFAULT 1,
    source       TEXT DEFAULT 'manual',
    source_event_id TEXT
);

-- ── care_circles: 어르신 중심의 동의 기반 돌봄 연결망 ───────────
CREATE TABLE IF NOT EXISTS care_circles (
    circle_id          TEXT PRIMARY KEY,
    senior_user_id     TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL DEFAULT '우리 가족 돌봄',
    senior_consented   INTEGER NOT NULL DEFAULT 0,
    consented_at       TEXT,
    created_at         TEXT DEFAULT (datetime('now','localtime'))
);

-- ── care_memberships: 가족·복지사 계정별 권한 ──────────────────
CREATE TABLE IF NOT EXISTS care_memberships (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    circle_id          TEXT NOT NULL,
    account_user_id    TEXT NOT NULL,
    nickname           TEXT NOT NULL DEFAULT '',
    role               TEXT NOT NULL DEFAULT 'family',
    permissions        TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL DEFAULT 'active',
    joined_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at         TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(circle_id, account_user_id)
);

-- ── care_invites: 평문을 저장하지 않는 일회용 연결 초대 ─────────
CREATE TABLE IF NOT EXISTS care_invites (
    invite_id          TEXT PRIMARY KEY,
    circle_id          TEXT NOT NULL,
    token_hash         TEXT NOT NULL UNIQUE,
    role               TEXT NOT NULL DEFAULT 'family',
    permissions        TEXT NOT NULL DEFAULT '[]',
    expires_at         TEXT NOT NULL,
    used_by            TEXT,
    used_at            TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT DEFAULT (datetime('now','localtime'))
);

-- ── care_routines: 예약 안부·가족 요약·활동 부재 기준 ───────────
CREATE TABLE IF NOT EXISTS care_routines (
    senior_user_id             TEXT PRIMARY KEY,
    timezone                   TEXT NOT NULL DEFAULT 'Asia/Seoul',
    prompt_times               TEXT NOT NULL DEFAULT '["09:00","14:00","20:00"]',
    digest_times               TEXT NOT NULL DEFAULT '["14:30","21:00"]',
    response_window_minutes    INTEGER NOT NULL DEFAULT 60,
    inactivity_hours           INTEGER NOT NULL DEFAULT 8,
    escalation_hours           INTEGER NOT NULL DEFAULT 12,
    inactivity_grace_minutes   INTEGER NOT NULL DEFAULT 30,
    inactivity_mode            TEXT NOT NULL DEFAULT 'both',
    quiet_start                TEXT NOT NULL DEFAULT '22:00',
    quiet_end                  TEXT NOT NULL DEFAULT '07:00',
    phone_activity_enabled     INTEGER NOT NULL DEFAULT 1,
    wearable_enabled           INTEGER NOT NULL DEFAULT 0,
    senior_consented           INTEGER NOT NULL DEFAULT 0,
    updated_at                 TEXT DEFAULT (datetime('now','localtime'))
);

-- ── phone_activity_events: 위치·내용 없이 마지막 활동 시각만 저장 ─
CREATE TABLE IF NOT EXISTS phone_activity_events (
    event_id           TEXT PRIMARY KEY,
    senior_user_id     TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    source             TEXT NOT NULL DEFAULT 'phone',
    occurred_at        TEXT NOT NULL,
    received_at        TEXT DEFAULT (datetime('now','localtime'))
);

-- ── care_outbox: 외부 알림 어댑터가 전달할 중복 방지 대기열 ─────
CREATE TABLE IF NOT EXISTS care_outbox (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    senior_user_id     TEXT NOT NULL,
    recipient_user_id  TEXT NOT NULL,
    event_type         TEXT NOT NULL,
    severity           TEXT NOT NULL DEFAULT 'info',
    payload_json       TEXT NOT NULL DEFAULT '{}',
    due_at             TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    dedupe_key         TEXT NOT NULL UNIQUE,
    created_at         TEXT DEFAULT (datetime('now','localtime')),
    sent_at            TEXT,
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    next_attempt_at    TEXT,
    last_error         TEXT NOT NULL DEFAULT '',
    claimed_at         TEXT,
    claim_token        TEXT,
    provider_message_id TEXT NOT NULL DEFAULT '',
    updated_at         TEXT DEFAULT (datetime('now','localtime'))
);

-- ── care_acknowledgements: 가족 알림별 확인·연락·방문 응답 ─────
CREATE TABLE IF NOT EXISTS care_acknowledgements (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id          INTEGER NOT NULL,
    senior_user_id     TEXT NOT NULL,
    responder_user_id  TEXT NOT NULL,
    response           TEXT NOT NULL,
    created_at         TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(outbox_id, responder_user_id)
);

-- ── care_device_pairings: 어르신이 만든 짧은 일회용 기기 연결 코드 ─
CREATE TABLE IF NOT EXISTS care_device_pairings (
    pairing_id         TEXT PRIMARY KEY,
    senior_user_id     TEXT NOT NULL,
    code_hash          TEXT NOT NULL UNIQUE,
    device_type        TEXT NOT NULL,
    label              TEXT NOT NULL DEFAULT '',
    expires_at         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',
    used_at            TEXT,
    created_at         TEXT DEFAULT (datetime('now','localtime'))
);

-- ── care_devices: 평문 토큰을 저장하지 않는 휴대폰·웨어러블 연결 ──
CREATE TABLE IF NOT EXISTS care_devices (
    device_id          TEXT PRIMARY KEY,
    senior_user_id     TEXT NOT NULL,
    token_hash         TEXT NOT NULL UNIQUE,
    device_type        TEXT NOT NULL,
    label              TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'active',
    paired_at          TEXT NOT NULL,
    last_seen_at       TEXT,
    revoked_at         TEXT
);

-- ── device_health_events: 기기 재전송 중복 방지와 수취 감사 ─────
CREATE TABLE IF NOT EXISTS device_health_events (
    event_id           TEXT PRIMARY KEY,
    device_id          TEXT NOT NULL,
    senior_user_id     TEXT NOT NULL,
    data_type          TEXT NOT NULL,
    value              REAL NOT NULL,
    occurred_at        TEXT NOT NULL,
    health_log_id      INTEGER,
    received_at        TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_care_memberships_account
    ON care_memberships(account_user_id, status);
CREATE INDEX IF NOT EXISTS idx_phone_activity_user_time
    ON phone_activity_events(senior_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_care_outbox_pending
    ON care_outbox(status, due_at);
CREATE INDEX IF NOT EXISTS idx_care_ack_senior
    ON care_acknowledgements(senior_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_care_devices_senior
    ON care_devices(senior_user_id, status);
CREATE INDEX IF NOT EXISTS idx_device_health_senior_time
    ON device_health_events(senior_user_id, occurred_at DESC);
"""


def ensure_schema(db_path: str = DB_PATH) -> None:
    """
    모든 테이블을 생성한다(IF NOT EXISTS). 모든 tool/서비스가 DB 접근 전에
    호출하는 **단일 스키마 보장 함수**. 여러 번 호출해도 안전(idempotent).

    의도적으로 외래 키(PRAGMA foreign_keys)를 켜지 않는다 — tool 들이 raw
    sqlite3 연결로 자유롭게 INSERT 하므로, FK 강제는 데모 흐름을 깨뜨릴 수 있다.

    Args:
        db_path: SQLite 데이터베이스 파일 경로
    """
    # DB 파일이 들어갈 디렉토리가 없으면 생성
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=1.0)
    try:
        conn.executescript(SCHEMA_SQL)
        # 마이그레이션: 기존 DB의 health_logs에 source 컬럼이 없으면 추가
        try:
            conn.execute("ALTER TABLE health_logs ADD COLUMN source TEXT DEFAULT 'manual'")
        except sqlite3.OperationalError:
            pass  # 이미 존재
        try:
            conn.execute("ALTER TABLE health_logs ADD COLUMN source_event_id TEXT")
        except sqlite3.OperationalError:
            pass  # 이미 존재
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_health_logs_source_event
               ON health_logs(source_event_id)
               WHERE source_event_id IS NOT NULL"""
        )
        # 마이그레이션: 기존 care_outbox를 전달 lease·재시도 구조로 확장한다.
        outbox_columns = {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "TEXT",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "claimed_at": "TEXT",
            "claim_token": "TEXT",
            "provider_message_id": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT",
        }
        existing_outbox_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(care_outbox)").fetchall()
        }
        for column, definition in outbox_columns.items():
            if column not in existing_outbox_columns:
                conn.execute(f"ALTER TABLE care_outbox ADD COLUMN {column} {definition}")
        conn.execute(
            "UPDATE care_outbox SET updated_at = COALESCE(updated_at, created_at)"
        )
        current_index_columns = [
            row[2]
            for row in conn.execute(
                "PRAGMA index_info('idx_care_outbox_pending')"
            ).fetchall()
        ]
        desired_index_columns = ["status", "next_attempt_at", "due_at"]
        if current_index_columns != desired_index_columns:
            conn.execute("DROP INDEX IF EXISTS idx_care_outbox_pending")
            conn.execute(
                """CREATE INDEX idx_care_outbox_pending
                   ON care_outbox(status, next_attempt_at, due_at)"""
            )
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """
    데이터베이스를 초기화하고 열린 연결을 반환한다.
    테이블 정의는 ensure_schema()(SCHEMA_SQL)에 단일화되어 있다.

    Args:
        db_path: SQLite 데이터베이스 파일 경로

    Returns:
        sqlite3.Connection: WAL 모드로 열린 데이터베이스 연결 객체
    """
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=1.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # 동시성 향상
    return conn


# ═══════════════════════════════════════════════════════════════════
# CRUD 함수
# ═══════════════════════════════════════════════════════════════════

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """데이터베이스 연결을 반환합니다. 연결이 필요할 때마다 호출."""
    conn = sqlite3.connect(db_path, timeout=1.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row  # 결과를 dict-like Row 객체로 반환
    return conn


# ── 사용자 CRUD ───────────────────────────────────────────────────

def add_user(
    user_id: str,
    nickname: str,
    family_user_id: Optional[str] = None,
    user_type: str = "senior",
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    새 사용자를 users 테이블에 추가합니다.

    Args:
        user_id: 카카오 채널 사용자 키 (고유 식별자)
        nickname: 사용자 닉네임
        family_user_id: 연결된 가족 사용자 ID (선택)
        user_type: 'senior' 또는 'family'
        db_path: 데이터베이스 경로

    Returns:
        생성된 사용자 정보 딕셔너리
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO users (user_id, nickname, family_user_id, user_type)
               VALUES (?, ?, ?, ?)""",
            (user_id, nickname, family_user_id, user_type)
        )
        conn.commit()
        return {
            "user_id": user_id,
            "nickname": nickname,
            "family_user_id": family_user_id,
            "user_type": user_type
        }
    except sqlite3.IntegrityError:
        # 이미 존재하는 사용자 → 업데이트
        conn.execute(
            """UPDATE users SET nickname=?, family_user_id=?, user_type=?
               WHERE user_id=?""",
            (nickname, family_user_id, user_type, user_id)
        )
        conn.commit()
        return {
            "user_id": user_id,
            "nickname": nickname,
            "family_user_id": family_user_id,
            "user_type": user_type
        }
    finally:
        conn.close()


def get_user(user_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """
    사용자 ID로 사용자 정보를 조회합니다.

    Args:
        user_id: 조회할 사용자 ID
        db_path: 데이터베이스 경로

    Returns:
        사용자 정보 딕셔너리 또는 None (존재하지 않는 경우)
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def get_users_by_family(family_user_id: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    가족 사용자 ID로 연결된 모든 노인 사용자를 조회합니다.

    Args:
        family_user_id: 가족 사용자 ID
        db_path: 데이터베이스 경로

    Returns:
        연결된 사용자 목록
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM users WHERE family_user_id = ?",
            (family_user_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ── 안부 확인 (Check-in) CRUD ─────────────────────────────────────

def add_checkin(
    user_id: str,
    message: Optional[str] = None,
    sentiment: Optional[str] = None,
    health_keywords: Optional[List[str]] = None,
    status: str = "normal",
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    안부 확인 기록을 checkins 테이블에 추가합니다.

    Args:
        user_id: 사용자 ID
        message: 사용자 응답 메시지 (무응답 시 None)
        sentiment: 감정 분석 결과 (positive / neutral / negative)
        health_keywords: 건강 키워드 리스트
        status: normal / concern / no_response
        db_path: 데이터베이스 경로

    Returns:
        생성된 체크인 기록 딕셔너리
    """
    conn = get_connection(db_path)
    try:
        keywords_json = json.dumps(health_keywords, ensure_ascii=False) if health_keywords else None
        cursor = conn.execute(
            """INSERT INTO checkins (user_id, message, sentiment, health_keywords, status)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, message, sentiment, keywords_json, status)
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "user_id": user_id,
            "message": message,
            "sentiment": sentiment,
            "health_keywords": health_keywords,
            "status": status
        }
    finally:
        conn.close()


def get_checkins_by_user(
    user_id: str,
    days: int = 7,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    특정 사용자의 최근 N일간 안부 확인 기록을 조회합니다.

    Args:
        user_id: 사용자 ID
        days: 조회할 기간 (일 단위, 기본 7일)
        db_path: 데이터베이스 경로

    Returns:
        체크인 기록 리스트 (최신순)
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM checkins
               WHERE user_id = ?
                 AND timestamp >= datetime('now', 'localtime', ? || ' days')
               ORDER BY timestamp DESC""",
            (user_id, f"-{days}")
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            # JSON 문자열을 리스트로 변환
            if d.get("health_keywords"):
                try:
                    d["health_keywords"] = json.loads(d["health_keywords"])
                except (json.JSONDecodeError, TypeError):
                    d["health_keywords"] = []
            else:
                d["health_keywords"] = []
            results.append(d)
        return results
    finally:
        conn.close()


def get_checkin_stats(
    user_id: str,
    days: int = 7,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    특정 사용자의 N일간 안부 확인 통계를 집계합니다.
    family_report Tool에서 사용됩니다.

    Args:
        user_id: 사용자 ID
        days: 집계 기간 (기본 7일)
        db_path: 데이터베이스 경로

    Returns:
        통계 딕셔너리:
        - total_checkins: 총 체크인 수
        - response_rate: 응답률 (%)
        - sentiment_distribution: 감정 분포 {positive: N, neutral: N, negative: N}
        - top_keywords: 가장 많이 언급된 건강 키워드
        - concern_days: concern 상태였던 날짜 목록
    """
    conn = get_connection(db_path)
    try:
        # 총 체크인 일수 (하루 여러 번 initiate해도 1일로 집계 — 응답률 왜곡 방지)
        total = conn.execute(
            """SELECT COUNT(DISTINCT COALESCE(NULLIF(checkin_date, ''), date(timestamp))) as cnt
               FROM checkins
               WHERE user_id = ? AND timestamp >= datetime('now', 'localtime', ? || ' days')""",
            (user_id, f"-{days}")
        ).fetchone()["cnt"]

        # 응답한 일수. 과거 버전은 user_message/response_received만 채운 기록이 있어 함께 본다.
        responded = conn.execute(
            """SELECT COUNT(DISTINCT COALESCE(NULLIF(checkin_date, ''), date(timestamp))) as cnt
               FROM checkins
               WHERE user_id = ? AND timestamp >= datetime('now', 'localtime', ? || ' days')
                 AND (
                    response_received = 1
                    OR message IS NOT NULL
                    OR user_message != ''
                 )""",
            (user_id, f"-{days}")
        ).fetchone()["cnt"]

        # 감정 분포
        sentiment_rows = conn.execute(
            """SELECT sentiment, COUNT(*) as cnt FROM checkins
               WHERE user_id = ? AND timestamp >= datetime('now', 'localtime', ? || ' days')
                 AND sentiment IS NOT NULL
               GROUP BY sentiment""",
            (user_id, f"-{days}")
        ).fetchall()
        sentiment_dist = {"positive": 0, "neutral": 0, "negative": 0}
        for row in sentiment_rows:
            if row["sentiment"] in sentiment_dist:
                sentiment_dist[row["sentiment"]] = row["cnt"]

        # concern 상태 날짜
        concern_rows = conn.execute(
            """SELECT timestamp, message FROM checkins
               WHERE user_id = ? AND timestamp >= datetime('now', 'localtime', ? || ' days')
                 AND status = 'concern'
               ORDER BY timestamp DESC""",
            (user_id, f"-{days}")
        ).fetchall()
        concern_days = [{"timestamp": row["timestamp"], "message": row["message"]} for row in concern_rows]

        # 건강 키워드 집계
        all_checkins = conn.execute(
            """SELECT health_keywords FROM checkins
               WHERE user_id = ? AND timestamp >= datetime('now', 'localtime', ? || ' days')
                 AND health_keywords IS NOT NULL""",
            (user_id, f"-{days}")
        ).fetchall()
        keyword_counts: Dict[str, int] = {}
        for row in all_checkins:
            try:
                keywords = json.loads(row["health_keywords"])
                for kw in keywords:
                    keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total_checkins": total,
            "response_rate": round((responded / total * 100) if total > 0 else 0, 1),
            "sentiment_distribution": sentiment_dist,
            "top_keywords": [{"keyword": kw, "count": cnt} for kw, cnt in top_keywords],
            "concern_days": concern_days
        }
    finally:
        conn.close()


# ── 알림 (Alert) CRUD ─────────────────────────────────────────────

def add_alert(
    user_id: str,
    risk_level: str,
    keywords: Optional[List[str]] = None,
    action_taken: Optional[str] = None,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    위험 감지 알림을 alerts 테이블에 추가합니다.

    Args:
        user_id: 사용자 ID
        risk_level: 위험 레벨 (none / yellow / red)
        keywords: 감지된 위험 키워드 리스트
        action_taken: 수행된 조치 설명
        db_path: 데이터베이스 경로

    Returns:
        생성된 알림 기록 딕셔너리
    """
    conn = get_connection(db_path)
    try:
        keywords_json = json.dumps(keywords, ensure_ascii=False) if keywords else None
        cursor = conn.execute(
            """INSERT INTO alerts (user_id, risk_level, keywords, action_taken)
               VALUES (?, ?, ?, ?)""",
            (user_id, risk_level, keywords_json, action_taken)
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "user_id": user_id,
            "risk_level": risk_level,
            "keywords": keywords,
            "action_taken": action_taken
        }
    finally:
        conn.close()


def get_alerts_by_user(
    user_id: str,
    days: int = 30,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    특정 사용자의 최근 N일간 위험 알림 기록을 조회합니다.

    Args:
        user_id: 사용자 ID
        days: 조회할 기간 (일 단위, 기본 30일)
        db_path: 데이터베이스 경로

    Returns:
        알림 기록 리스트 (최신순)
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM alerts
               WHERE user_id = ?
                 AND timestamp >= datetime('now', 'localtime', ? || ' days')
               ORDER BY timestamp DESC""",
            (user_id, f"-{days}")
        ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get("keywords"):
                try:
                    d["keywords"] = json.loads(d["keywords"])
                except (json.JSONDecodeError, TypeError):
                    d["keywords"] = []
            else:
                d["keywords"] = []
            results.append(d)
        return results
    finally:
        conn.close()


# ── 건강 로그 (Health Log) CRUD (본선 확장용) ────────────────────

def add_health_log(
    user_id: str,
    data_type: str,
    value: str,
    normal_range: int = 1,
    db_path: str = DB_PATH
) -> Dict[str, Any]:
    """
    건강 데이터를 health_logs 테이블에 추가합니다. (본선 확장용)

    Args:
        user_id: 사용자 ID
        data_type: 데이터 유형 (blood_pressure / blood_sugar / weight)
        value: 측정값
        normal_range: 정상 범위 여부 (1=정상, 0=비정상)
        db_path: 데이터베이스 경로

    Returns:
        생성된 건강 로그 딕셔너리
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO health_logs (user_id, data_type, value, normal_range)
               VALUES (?, ?, ?, ?)""",
            (user_id, data_type, value, normal_range)
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "user_id": user_id,
            "data_type": data_type,
            "value": value,
            "normal_range": normal_range
        }
    finally:
        conn.close()


def get_health_logs_by_user(
    user_id: str,
    data_type: Optional[str] = None,
    days: int = 30,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    특정 사용자의 건강 데이터 기록을 조회합니다. (본선 확장용)

    Args:
        user_id: 사용자 ID
        data_type: 필터링할 데이터 유형 (선택)
        days: 조회 기간
        db_path: 데이터베이스 경로

    Returns:
        건강 로그 리스트
    """
    conn = get_connection(db_path)
    try:
        if data_type:
            rows = conn.execute(
                """SELECT * FROM health_logs
                   WHERE user_id = ? AND data_type = ?
                     AND timestamp >= datetime('now', 'localtime', ? || ' days')
                   ORDER BY timestamp DESC""",
                (user_id, data_type, f"-{days}")
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM health_logs
                   WHERE user_id = ?
                     AND timestamp >= datetime('now', 'localtime', ? || ' days')
                   ORDER BY timestamp DESC""",
                (user_id, f"-{days}")
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# 모듈 직접 실행 시 DB 초기화
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    conn = init_db()
    print(f"✅ 데이터베이스 초기화 완료: {DB_PATH}")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        print(f"  - {t} 테이블 생성 완료")
    conn.close()
