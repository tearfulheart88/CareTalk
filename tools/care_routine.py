"""Scheduled check-ins, family digests, and minimal phone-activity monitoring."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db.schema import DB_PATH, ensure_schema
from tools.care_circle import authorize_circle_member


INTERACTION_EVENTS = {"screen_unlock", "app_open", "manual_confirm"}
MOTION_EVENTS = {"device_motion"}
WEARABLE_EVENTS = {"wearable_sync"}
ALLOWED_ACTIVITY_EVENTS = INTERACTION_EVENTS | MOTION_EVENTS | WEARABLE_EVENTS
ALLOWED_ACTIVITY_SOURCES = {"phone", "wearable", "manual", "demo"}
ACKNOWLEDGEMENT_ALIASES = {
    "확인했어요": "confirmed",
    "알림 확인했어요": "confirmed",
    "전화해볼게요": "calling",
    "직접 연락할게요": "calling",
    "방문 확인할게요": "visiting",
    "해결됐어요": "resolved",
    "도움이 더 필요해요": "needs_help",
    "confirmed": "confirmed",
    "calling": "calling",
    "visiting": "visiting",
    "resolved": "resolved",
    "needs_help": "needs_help",
}


def _db_path(db_path: Optional[str]) -> str:
    return db_path or DB_PATH


def _connect(db_path: str) -> sqlite3.Connection:
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    # .NET DateTime 등은 초 이하 7자리를 보낼 수 있지만 Python 3.10은 6자리까지 받는다.
    text = re.sub(r"(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$|$)", r"\1", text)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _parse_db_datetime(value: Any, tz: ZoneInfo) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _parse_datetime(str(value), tz).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _time_list(value: Any, *, minimum: int, maximum: int, field: str) -> list[str]:
    if isinstance(value, str):
        raw = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        raw = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw = []
    unique: list[str] = []
    for item in raw:
        try:
            parsed_clock = _parse_clock(item)
        except ValueError as exc:
            raise ValueError(f"{field}의 시간은 HH:MM 형식이어야 합니다.") from exc
        canonical = f"{parsed_clock.hour:02d}:{parsed_clock.minute:02d}"
        if canonical not in unique:
            unique.append(canonical)
    unique.sort()
    if not minimum <= len(unique) <= maximum:
        raise ValueError(f"{field}은 {minimum}~{maximum}개 시간이어야 합니다.")
    return unique


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _mask_account(value: str) -> str:
    text = str(value or "")
    return "**" if len(text) <= 2 else text[:2] + "*" * min(6, len(text) - 2)


def _settings_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "senior_user_id": row["senior_user_id"],
        "timezone": row["timezone"],
        "prompt_times": _json_list(row["prompt_times"]),
        "digest_times": _json_list(row["digest_times"]),
        "response_window_minutes": int(row["response_window_minutes"]),
        "inactivity_hours": int(row["inactivity_hours"]),
        "escalation_hours": int(row["escalation_hours"]),
        "inactivity_grace_minutes": int(row["inactivity_grace_minutes"]),
        "inactivity_mode": row["inactivity_mode"],
        "quiet_start": row["quiet_start"],
        "quiet_end": row["quiet_end"],
        "phone_activity_enabled": bool(row["phone_activity_enabled"]),
        "wearable_enabled": bool(row["wearable_enabled"]),
        "senior_consented": bool(row["senior_consented"]),
    }


def _get_settings(conn: sqlite3.Connection, senior_user_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM care_routines WHERE senior_user_id = ?",
        (senior_user_id,),
    ).fetchone()
    return _settings_from_row(row) if row else None


def configure_care_routine(
    requester_user_id: str,
    senior_user_id: str,
    *,
    prompt_times: Any = "09:00,14:00,20:00",
    digest_times: Any = "14:30,21:00",
    timezone_name: str = "Asia/Seoul",
    response_window_minutes: int = 60,
    inactivity_hours: int = 8,
    escalation_hours: int = 12,
    inactivity_grace_minutes: int = 30,
    inactivity_mode: str = "both",
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
    phone_activity_enabled: bool = True,
    wearable_enabled: bool = False,
    senior_consented: bool = False,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return {"error": "지원되는 timezone 이름을 입력해 주세요. 예: Asia/Seoul"}
    try:
        prompts = _time_list(prompt_times, minimum=1, maximum=6, field="prompt_times")
        digests = _time_list(digest_times, minimum=1, maximum=4, field="digest_times")
        quiet_start = _parse_clock(quiet_start).strftime("%H:%M")
        quiet_end = _parse_clock(quiet_end).strftime("%H:%M")
    except ValueError as exc:
        return {"error": str(exc)}
    if not 15 <= int(response_window_minutes) <= 240:
        return {"error": "response_window_minutes는 15~240분이어야 합니다."}
    if not 2 <= int(inactivity_hours) <= 48:
        return {"error": "inactivity_hours는 2~48시간이어야 합니다."}
    if not int(inactivity_hours) < int(escalation_hours) <= 72:
        return {"error": "escalation_hours는 inactivity_hours보다 크고 72시간 이하여야 합니다."}
    if not 10 <= int(inactivity_grace_minutes) <= 180:
        return {"error": "inactivity_grace_minutes는 10~180분이어야 합니다."}
    if inactivity_mode not in {"both", "either"}:
        return {"error": "inactivity_mode는 both 또는 either여야 합니다."}

    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id
        )
        if error:
            return {"error": error}
        if requester["role"] != "senior" and "manage_schedule" not in _json_list(requester["permissions"]):
            return {"error": "이 계정에는 예약을 변경할 권한이 없습니다."}
        existing = _get_settings(conn, senior_user_id)
        if requester["role"] == "senior":
            if not senior_consented:
                return {"error": "예약 질문과 휴대폰 활동 확인은 어르신 동의 후 켤 수 있습니다."}
        elif existing is None or not existing["senior_consented"]:
            return {"error": "어르신이 먼저 예약 돌봄에 동의해야 가족이 일정을 관리할 수 있습니다."}
        if requester["role"] != "senior":
            timezone_name = existing["timezone"]
            inactivity_hours = existing["inactivity_hours"]
            escalation_hours = existing["escalation_hours"]
            inactivity_grace_minutes = existing["inactivity_grace_minutes"]
            inactivity_mode = existing["inactivity_mode"]
            quiet_start = existing["quiet_start"]
            quiet_end = existing["quiet_end"]
            phone_activity_enabled = existing["phone_activity_enabled"]
            wearable_enabled = existing["wearable_enabled"]
        now = _iso(_utcnow())
        conn.execute(
            """INSERT INTO care_routines
               (senior_user_id, timezone, prompt_times, digest_times,
                response_window_minutes, inactivity_hours, escalation_hours,
                inactivity_grace_minutes, inactivity_mode, quiet_start, quiet_end,
                phone_activity_enabled, wearable_enabled, senior_consented, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
               ON CONFLICT(senior_user_id) DO UPDATE SET
                 timezone = excluded.timezone,
                 prompt_times = excluded.prompt_times,
                 digest_times = excluded.digest_times,
                 response_window_minutes = excluded.response_window_minutes,
                 inactivity_hours = excluded.inactivity_hours,
                 escalation_hours = excluded.escalation_hours,
                 inactivity_grace_minutes = excluded.inactivity_grace_minutes,
                 inactivity_mode = excluded.inactivity_mode,
                 quiet_start = excluded.quiet_start,
                 quiet_end = excluded.quiet_end,
                 phone_activity_enabled = excluded.phone_activity_enabled,
                 wearable_enabled = excluded.wearable_enabled,
                 senior_consented = 1,
                 updated_at = excluded.updated_at""",
            (
                senior_user_id,
                timezone_name,
                json.dumps(prompts),
                json.dumps(digests),
                int(response_window_minutes),
                int(inactivity_hours),
                int(escalation_hours),
                int(inactivity_grace_minutes),
                inactivity_mode,
                quiet_start,
                quiet_end,
                1 if phone_activity_enabled else 0,
                1 if wearable_enabled else 0,
                now,
            ),
        )
        conn.execute(
            "UPDATE care_circles SET senior_consented = 1 WHERE circle_id = ?",
            (circle["circle_id"],),
        )
        conn.commit()
        settings = _get_settings(conn, senior_user_id)
    finally:
        conn.close()

    return {
        "status": "configured",
        "settings": settings,
        "flow": [
            "예약 시각에 어르신 질문을 대기열에 생성",
            "허용된 가족에게 중간·하루 요약 생성",
            "활동 부재 시 어르신에게 먼저 확인",
            "유예시간 후에도 변화가 없을 때 가족에게 안내",
        ],
        "delivery_performed": False,
        "scheduler_required": True,
        "privacy": {
            "exact_location_collected": False,
            "screen_content_collected": False,
            "raw_motion_collected": False,
            "stored_signal": "활동 종류와 마지막 발생 시각",
        },
    }


def record_phone_activity(
    requester_user_id: str,
    senior_user_id: str,
    event_type: str,
    *,
    source: str = "phone",
    occurred_at: str = "",
    event_id: str = "",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    if requester_user_id != senior_user_id:
        return {"error": "휴대폰 활동은 인증된 어르신 기기 어댑터만 기록할 수 있습니다."}
    if event_type not in ALLOWED_ACTIVITY_EVENTS:
        return {"error": "event_type은 screen_unlock, app_open, manual_confirm, device_motion, wearable_sync 중 하나여야 합니다."}
    if source not in ALLOWED_ACTIVITY_SOURCES:
        return {"error": "source는 phone, wearable, manual, demo 중 하나여야 합니다."}

    path = _db_path(db_path)
    conn = _connect(path)
    try:
        _, _, error = authorize_circle_member(conn, senior_user_id, requester_user_id)
        if error:
            return {"error": error}
        settings = _get_settings(conn, senior_user_id)
        if settings is None or not settings["senior_consented"]:
            return {"error": "먼저 동의 후 care_routine configure를 실행해 주세요."}
        if event_type in WEARABLE_EVENTS and not settings["wearable_enabled"]:
            return {"error": "웨어러블 활동 수집이 꺼져 있습니다."}
        tz = ZoneInfo(settings["timezone"])
        try:
            event_time = _parse_datetime(occurred_at, tz) if occurred_at else datetime.now(tz)
        except ValueError:
            return {"error": "occurred_at은 ISO 8601 날짜·시간이어야 합니다."}
        event_utc = event_time.astimezone(timezone.utc)
        now = _utcnow()
        if event_utc > now.replace(microsecond=0) and (event_utc - now).total_seconds() > 300:
            return {"error": "occurred_at은 현재보다 5분 이상 미래일 수 없습니다."}
        if (now - event_utc).total_seconds() > 7 * 86400:
            return {"error": "7일보다 오래된 활동 신호는 기록하지 않습니다."}
        safe_event_id = event_id.strip() or "activity_" + secrets.token_hex(10)
        inserted = conn.execute(
            """INSERT OR IGNORE INTO phone_activity_events
               (event_id, senior_user_id, event_type, source, occurred_at)
               VALUES (?, ?, ?, ?, ?)""",
            (safe_event_id, senior_user_id, event_type, source, _iso(event_utc)),
        )
        if inserted.rowcount:
            conn.execute(
                """UPDATE care_outbox SET status = 'cancelled'
                   WHERE senior_user_id = ? AND status = 'pending'
                     AND event_type IN ('activity_check', 'inactivity_notice')
                     AND created_at <= ?""",
                (senior_user_id, _iso(event_utc)),
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "recorded" if inserted.rowcount else "duplicate_ignored",
        "event_id": safe_event_id,
        "event_type": event_type,
        "source": source,
        "occurred_at": _iso(event_utc),
        "stored_fields": ["event_id", "event_type", "source", "occurred_at"],
        "exact_location_collected": False,
        "raw_sensor_data_collected": False,
        "pending_inactivity_notices_cancelled": bool(inserted.rowcount),
    }


def _latest_event(
    conn: sqlite3.Connection, senior_user_id: str, event_types: set[str]
) -> Optional[sqlite3.Row]:
    placeholders = ",".join("?" for _ in event_types)
    return conn.execute(
        f"""SELECT event_type, source, occurred_at
            FROM phone_activity_events
            WHERE senior_user_id = ? AND event_type IN ({placeholders})
            ORDER BY occurred_at DESC LIMIT 1""",
        (senior_user_id, *sorted(event_types)),
    ).fetchone()


def _activity_snapshot(
    conn: sqlite3.Connection,
    senior_user_id: str,
    now_utc: datetime,
    tz: ZoneInfo,
) -> dict[str, Any]:
    interaction = _latest_event(conn, senior_user_id, INTERACTION_EVENTS)
    motion = _latest_event(conn, senior_user_id, MOTION_EVENTS)
    wearable = _latest_event(conn, senior_user_id, WEARABLE_EVENTS)

    response = conn.execute(
        """SELECT created_at FROM checkin_responses
           WHERE user_id = ? ORDER BY id DESC LIMIT 1""",
        (senior_user_id,),
    ).fetchone()
    response_at = _parse_db_datetime(response["created_at"], tz) if response else None
    interaction_at = _parse_db_datetime(interaction["occurred_at"], tz) if interaction else None
    interaction_source = interaction["event_type"] if interaction else None
    if response_at and (interaction_at is None or response_at > interaction_at):
        interaction_at = response_at
        interaction_source = "checkin_reply"
    motion_at = _parse_db_datetime(motion["occurred_at"], tz) if motion else None
    wearable_at = _parse_db_datetime(wearable["occurred_at"], tz) if wearable else None

    def hours_since(value: Optional[datetime]) -> Optional[float]:
        if value is None:
            return None
        return round(max(0.0, (now_utc - value).total_seconds() / 3600), 1)

    return {
        "last_interaction_at": _iso(interaction_at) if interaction_at else None,
        "last_interaction_source": interaction_source,
        "interaction_inactive_hours": hours_since(interaction_at),
        "last_motion_at": _iso(motion_at) if motion_at else None,
        "motion_inactive_hours": hours_since(motion_at),
        "last_wearable_sync_at": _iso(wearable_at) if wearable_at else None,
        "wearable_inactive_hours": hours_since(wearable_at),
    }


def _is_quiet(now_local: datetime, start: str, end: str) -> bool:
    current = now_local.time().replace(second=0, microsecond=0)
    start_t = _parse_clock(start)
    end_t = _parse_clock(end)
    if start_t == end_t:
        return False
    if start_t < end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t


def _slot_due(now_local: datetime, slot: str, grace_minutes: int = 30) -> bool:
    slot_time = _parse_clock(slot)
    slot_dt = now_local.replace(
        hour=slot_time.hour, minute=slot_time.minute, second=0, microsecond=0
    )
    delta = (now_local - slot_dt).total_seconds() / 60
    return 0 <= delta < grace_minutes


def _event_exists(conn: sqlite3.Connection, dedupe_key: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM care_outbox WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()


def _queue_event(
    conn: sqlite3.Connection,
    *,
    senior_user_id: str,
    recipient_user_id: str,
    event_type: str,
    severity: str,
    payload: dict[str, Any],
    due_at: datetime,
    dedupe_key: str,
) -> Optional[dict[str, Any]]:
    created_at = _iso(_utcnow())
    cursor = conn.execute(
        """INSERT OR IGNORE INTO care_outbox
           (senior_user_id, recipient_user_id, event_type, severity,
            payload_json, due_at, status, dedupe_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            senior_user_id,
            recipient_user_id,
            event_type,
            severity,
            json.dumps(payload, ensure_ascii=False),
            _iso(due_at),
            dedupe_key,
            created_at,
        ),
    )
    if not cursor.rowcount:
        return None
    return {
        "outbox_id": cursor.lastrowid,
        "event_type": event_type,
        "severity": severity,
        "recipient_hint": _mask_account(recipient_user_id),
        "due_at": _iso(due_at),
        "preview": payload.get("text", "")[:180],
    }


