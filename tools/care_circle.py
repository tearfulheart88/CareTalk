"""Consent-first family account linking for CareTalk."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from db.schema import DB_PATH, ensure_schema


ALLOWED_ROLES = {"family", "guardian", "helper"}
PERMISSION_ORDER = (
    "view_summary",
    "receive_inactivity_alerts",
    "receive_emergency_alerts",
    "manage_schedule",
)
DEFAULT_PERMISSIONS = {
    "family": ["view_summary", "receive_inactivity_alerts", "receive_emergency_alerts"],
    "guardian": list(PERMISSION_ORDER),
    "helper": ["view_summary", "receive_inactivity_alerts"],
}
SENIOR_PERMISSIONS = list(PERMISSION_ORDER)


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


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_invite(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _account_hint(account_user_id: str) -> str:
    text = str(account_user_id or "")
    if len(text) <= 2:
        return "**"
    return text[:2] + "*" * min(6, len(text) - 2)


def _permissions(value: Any, role: str) -> list[str]:
    if value in (None, ""):
        requested: Iterable[str] = DEFAULT_PERMISSIONS.get(role, [])
    elif isinstance(value, str):
        requested = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        requested = [str(item).strip() for item in value if str(item).strip()]
    else:
        requested = []
    requested_set = set(requested)
    return [permission for permission in PERMISSION_ORDER if permission in requested_set]


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _message_json(text: str, replies: list[str]) -> dict[str, Any]:
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": [
                {"label": reply, "action": "message", "messageText": reply}
                for reply in replies
            ],
        },
    }


def _find_circle(conn: sqlite3.Connection, senior_user_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM care_circles WHERE senior_user_id = ?",
        (senior_user_id,),
    ).fetchone()


def _active_membership(
    conn: sqlite3.Connection, circle_id: str, account_user_id: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM care_memberships
           WHERE circle_id = ? AND account_user_id = ? AND status = 'active'""",
        (circle_id, account_user_id),
    ).fetchone()


def authorize_circle_member(
    conn: sqlite3.Connection,
    senior_user_id: str,
    requester_user_id: str,
    permission: str = "",
) -> tuple[Optional[sqlite3.Row], Optional[sqlite3.Row], Optional[str]]:
    """Authorize an active member. The authenticated adapter must inject requester_user_id."""
    circle = _find_circle(conn, senior_user_id)
    if circle is None:
        return None, None, "먼저 어르신 동의로 돌봄 연결망을 만들어 주세요."
    membership = _active_membership(conn, circle["circle_id"], requester_user_id)
    if membership is None:
        return circle, None, "연결된 계정만 이 정보를 볼 수 있습니다."
    if permission and membership["role"] != "senior":
        granted = _json_list(membership["permissions"])
        if permission not in granted:
            return circle, membership, "이 계정에는 해당 기능 권한이 없습니다."
    return circle, membership, None


def _ensure_circle(
    conn: sqlite3.Connection,
    senior_user_id: str,
    nickname: str,
    circle_name: str,
) -> sqlite3.Row:
    circle = _find_circle(conn, senior_user_id)
    now = _iso(_utcnow())
    if circle is None:
        circle_id = "circle_" + secrets.token_hex(8)
        conn.execute(
            """INSERT INTO care_circles
               (circle_id, senior_user_id, display_name, senior_consented, consented_at)
               VALUES (?, ?, ?, 1, ?)""",
            (circle_id, senior_user_id, circle_name or "우리 가족 돌봄", now),
        )
    else:
        circle_id = circle["circle_id"]
        conn.execute(
            """UPDATE care_circles
               SET display_name = ?, senior_consented = 1,
                   consented_at = COALESCE(consented_at, ?)
               WHERE circle_id = ?""",
            (circle_name or circle["display_name"], now, circle_id),
        )

    display_name = nickname or "어르신"
    conn.execute(
        """INSERT INTO users (user_id, nickname, user_type)
           VALUES (?, ?, 'senior')
           ON CONFLICT(user_id) DO UPDATE SET nickname = excluded.nickname""",
        (senior_user_id, display_name),
    )
    conn.execute(
        """INSERT INTO care_memberships
           (circle_id, account_user_id, nickname, role, permissions, status, joined_at, updated_at)
           VALUES (?, ?, ?, 'senior', ?, 'active', ?, ?)
           ON CONFLICT(circle_id, account_user_id) DO UPDATE SET
             nickname = excluded.nickname,
             role = 'senior',
             permissions = excluded.permissions,
             status = 'active',
             updated_at = excluded.updated_at""",
        (circle_id, senior_user_id, display_name, json.dumps(SENIOR_PERMISSIONS), now, now),
    )
    return _find_circle(conn, senior_user_id)  # type: ignore[return-value]


