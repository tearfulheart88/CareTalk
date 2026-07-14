"""Consent-bound phone and wearable ingestion for CareTalk."""

from __future__ import annotations

import base64
import hashlib
import math
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from db.schema import DB_PATH, ensure_schema
from tools.care_circle import authorize_circle_member


DEVICE_TYPES = {"phone", "wearable"}
PHONE_EVENTS = {"screen_unlock", "app_open", "manual_confirm", "device_motion"}
WEARABLE_EVENTS = {"wearable_sync", "device_motion"}
HEALTH_TYPES = {
    "systolic",
    "diastolic",
    "blood_sugar",
    "weight",
    "temperature",
    "heart_rate",
}
_HEALTH_RESERVATION_TTL = timedelta(minutes=5)


class DeviceBridgeError(Exception):
    """A safe, user-facing device bridge failure."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _path(db_path: Optional[str]) -> str:
    return db_path or DB_PATH


def _connect(db_path: str) -> sqlite3.Connection:
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value: str, timezone_name: str = "Asia/Seoul") -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DeviceBridgeError("날짜·시간은 ISO 8601 형식이어야 합니다.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(timezone.utc)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_pairing_code(value: str) -> str:
    return "".join(char for char in str(value or "").upper() if char.isalnum())


def _new_pairing_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(7)).decode("ascii").rstrip("=")[:10]
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _routine_row(conn: sqlite3.Connection, senior_user_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM care_routines WHERE senior_user_id = ?",
        (senior_user_id,),
    ).fetchone()
    if row is None or not bool(row["senior_consented"]):
        raise DeviceBridgeError("먼저 어르신 동의로 예약 돌봄을 활성화해 주세요.", 403)
    return row


def _require_senior(
    conn: sqlite3.Connection, requester_user_id: str, senior_user_id: str
) -> sqlite3.Row:
    _, member, error = authorize_circle_member(conn, senior_user_id, requester_user_id)
    if error:
        raise DeviceBridgeError(error, 403)
    if member is None or member["role"] != "senior" or requester_user_id != senior_user_id:
        raise DeviceBridgeError("기기 연결과 해제는 어르신 계정에서만 할 수 있습니다.", 403)
    return member


def _check_device_enabled(routine: sqlite3.Row, device_type: str) -> None:
    if device_type == "phone" and not bool(routine["phone_activity_enabled"]):
        raise DeviceBridgeError("휴대폰 활동 확인이 꺼져 있습니다. 설정에서 먼저 켜 주세요.", 409)
    if device_type == "wearable" and not bool(routine["wearable_enabled"]):
        raise DeviceBridgeError("웨어러블 연동이 꺼져 있습니다. 설정에서 먼저 켜 주세요.", 409)


def create_device_pairing(
    requester_user_id: str,
    senior_user_id: str,
    *,
    device_type: str = "phone",
    label: str = "",
    senior_consented: bool = False,
    expires_minutes: int = 10,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Create a short-lived, one-use pairing code; only its hash is stored."""
    if not senior_consented:
        raise DeviceBridgeError("기기에서 수집할 항목을 확인하고 어르신이 동의한 뒤 연결해 주세요.", 403)
    device_type = str(device_type or "phone").strip().lower()
    if device_type not in DEVICE_TYPES:
        raise DeviceBridgeError("device_type은 phone 또는 wearable이어야 합니다.")
    try:
        expires_minutes = int(expires_minutes)
    except (TypeError, ValueError) as exc:
        raise DeviceBridgeError("expires_minutes는 숫자여야 합니다.") from exc
    if not 5 <= expires_minutes <= 30:
        raise DeviceBridgeError("연결 코드는 5~30분 동안만 유효하게 만들 수 있습니다.")
    safe_label = str(label or "").strip()[:40]

    path = _path(db_path)
    conn = _connect(path)
    try:
        _require_senior(conn, requester_user_id, senior_user_id)
        routine = _routine_row(conn, senior_user_id)
        _check_device_enabled(routine, device_type)
        now = _utcnow()
        conn.execute(
            """UPDATE care_device_pairings SET status = 'expired'
               WHERE senior_user_id = ? AND status = 'active' AND expires_at <= ?""",
            (senior_user_id, _iso(now)),
        )
        conn.execute(
            """UPDATE care_device_pairings SET status = 'cancelled'
               WHERE senior_user_id = ? AND device_type = ? AND status = 'active'""",
            (senior_user_id, device_type),
        )
        code = _new_pairing_code()
        pairing_id = "pair_" + secrets.token_hex(10)
        expires_at = _iso(now + timedelta(minutes=expires_minutes))
        conn.execute(
            """INSERT INTO care_device_pairings
               (pairing_id, senior_user_id, code_hash, device_type, label,
                expires_at, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (
                pairing_id,
                senior_user_id,
                _hash_secret(_normalize_pairing_code(code)),
                device_type,
                safe_label,
                expires_at,
                _iso(now),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "pairing_created",
        "pairing_code": code,
        "expires_at": expires_at,
        "device_type": device_type,
        "label": safe_label,
        "one_time_secret": True,
        "stored_as_plaintext": False,
        "next_step": "연결할 기기에서 이 코드를 입력하세요. 코드는 한 번 사용하거나 만료되면 다시 쓸 수 없습니다.",
        "privacy": {
            "exact_location_collected": False,
            "screen_content_collected": False,
            "raw_sensor_data_collected": False,
        },
    }


def exchange_device_pairing(
    pairing_code: str, *, db_path: Optional[str] = None
) -> dict[str, Any]:
    """Exchange a one-use code for a token shown once to the companion device."""
    normalized = _normalize_pairing_code(pairing_code)
    if len(normalized) != 10:
        raise DeviceBridgeError("올바른 기기 연결 코드를 입력해 주세요.", 401)
    path = _path(db_path)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM care_device_pairings WHERE code_hash = ?",
            (_hash_secret(normalized),),
        ).fetchone()
        now = _utcnow()
        if row is None or row["status"] != "active":
            raise DeviceBridgeError("연결 코드가 만료되었거나 이미 사용되었습니다.", 401)
        expires_at = _parse_datetime(row["expires_at"])
        if expires_at <= now:
            conn.execute(
                "UPDATE care_device_pairings SET status = 'expired' WHERE pairing_id = ?",
                (row["pairing_id"],),
            )
            conn.commit()
            raise DeviceBridgeError("연결 코드가 만료되었습니다. 어르신 계정에서 새 코드를 만들어 주세요.", 401)
        routine = _routine_row(conn, row["senior_user_id"])
        _check_device_enabled(routine, row["device_type"])

        device_id = "device_" + secrets.token_hex(10)
        device_token = "ctd_" + secrets.token_urlsafe(32)
        now_iso = _iso(now)
        conn.execute(
            """INSERT INTO care_devices
               (device_id, senior_user_id, token_hash, device_type, label,
                status, paired_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                device_id,
                row["senior_user_id"],
                _hash_secret(device_token),
                row["device_type"],
                row["label"],
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            """UPDATE care_device_pairings
               SET status = 'used', used_at = ? WHERE pairing_id = ? AND status = 'active'""",
            (now_iso, row["pairing_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "paired",
        "device_id": device_id,
        "device_type": row["device_type"],
        "label": row["label"],
        "device_token": device_token,
        "token_shown_once": True,
        "token_stored_as_plaintext": False,
        "activity_endpoint": "/device/activity",
        "health_endpoint": "/device/health",
    }


def list_care_devices(
    requester_user_id: str,
    senior_user_id: str,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _path(db_path)
    conn = _connect(path)
    try:
        _require_senior(conn, requester_user_id, senior_user_id)
        rows = conn.execute(
            """SELECT device_id, device_type, label, status, paired_at, last_seen_at, revoked_at
               FROM care_devices WHERE senior_user_id = ?
               ORDER BY paired_at DESC""",
            (senior_user_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "status": "ok",
        "devices": [dict(row) for row in rows],
        "active_count": sum(1 for row in rows if row["status"] == "active"),
        "tokens_returned": False,
    }


def revoke_care_device(
    requester_user_id: str,
    senior_user_id: str,
    device_id: str,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    if not str(device_id or "").strip():
        raise DeviceBridgeError("해제할 device_id를 입력해 주세요.")
    path = _path(db_path)
    conn = _connect(path)
    try:
        _require_senior(conn, requester_user_id, senior_user_id)
        now = _iso(_utcnow())
        cursor = conn.execute(
            """UPDATE care_devices
               SET status = 'revoked', revoked_at = ?
               WHERE device_id = ? AND senior_user_id = ? AND status = 'active'""",
            (now, device_id, senior_user_id),
        )
        conn.commit()
        if cursor.rowcount != 1:
            raise DeviceBridgeError("활성 상태인 기기를 찾지 못했습니다.", 404)
    finally:
        conn.close()
    return {
        "status": "revoked",
        "device_id": device_id,
        "message": "기기 연결을 해제했습니다. 해당 기기 토큰은 더 이상 사용할 수 없습니다.",
    }


def _authenticated_device(conn: sqlite3.Connection, device_token: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    token = str(device_token or "").strip()
    if not token.startswith("ctd_") or len(token) < 40:
        raise DeviceBridgeError("유효한 기기 인증 토큰이 필요합니다.", 401)
    device = conn.execute(
        "SELECT * FROM care_devices WHERE token_hash = ? AND status = 'active'",
        (_hash_secret(token),),
    ).fetchone()
    if device is None:
        raise DeviceBridgeError("기기 인증 토큰이 만료되었거나 해제되었습니다.", 401)
    routine = _routine_row(conn, device["senior_user_id"])
    _check_device_enabled(routine, device["device_type"])
    return device, routine


def ingest_device_activity(
    device_token: str,
    event_type: str,
    *,
    occurred_at: str = "",
    event_id: str = "",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _path(db_path)
    conn = _connect(path)
    try:
        device, _routine = _authenticated_device(conn, device_token)
        allowed = PHONE_EVENTS if device["device_type"] == "phone" else WEARABLE_EVENTS
        event_type = str(event_type or "").strip()
        if event_type not in allowed:
            raise DeviceBridgeError(
                f"{device['device_type']} 기기에서 허용되지 않은 활동 종류입니다."
            )
        safe_event_id = str(event_id or "").strip()
        if safe_event_id and len(safe_event_id) > 96:
            raise DeviceBridgeError("event_id는 96자 이하여야 합니다.")
        namespaced_event_id = (
            f"{device['device_id']}:{safe_event_id}"
            if safe_event_id
            else f"{device['device_id']}:activity_{secrets.token_hex(10)}"
        )
        senior_user_id = device["senior_user_id"]
        source = device["device_type"]
    finally:
        conn.close()

    from tools.care_routine import record_phone_activity

    result = record_phone_activity(
        senior_user_id,
        senior_user_id,
        event_type,
        source=source,
        occurred_at=occurred_at,
        event_id=namespaced_event_id,
        db_path=path,
    )
    if result.get("error"):
        raise DeviceBridgeError(str(result["error"]))
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE care_devices SET last_seen_at = ? WHERE device_id = ? AND status = 'active'",
            (_iso(_utcnow()), device["device_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result.update(
        {
            "device_id": device["device_id"],
            "authenticated_device": True,
            "exact_location_collected": False,
            "screen_content_collected": False,
        }
    )
    return result


def ingest_device_health(
    device_token: str,
    event_id: str,
    data_type: str,
    value: Any,
    *,
    occurred_at: str = "",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    data_type = str(data_type or "").strip()
    if data_type not in HEALTH_TYPES:
        raise DeviceBridgeError("지원하지 않는 건강 데이터 종류입니다.")
    safe_event_id = str(event_id or "").strip()
    if not safe_event_id or len(safe_event_id) > 96:
        raise DeviceBridgeError("중복 방지를 위해 1~96자의 event_id가 필요합니다.")
    if isinstance(value, bool):
        raise DeviceBridgeError("value는 숫자여야 합니다.")
    try:
        safe_value = float(value)
    except (TypeError, ValueError) as exc:
        raise DeviceBridgeError("value는 숫자여야 합니다.") from exc
    if not math.isfinite(safe_value):
        raise DeviceBridgeError("value는 유한한 숫자여야 합니다.")

    path = _path(db_path)
    conn = _connect(path)
    try:
        device, routine = _authenticated_device(conn, device_token)
        occurred = _parse_datetime(occurred_at, routine["timezone"]) if occurred_at else _utcnow()
        now = _utcnow()
        if occurred > now + timedelta(minutes=5):
            raise DeviceBridgeError("occurred_at은 현재보다 5분 이상 미래일 수 없습니다.")
        if occurred < now - timedelta(days=7):
            raise DeviceBridgeError("7일보다 오래된 건강 신호는 기록하지 않습니다.")
        namespaced_event_id = f"{device['device_id']}:{safe_event_id}"
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT health_log_id, received_at
               FROM device_health_events WHERE event_id = ?""",
            (namespaced_event_id,),
        ).fetchone()
        if existing is not None:
            if existing["health_log_id"] is not None:
                conn.commit()
                return {
                    "status": "duplicate_ignored",
                    "event_id": safe_event_id,
                    "health_log_id": existing["health_log_id"],
                    "authenticated_device": True,
                }
            try:
                reserved_at = _parse_datetime(existing["received_at"])
            except (DeviceBridgeError, TypeError):
                reserved_at = now - _HEALTH_RESERVATION_TTL
            if reserved_at > now - _HEALTH_RESERVATION_TTL:
                conn.commit()
                return {
                    "status": "processing",
                    "event_id": safe_event_id,
                    "retry_after_seconds": 5,
                    "authenticated_device": True,
                }
            conn.execute(
                """DELETE FROM device_health_events
                   WHERE event_id = ? AND health_log_id IS NULL""",
                (namespaced_event_id,),
            )
        conn.execute(
            """INSERT INTO device_health_events
               (event_id, device_id, senior_user_id, data_type, value, occurred_at, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                namespaced_event_id,
                device["device_id"],
                device["senior_user_id"],
                data_type,
                safe_value,
                _iso(occurred),
                _iso(now),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from tools.health_log import log_health_data

    result = log_health_data(
        device["senior_user_id"],
        data_type,
        safe_value,
        db_path=path,
        source="device",
        source_event_id=namespaced_event_id,
    )
    if result.get("error"):
        cleanup = _connect(path)
        try:
            cleanup.execute(
                "DELETE FROM device_health_events WHERE event_id = ? AND health_log_id IS NULL",
                (namespaced_event_id,),
            )
            cleanup.commit()
        finally:
            cleanup.close()
        raise DeviceBridgeError(str(result["error"]))

    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE health_logs SET timestamp = ? WHERE id = ?",
            (_iso(occurred), result["log_id"]),
        )
        conn.execute(
            "UPDATE device_health_events SET health_log_id = ? WHERE event_id = ?",
            (result["log_id"], namespaced_event_id),
        )
        conn.execute(
            "UPDATE care_devices SET last_seen_at = ? WHERE device_id = ? AND status = 'active'",
            (_iso(_utcnow()), device["device_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    result.update(
        {
            "event_id": safe_event_id,
            "occurred_at": _iso(occurred),
            "device_id": device["device_id"],
            "authenticated_device": True,
            "raw_sensor_data_collected": False,
        }
    )
    return result