def _members_with_permission(
    conn: sqlite3.Connection, circle_id: str, permission: str
) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT * FROM care_memberships
           WHERE circle_id = ? AND status = 'active' AND role != 'senior'""",
        (circle_id,),
    ).fetchall()
    return [row for row in rows if permission in _json_list(row["permissions"])]


def _scheduled_prompt(slot: str, nickname: str) -> tuple[str, list[str]]:
    hour = int(slot.split(":", 1)[0])
    name = nickname if nickname.endswith("님") else nickname + "님"
    if hour < 12:
        return (
            f"좋은 아침이에요, {name}.\n\n지금 어떠세요?\n아래에서 하나만 눌러 주세요.",
            ["잘 잤어요", "아침 먹었어요", "약 먹었어요", "조금 아파요", "도움이 필요해요"],
        )
    if hour < 18:
        return (
            f"{name}, 오후 안부를 확인할게요.\n\n지금과 가까운 답을 하나 눌러 주세요.",
            ["오늘은 괜찮아요", "밥 먹었어요", "산책했어요", "조금 아파요", "도움이 필요해요"],
        )
    return (
        f"{name}, 오늘 하루는 어떠셨어요?\n\n길게 쓰지 않고 하나만 눌러도 돼요.",
        ["오늘은 괜찮아요", "저녁 먹었어요", "약 먹었어요", "조금 외로워요", "도움이 필요해요"],
    )


def _queue_due_prompts(
    conn: sqlite3.Connection,
    senior_user_id: str,
    settings: dict[str, Any],
    now_local: datetime,
) -> list[dict[str, Any]]:
    user = conn.execute(
        "SELECT nickname FROM users WHERE user_id = ?", (senior_user_id,)
    ).fetchone()
    nickname = user["nickname"] if user and user["nickname"] else "어르신"
    queued: list[dict[str, Any]] = []
    for slot in settings["prompt_times"]:
        if not _slot_due(now_local, slot):
            continue
        key = f"checkin:{senior_user_id}:{now_local.date().isoformat()}:{slot}"
        if _event_exists(conn, key):
            continue
        text, replies = _scheduled_prompt(slot, nickname)
        cursor = conn.execute(
            """INSERT INTO checkins
               (user_id, nickname, checkin_date, checkin_time, status)
               VALUES (?, ?, ?, ?, 'initiated')""",
            (senior_user_id, nickname, now_local.date().isoformat(), slot + ":00"),
        )
        payload = {
            "text": text,
            "checkin_id": cursor.lastrowid,
            "quick_replies": replies,
            "message_json": {
                "version": "2.0",
                "template": {
                    "outputs": [{"simpleText": {"text": text}}],
                    "quickReplies": [
                        {"label": reply, "action": "message", "messageText": reply}
                        for reply in replies
                    ],
                },
            },
        }
        event = _queue_event(
            conn,
            senior_user_id=senior_user_id,
            recipient_user_id=senior_user_id,
            event_type="scheduled_checkin",
            severity="info",
            payload=payload,
            due_at=now_local,
            dedupe_key=key,
        )
        if event:
            queued.append(event)
    return queued


def _activity_text(snapshot: dict[str, Any]) -> str:
    interaction = snapshot["interaction_inactive_hours"]
    motion = snapshot["motion_inactive_hours"]
    if interaction is None and motion is None:
        return "휴대폰 활동 기준을 아직 만들고 있어요."
    parts = []
    if interaction is not None:
        parts.append(f"마지막 화면 사용 {interaction:g}시간 전")
    if motion is not None:
        parts.append(f"마지막 휴대폰 이동 {motion:g}시간 전")
    return ", ".join(parts)


def _routine_day_summary(
    conn: sqlite3.Connection, senior_user_id: str, local_date: str
) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT nickname, status, sentiment, health_keywords,
                  message, user_message, response_received, checkin_time
           FROM checkins
           WHERE user_id = ?
             AND COALESCE(NULLIF(checkin_date, ''), date(timestamp)) = ?
           ORDER BY COALESCE(NULLIF(checkin_time, ''), timestamp), id""",
        (senior_user_id, local_date),
    ).fetchall()
    user = conn.execute(
        "SELECT nickname FROM users WHERE user_id = ?", (senior_user_id,)
    ).fetchone()
    nickname = (
        next((row["nickname"] for row in reversed(rows) if row["nickname"]), "")
        or (user["nickname"] if user and user["nickname"] else "어르신")
    )
    responded_rows = [
        row
        for row in rows
        if bool(row["response_received"])
        or bool(row["message"])
        or bool(row["user_message"])
    ]
    messages = [str(row["user_message"] or row["message"] or "") for row in responded_rows]
    health_keywords: list[str] = []
    for row in responded_rows:
        for keyword in _json_list(row["health_keywords"]):
            if keyword not in health_keywords:
                health_keywords.append(keyword)
    confirmations = []
    if any("먹었" in message and "못 먹" not in message for message in messages):
        confirmations.append("식사")
    if any(("약 먹" in message or "약을 먹" in message) and "못 먹" not in message for message in messages):
        confirmations.append("복약")
    if any("산책" in message or "운동" in message for message in messages):
        confirmations.append("활동")
    if any("잘 잤" in message for message in messages):
        confirmations.append("수면")
    concern_count = sum(
        1
        for row in responded_rows
        if row["status"] == "concern" or row["sentiment"] == "negative"
    )
    latest_sentiment = next(
        (row["sentiment"] for row in reversed(responded_rows) if row["sentiment"]),
        "",
    )
    total = len(rows)
    responded = len(responded_rows)
    if total == 0:
        status = "no_checkin"
        summary_line = f"{nickname}님은 오늘 아직 예약 안부 전이에요."
    elif responded == 0:
        status = "no_response"
        summary_line = f"{nickname}님은 오늘 {total}회 안부 질문에 아직 응답이 없어요."
    else:
        status = "complete" if responded == total else "partial"
        summary_line = f"{nickname}님은 오늘 {total}회 질문 중 {responded}회 응답했어요."
        if confirmations:
            summary_line += " " + "·".join(confirmations) + " 확인."
        if concern_count:
            summary_line += f" 불편 신호 {concern_count}회는 사람이 확인해 주세요."
    return {
        "date": local_date,
        "nickname": nickname,
        "status": status,
        "scheduled_count": total,
        "responded_count": responded,
        "response_rate": round((responded / total) * 100, 1) if total else 0.0,
        "latest_sentiment": latest_sentiment,
        "routine_confirmations": confirmations,
        "health_keywords": health_keywords[:5],
        "concern_count": concern_count,
        "summary_line": summary_line,
        "raw_messages_shared": False,
    }