def create_circle_invite(
    requester_user_id: str,
    senior_user_id: str,
    *,
    nickname: str = "어르신",
    circle_name: str = "우리 가족 돌봄",
    role: str = "family",
    permissions: Any = "",
    senior_consented: bool = False,
    invite_hours: int = 24,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    if not senior_consented:
        return {"error": "어르신이 연결 대상과 공유 범위에 동의한 뒤 초대할 수 있습니다."}
    if requester_user_id != senior_user_id:
        return {"error": "첫 연결 초대는 어르신의 인증된 계정에서 만들어야 합니다."}
    if role not in ALLOWED_ROLES:
        return {"error": "role은 family, guardian, helper 중 하나여야 합니다."}
    if not 1 <= int(invite_hours) <= 72:
        return {"error": "invite_hours는 1~72시간이어야 합니다."}

    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle = _ensure_circle(conn, senior_user_id, nickname, circle_name)
        code = secrets.token_hex(6).upper()
        invite_id = "invite_" + secrets.token_hex(8)
        expires_at = _iso(_utcnow() + timedelta(hours=int(invite_hours)))
        granted = _permissions(permissions, role)
        conn.execute(
            """INSERT INTO care_invites
               (invite_id, circle_id, token_hash, role, permissions, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                invite_id,
                circle["circle_id"],
                _hash_invite(code),
                role,
                json.dumps(granted, ensure_ascii=False),
                expires_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    text = (
        "가족 연결 초대가 준비됐어요.\n\n"
        f"초대코드: {code}\n"
        f"{invite_hours}시간 안에 한 번만 사용할 수 있어요.\n\n"
        "코드는 지정한 가족에게만 전달해 주세요."
    )
    return {
        "status": "invite_created",
        "invite_code": code,
        "expires_at": expires_at,
        "role": role,
        "permissions": granted,
        "one_time_secret": True,
        "stored_in_plaintext": False,
        "message": text,
        "message_json": _message_json(text, ["연결 상태 보기", "초대 취소 방법", "공유 범위 보기"]),
    }


def join_circle(
    requester_user_id: str,
    invite_code: str,
    *,
    nickname: str = "가족",
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    if not invite_code.strip():
        return {"error": "invite_code가 필요합니다."}
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        invite = conn.execute(
            """SELECT i.*, c.senior_user_id, c.display_name, c.senior_consented
               FROM care_invites i
               JOIN care_circles c ON c.circle_id = i.circle_id
               WHERE i.token_hash = ? AND i.status = 'active'""",
            (_hash_invite(invite_code),),
        ).fetchone()
        if invite is None:
            conn.rollback()
            return {"error": "초대코드가 올바르지 않거나 이미 사용되었습니다."}
        if _parse_iso(invite["expires_at"]) <= _utcnow():
            conn.execute(
                "UPDATE care_invites SET status = 'expired' WHERE invite_id = ?",
                (invite["invite_id"],),
            )
            conn.commit()
            return {"error": "초대코드가 만료되었습니다. 어르신 계정에서 다시 초대해 주세요."}
        if not bool(invite["senior_consented"]):
            conn.rollback()
            return {"error": "어르신의 공유 동의가 철회되어 연결할 수 없습니다."}
        if requester_user_id == invite["senior_user_id"]:
            conn.rollback()
            return {"error": "어르신 본인 계정은 이미 연결되어 있습니다."}

        now = _iso(_utcnow())
        granted = _json_list(invite["permissions"])
        conn.execute(
            """INSERT INTO users (user_id, nickname, user_type)
               VALUES (?, ?, 'family')
               ON CONFLICT(user_id) DO UPDATE SET nickname = excluded.nickname""",
            (requester_user_id, nickname or "가족"),
        )
        conn.execute(
            """INSERT INTO care_memberships
               (circle_id, account_user_id, nickname, role, permissions, status, joined_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
               ON CONFLICT(circle_id, account_user_id) DO UPDATE SET
                 nickname = excluded.nickname,
                 role = excluded.role,
                 permissions = excluded.permissions,
                 status = 'active',
                 joined_at = excluded.joined_at,
                 updated_at = excluded.updated_at""",
            (
                invite["circle_id"],
                requester_user_id,
                nickname or "가족",
                invite["role"],
                json.dumps(granted, ensure_ascii=False),
                now,
                now,
            ),
        )
        updated = conn.execute(
            """UPDATE care_invites
               SET status = 'used', used_by = ?, used_at = ?
               WHERE invite_id = ? AND status = 'active'""",
            (requester_user_id, now, invite["invite_id"]),
        )
        if updated.rowcount != 1:
            conn.rollback()
            return {"error": "초대코드가 방금 사용되었습니다. 새 코드를 요청해 주세요."}
        conn.commit()
    finally:
        conn.close()

    text = (
        f"{invite['display_name']} 연결이 완료됐어요.\n\n"
        "가족 리포트와 허용된 알림만 받을 수 있어요.\n"
        "어르신은 언제든 권한을 바꾸거나 연결을 끊을 수 있어요."
    )
    return {
        "status": "connected",
        "circle_name": invite["display_name"],
        "role": invite["role"],
        "permissions": granted,
        "senior_account_hint": _account_hint(invite["senior_user_id"]),
        "message": text,
        "message_json": _message_json(text, ["오늘 요약 보기", "알림 설정 보기", "연결 해제"]),
    }


def list_circle(
    requester_user_id: str,
    senior_user_id: str,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id
        )
        if error:
            return {"error": error}
        rows = conn.execute(
            """SELECT account_user_id, nickname, role, permissions, joined_at
               FROM care_memberships
               WHERE circle_id = ? AND status = 'active'
               ORDER BY CASE WHEN role = 'senior' THEN 0 ELSE 1 END, joined_at""",
            (circle["circle_id"],),
        ).fetchall()
    finally:
        conn.close()

    members = [
        {
            "account_hint": _account_hint(row["account_user_id"]),
            "nickname": row["nickname"],
            "role": row["role"],
            "permissions": _json_list(row["permissions"]),
            "is_requester": row["account_user_id"] == requester_user_id,
        }
        for row in rows
    ]
    family_count = sum(1 for member in members if member["role"] != "senior")
    return {
        "status": "active",
        "circle_name": circle["display_name"],
        "senior_consented": bool(circle["senior_consented"]),
        "connected_family_count": family_count,
        "members": members,
        "requester_role": requester["role"],
        "sharing_default": "요약과 허용된 알림만 공유하며 원문 대화와 정확한 위치는 공유하지 않습니다.",
    }


def update_member_permissions(
    requester_user_id: str,
    senior_user_id: str,
    target_user_id: str,
    permissions: Any,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id
        )
        if error:
            return {"error": error}
        if requester["role"] != "senior":
            return {"error": "공유 권한은 어르신 계정에서만 바꿀 수 있습니다."}
        target = _active_membership(conn, circle["circle_id"], target_user_id)
        if target is None or target["role"] == "senior":
            return {"error": "권한을 바꿀 활성 가족 계정을 찾지 못했습니다."}
        granted = _permissions(permissions, target["role"])
        now = _iso(_utcnow())
        conn.execute(
            """UPDATE care_memberships
               SET permissions = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(granted, ensure_ascii=False), now, target["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "status": "permissions_updated",
        "target_account_hint": _account_hint(target_user_id),
        "permissions": granted,
        "message": "선택한 가족 계정의 공유 범위를 변경했습니다.",
    }


def revoke_member(
    requester_user_id: str,
    senior_user_id: str,
    target_user_id: str,
    *,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = _connect(path)
    try:
        circle, requester, error = authorize_circle_member(
            conn, senior_user_id, requester_user_id
        )
        if error:
            return {"error": error}
        if requester["role"] != "senior" and requester_user_id != target_user_id:
            return {"error": "어르신은 가족 연결을 해제할 수 있고, 가족은 자기 연결만 해제할 수 있습니다."}
        if target_user_id == senior_user_id:
            return {"error": "어르신 계정은 연결망에서 해제할 수 없습니다."}
        target = _active_membership(conn, circle["circle_id"], target_user_id)
        if target is None:
            return {"error": "활성 연결을 찾지 못했습니다."}
        now = _iso(_utcnow())
        conn.execute(
            """UPDATE care_memberships
               SET status = 'revoked', updated_at = ? WHERE id = ?""",
            (now, target["id"]),
        )
        conn.execute(
            """UPDATE care_outbox SET status = 'cancelled'
               WHERE recipient_user_id = ? AND senior_user_id = ? AND status = 'pending'""",
            (target_user_id, senior_user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "status": "revoked",
        "target_account_hint": _account_hint(target_user_id),
        "pending_notifications_cancelled": True,
        "message": "가족 연결과 아직 발송되지 않은 알림을 해제했습니다.",
    }


def manage_care_circle(
    action: str,
    requester_user_id: str,
    senior_user_id: str = "",
    *,
    nickname: str = "",
    invite_code: str = "",
    role: str = "family",
    permissions: Any = "",
    target_user_id: str = "",
    circle_name: str = "우리 가족 돌봄",
    senior_consented: bool = False,
    invite_hours: int = 24,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Dispatch care-circle actions while keeping account linking explicit."""
    action = str(action or "list").strip().lower()
    if action == "create_invite":
        if not senior_user_id:
            return {"error": "senior_user_id가 필요합니다."}
        return create_circle_invite(
            requester_user_id,
            senior_user_id,
            nickname=nickname or "어르신",
            circle_name=circle_name,
            role=role,
            permissions=permissions,
            senior_consented=senior_consented,
            invite_hours=invite_hours,
            db_path=db_path,
        )
    if action == "join":
        return join_circle(
            requester_user_id,
            invite_code,
            nickname=nickname or "가족",
            db_path=db_path,
        )
    if not senior_user_id:
        return {"error": "senior_user_id가 필요합니다."}
    if action == "list":
        return list_circle(requester_user_id, senior_user_id, db_path=db_path)
    if action == "update_permissions":
        if not target_user_id:
            return {"error": "target_user_id가 필요합니다."}
        return update_member_permissions(
            requester_user_id,
            senior_user_id,
            target_user_id,
            permissions,
            db_path=db_path,
        )
    if action == "revoke":
        if not target_user_id:
            return {"error": "target_user_id가 필요합니다."}
        return revoke_member(
            requester_user_id,
            senior_user_id,
            target_user_id,
            db_path=db_path,
        )
    return {
        "error": "action은 create_invite, join, list, update_permissions, revoke 중 하나여야 합니다."
    }