def _queue_due_digests(
    conn: sqlite3.Connection,
    circle: sqlite3.Row,
    senior_user_id: str,
    settings: dict[str, Any],
    now_local: datetime,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    due_slots = [slot for slot in settings["digest_times"] if _slot_due(now_local, slot)]
    if not due_slots:
        return []
    safe_summary = _routine_day_summary(
        conn, senior_user_id, now_local.date().isoformat()
    )
    recipients = _members_with_permission(conn, circle["circle_id"], "view_summary")
    queued: list[dict[str, Any]] = []
    for slot in due_slots:
        label = "중간 일과" if int(slot[:2]) < 18 else "하루 일과"
        text = (
            f"{label} 요약\n\n"
            f"{safe_summary.get('summary_line') or '아직 안부 응답이 없습니다.'}\n"
            f"{_activity_text(snapshot)}\n\n"
            "동의된 요약만 공유하며 원문 대화와 정확한 위치는 포함하지 않아요."
        )
        payload = {
            "text": text,
            "summary": safe_summary,
            "phone_activity": snapshot,
            "quick_replies": ["확인했어요", "직접 연락할게요", "오늘 기록 보기"],
        }
        for member in recipients:
            key = (
                f"digest:{senior_user_id}:{member['account_user_id']}:"
                f"{now_local.date().isoformat()}:{slot}"
            )
            event = _queue_event(
                conn,
                senior_user_id=senior_user_id,
                recipient_user_id=member["account_user_id"],
                event_type="family_digest",
                severity="attention" if safe_summary.get("concern_count") or safe_summary.get("status") == "no_response" else "info",
                payload=payload,
                due_at=now_local,
                dedupe_key=key,
            )
            if event:
                queued.append(event)
    return queued


def _inactivity_state(
    settings: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[str, bool, float]:
    interaction = snapshot["interaction_inactive_hours"]
    motion = snapshot["motion_inactive_hours"]
    threshold = float(settings["inactivity_hours"])
    known = [value for value in (interaction, motion) if value is not None]
    if not known:
        return "awaiting_first_signal", False, 0.0
    if settings["inactivity_mode"] == "both":
        if interaction is None or motion is None:
            return "awaiting_both_signals", False, max(known)
        inactive = interaction >= threshold and motion >= threshold
    else:
        inactive = any(value >= threshold for value in known)
    if not inactive:
        if any(value >= threshold for value in known):
            return "partial_stale", False, max(known)
        return "active", False, max(known)
    return "inactive", True, max(known)


def _queue_inactivity_flow(
    conn: sqlite3.Connection,
    circle: sqlite3.Row,
    senior_user_id: str,
    settings: dict[str, Any],
    now_local: datetime,
    snapshot: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    if not settings["phone_activity_enabled"]:
        return "disabled", []
    if _is_quiet(now_local, settings["quiet_start"], settings["quiet_end"]):
        return "quiet_hours", []
    state, inactive, longest = _inactivity_state(settings, snapshot)
    if not inactive:
        return state, []

    baseline = "|".join(
        [
            str(snapshot.get("last_interaction_at") or "none"),
            str(snapshot.get("last_motion_at") or "none"),
        ]
    )
    token = hashlib.sha256(baseline.encode("utf-8")).hexdigest()[:16]
    check_key = f"activity-check:{senior_user_id}:{token}"
    check = _event_exists(conn, check_key)
    queued: list[dict[str, Any]] = []
    if check is None:
        text = (
            "휴대폰 활동이 한동안 보이지 않았어요.\n\n"
            "괜찮으시면 아래 버튼 하나만 눌러 주세요.\n"
            "배터리나 통신 문제일 수도 있어요."
        )
        payload = {
            "text": text,
            "quick_replies": ["저는 괜찮아요", "휴대폰을 두고 왔어요", "도움이 필요해요"],
            "activity_snapshot": snapshot,
        }
        event = _queue_event(
            conn,
            senior_user_id=senior_user_id,
            recipient_user_id=senior_user_id,
            event_type="activity_check",
            severity="attention",
            payload=payload,
            due_at=now_local,
            dedupe_key=check_key,
        )
        if event:
            queued.append(event)
        return "senior_confirmation_queued", queued

    check_created = _parse_db_datetime(check["created_at"], ZoneInfo(settings["timezone"]))
    elapsed_minutes = (
        (now_local.astimezone(timezone.utc) - check_created).total_seconds() / 60
        if check_created
        else 0
    )
    if elapsed_minutes < settings["inactivity_grace_minutes"]:
        return "awaiting_senior_confirmation", []

    recipients = _members_with_permission(
        conn, circle["circle_id"], "receive_inactivity_alerts"
    )
    severity = "urgent" if longest >= settings["escalation_hours"] else "attention"
    text = (
        "휴대폰 활동 확인 안내\n\n"
        f"{_activity_text(snapshot)}이며, 어르신 확인 요청에도 아직 변화가 없습니다.\n\n"
        "휴대폰을 두고 외출했거나 배터리·통신 문제일 수 있으며 응급 상황으로 확정된 것은 아닙니다. "
        "먼저 전화로 안부를 확인해 주세요."
    )
    payload = {
        "text": text,
        "severity": severity,
        "activity_snapshot": snapshot,
        "not_an_emergency_diagnosis": True,
        "quick_replies": ["전화해볼게요", "방문 확인할게요", "확인했어요", "도움이 더 필요해요"],
    }
    for member in recipients:
        key = f"inactivity:{senior_user_id}:{member['account_user_id']}:{token}"
        event = _queue_event(
            conn,
            senior_user_id=senior_user_id,
            recipient_user_id=member["account_user_id"],
            event_type="inactivity_notice",
            severity=severity,
            payload=payload,
            due_at=now_local,
            dedupe_key=key,
        )
        if event:
            queued.append(event)
    return ("family_notice_queued" if recipients else "no_authorized_recipient"), queued


def run_due_care_tasks(
    requester_user_id: str,
    senior_user_id: str,
    *,
    now: str = "",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id, "manage_schedule"
        )
        if error:
            return {"error": error}
        settings = _get_settings(conn, senior_user_id)
        if settings is None:
            return {"error": "먼저 care_routine configure로 예약을 설정해 주세요."}
        if not settings["senior_consented"]:
            return {"error": "어르신이 예약 돌봄을 중지했습니다. 다시 동의하고 configure해야 합니다."}
        tz = ZoneInfo(settings["timezone"])
        try:
            now_local = _parse_datetime(now, tz).astimezone(tz) if now else datetime.now(tz)
        except ValueError:
            return {"error": "now는 ISO 8601 날짜·시간이어야 합니다."}
        now_utc = now_local.astimezone(timezone.utc)
        snapshot = _activity_snapshot(conn, senior_user_id, now_utc, tz)
        queued = _queue_due_prompts(conn, senior_user_id, settings, now_local)
        conn.commit()
        queued.extend(
            _queue_due_digests(
                conn, circle, senior_user_id, settings, now_local, snapshot
            )
        )
        inactivity_status, inactivity_events = _queue_inactivity_flow(
            conn, circle, senior_user_id, settings, now_local, snapshot
        )
        queued.extend(inactivity_events)
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "processed",
        "run_at": now_local.isoformat(timespec="minutes"),
        "queued_count": len(queued),
        "queued_events": queued,
        "phone_activity": snapshot,
        "inactivity_status": inactivity_status,
        "delivery_performed": False,
        "delivery_mode": "persistent_outbox",
        "scheduler_note": "운영 환경의 예약 작업이 이 action을 주기적으로 호출하고 승인된 카카오 어댑터가 대기열을 전달합니다.",
        "requester_role": requester["role"],
    }


def acknowledge_care_notification(
    requester_user_id: str,
    senior_user_id: str,
    outbox_id: int,
    response: str,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    normalized = ACKNOWLEDGEMENT_ALIASES.get(str(response or "").strip())
    if normalized is None:
        return {
            "error": "response는 확인했어요, 전화해볼게요, 방문 확인할게요, 해결됐어요, 도움이 더 필요해요 중 하나여야 합니다."
        }
    try:
        safe_outbox_id = int(outbox_id)
    except (TypeError, ValueError):
        return {"error": "outbox_id는 숫자여야 합니다."}

    path = _db_path(db_path)
    conn = _connect(path)
    try:
        _, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id
        )
        if error:
            return {"error": error}
        notification = conn.execute(
            """SELECT * FROM care_outbox
               WHERE id = ? AND senior_user_id = ?""",
            (safe_outbox_id, senior_user_id),
        ).fetchone()
        if notification is None:
            return {"error": "확인할 알림을 찾지 못했습니다."}
        if notification["recipient_user_id"] != requester_user_id:
            return {"error": "이 계정이 받은 알림만 확인할 수 있습니다."}
        if notification["event_type"] not in {"family_digest", "inactivity_notice"}:
            return {"error": "가족 요약 또는 활동 부재 알림만 이 action으로 확인할 수 있습니다."}
        if notification["status"] == "cancelled":
            return {"error": "이미 취소된 알림입니다."}
        now = _iso(_utcnow())
        conn.execute(
            """INSERT INTO care_acknowledgements
               (outbox_id, senior_user_id, responder_user_id, response, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(outbox_id, responder_user_id) DO UPDATE SET
                 response = excluded.response,
                 created_at = excluded.created_at""",
            (safe_outbox_id, senior_user_id, requester_user_id, normalized, now),
        )
        conn.execute(
            "UPDATE care_outbox SET status = 'acknowledged' WHERE id = ?",
            (safe_outbox_id,),
        )
        conn.commit()
    finally:
        conn.close()

    label = {
        "confirmed": "확인 완료",
        "calling": "전화 확인 예정",
        "visiting": "방문 확인 예정",
        "resolved": "상황 해결",
        "needs_help": "추가 도움 요청",
    }[normalized]
    text = f"{requester['nickname'] or '가족'}님의 응답을 기록했어요.\n{label}"
    return {
        "status": "acknowledged",
        "outbox_id": safe_outbox_id,
        "response": normalized,
        "response_label": label,
        "responder_hint": _mask_account(requester_user_id),
        "message": text,
        "message_json": {
            "version": "2.0",
            "template": {
                "outputs": [{"simpleText": {"text": text}}],
                "quickReplies": [
                    {"label": "오늘 요약 보기", "action": "message", "messageText": "오늘 요약 보기"},
                    {"label": "연결 상태 보기", "action": "message", "messageText": "연결 상태 보기"},
                ],
            },
        },
    }


def pause_care_routine(
    requester_user_id: str,
    senior_user_id: str,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        _, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id
        )
        if error:
            return {"error": error}
        if requester["role"] != "senior":
            return {"error": "예약 돌봄 중지는 어르신 계정에서만 할 수 있습니다."}
        routine = _get_settings(conn, senior_user_id)
        if routine is None:
            return {"error": "중지할 예약 돌봄 설정이 없습니다."}
        now = _iso(_utcnow())
        conn.execute(
            """UPDATE care_routines
               SET senior_consented = 0, phone_activity_enabled = 0, updated_at = ?
               WHERE senior_user_id = ?""",
            (now, senior_user_id),
        )
        cancelled = conn.execute(
            """UPDATE care_outbox SET status = 'cancelled'
               WHERE senior_user_id = ? AND status = 'pending'""",
            (senior_user_id,),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    return {
        "status": "paused",
        "phone_activity_enabled": False,
        "senior_consented": False,
        "pending_notifications_cancelled": cancelled,
        "stored_history_deleted": False,
        "message": "예약 질문과 휴대폰 활동 확인을 중지하고 미발송 알림을 취소했습니다. 기존 기록 삭제는 운영 정책에 따른 별도 요청이 필요합니다.",
    }


def get_care_routine_status(
    requester_user_id: str,
    senior_user_id: str,
    *,
    now: str = "",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id, "view_summary"
        )
        if error:
            return {"error": error}
        settings = _get_settings(conn, senior_user_id)
        if settings is None:
            return {"error": "아직 예약 돌봄 설정이 없습니다."}
        tz = ZoneInfo(settings["timezone"])
        try:
            now_local = _parse_datetime(now, tz).astimezone(tz) if now else datetime.now(tz)
        except ValueError:
            return {"error": "now는 ISO 8601 날짜·시간이어야 합니다."}
        snapshot = _activity_snapshot(
            conn, senior_user_id, now_local.astimezone(timezone.utc), tz
        )
        if not settings["senior_consented"] or not settings["phone_activity_enabled"]:
            activity_state = "disabled"
        else:
            activity_state, _, _ = _inactivity_state(settings, snapshot)
        pending = conn.execute(
            """SELECT event_type, severity, due_at, recipient_user_id
               FROM care_outbox
               WHERE senior_user_id = ? AND status = 'pending'
               ORDER BY id DESC LIMIT 10""",
            (senior_user_id,),
        ).fetchall()
        connected_count = conn.execute(
            """SELECT COUNT(*) AS cnt FROM care_memberships
               WHERE circle_id = ? AND status = 'active' AND role != 'senior'""",
            (circle["circle_id"],),
        ).fetchone()["cnt"]
        acknowledgements = conn.execute(
            """SELECT a.response, a.created_at, a.responder_user_id, o.event_type
               FROM care_acknowledgements a
               JOIN care_outbox o ON o.id = a.outbox_id
               WHERE a.senior_user_id = ?
               ORDER BY a.id DESC LIMIT 5""",
            (senior_user_id,),
        ).fetchall()
        safe_daily = _routine_day_summary(
            conn, senior_user_id, now_local.date().isoformat()
        )
    finally:
        conn.close()

    return {
        "status": "active" if settings["senior_consented"] else "paused",
        "circle_name": circle["display_name"],
        "connected_family_count": connected_count,
        "requester_role": requester["role"],
        "settings": settings,
        "today_summary": safe_daily,
        "phone_activity": snapshot,
        "activity_state": activity_state,
        "pending_notifications": [
            {
                "event_type": row["event_type"],
                "severity": row["severity"],
                "due_at": row["due_at"],
                "recipient_hint": _mask_account(row["recipient_user_id"]),
            }
            for row in pending
        ],
        "recent_family_actions": [
            {
                "event_type": row["event_type"],
                "response": row["response"],
                "responder_hint": _mask_account(row["responder_user_id"]),
                "created_at": row["created_at"],
            }
            for row in acknowledgements
        ],
        "privacy": {
            "exact_location_collected": False,
            "screen_content_collected": False,
            "raw_sensor_data_collected": False,
            "family_receives_raw_chat": False,
        },
    }


def claim_pending_notifications(
    *, limit: int = 50, now: Optional[datetime] = None, db_path: Optional[str] = None
) -> list[dict[str, Any]]:
    """Internal adapter hook. This is intentionally not exposed as an MCP tool."""
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            """SELECT * FROM care_outbox
               WHERE status = 'pending' AND due_at <= ?
               ORDER BY due_at, id LIMIT ?""",
            (_iso(now or _utcnow()), max(1, min(int(limit), 200))),
        ).fetchall()
    finally:
        conn.close()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            item["payload"] = {}
        results.append(item)
    return results


def mark_notification_delivery(
    outbox_id: int,
    status: str,
    *,
    db_path: Optional[str] = None,
) -> bool:
    """Mark an adapter delivery as sent or failed without exposing it to the model."""
    if status not in {"sent", "failed"}:
        raise ValueError("status must be sent or failed")
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        cursor = conn.execute(
            """UPDATE care_outbox
               SET status = ?, sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END
               WHERE id = ? AND status = 'pending'""",
            (status, status, _iso(_utcnow()), int(outbox_id)),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def manage_care_routine(
    action: str,
    requester_user_id: str,
    senior_user_id: str,
    *,
    prompt_times: Any = "09:00,14:00,20:00",
    digest_times: Any = "14:30,21:00",
    timezone_name: str = "Asia/Seoul",
    response_window_minutes: int = 60,
    inactivity_hours: int = 8,
    escalation_hours: int = 12,
    inactivity_grace_minutes: int = 30,
    inactivity_mode: str = "both",
    quiet_start: str = "22:00",
    quiet_end: str = "07:00",
    phone_activity_enabled: bool = True,
    wearable_enabled: bool = False,
    senior_consented: bool = False,
    event_type: str = "",
    source: str = "phone",
    occurred_at: str = "",
    event_id: str = "",
    now: str = "",
    outbox_id: int = 0,
    response: str = "",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    action = str(action or "status").strip().lower()
    if action == "configure":
        return configure_care_routine(
            requester_user_id,
            senior_user_id,
            prompt_times=prompt_times,
            digest_times=digest_times,
            timezone_name=timezone_name,
            response_window_minutes=response_window_minutes,
            inactivity_hours=inactivity_hours,
            escalation_hours=escalation_hours,
            inactivity_grace_minutes=inactivity_grace_minutes,
            inactivity_mode=inactivity_mode,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            phone_activity_enabled=phone_activity_enabled,
            wearable_enabled=wearable_enabled,
            senior_consented=senior_consented,
            db_path=db_path,
        )
    if action == "record_activity":
        return record_phone_activity(
            requester_user_id,
            senior_user_id,
            event_type,
            source=source,
            occurred_at=occurred_at,
            event_id=event_id,
            db_path=db_path,
        )
    if action == "run_due":
        return run_due_care_tasks(
            requester_user_id, senior_user_id, now=now, db_path=db_path
        )
    if action == "acknowledge":
        return acknowledge_care_notification(
            requester_user_id,
            senior_user_id,
            outbox_id,
            response,
            db_path=db_path,
        )
    if action == "pause":
        return pause_care_routine(
            requester_user_id, senior_user_id, db_path=db_path
        )
    if action == "status":
        return get_care_routine_status(
            requester_user_id, senior_user_id, now=now, db_path=db_path
        )
    return {"error": "action은 configure, record_activity, run_due, acknowledge, status, pause 중 하나여야 합니다."}
