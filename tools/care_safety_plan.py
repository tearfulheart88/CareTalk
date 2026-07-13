"""Consent-first, human-in-the-loop care safety plan."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_PHONE_PATTERN = re.compile(r"(?:01[016789]|0\d{1,2})[- .]?\d{3,4}[- .]?\d{4}")
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_RESIDENT_ID_PATTERN = re.compile(r"\b\d{6}[- ]?[1-4]\d{6}\b")
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _split_roles(value: str) -> list[str]:
    roles = [item.strip() for item in re.split(r"[,/·]", value) if item.strip()]
    unique = []
    for role in roles:
        if role not in unique:
            unique.append(role)
    return unique[:4]


def _accessibility_design(needs: str) -> list[str]:
    lowered = needs.lower()
    actions = ["한 화면에 한 질문만 표시하고 선택지는 네 개 이하로 유지"]
    if any(word in lowered for word in ("시력", "글씨", "저시력", "눈")):
        actions.append("큰 글씨와 높은 명암, 짧은 문장으로 표시")
    if any(word in lowered for word in ("청력", "귀", "난청")):
        actions.append("소리만 사용하지 않고 모든 안내를 텍스트로 함께 표시")
    if any(word in lowered for word in ("손", "떨림", "운동", "터치")):
        actions.append("충분히 큰 선택 영역과 되돌리기 가능한 입력 제공")
    if any(word in lowered for word in ("인지", "기억", "치매")):
        actions.append("같은 표현과 같은 순서로 반복하고 보호자 검토를 우선")
    if len(actions) == 1:
        actions.append("'괜찮아요·도움 필요·나중에 답할게요·오늘은 쉴래요' 고정 선택지 제공")
    return actions


def _cards(
    nickname: str,
    checkin_time: str,
    window: int,
    roles: list[str],
    consented: bool,
    escalation: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "type": "care_agreement",
            "title": f"{nickname}님의 돌봄 안전약속",
            "description": "AI가 대신 판단하는 계획이 아니라 당사자와 가족이 함께 확인할 초안입니다.",
            "tags": ["동의 확인" if consented else "동의 전 초안", "사람 확인 필수"],
            "items": [
                {"label": "안부 시간", "value": checkin_time},
                {"label": "응답 여유", "value": f"{window}시간"},
                {"label": "확인 역할", "value": ", ".join(roles)},
                {"label": "중지 방법", "value": "'중지' 또는 '오늘은 쉴래요'라고 답하기"},
            ],
            "buttons": [
                {"label": "안전약속 읽기", "action": "message", "value": "돌봄 안전약속을 쉬운 말로 읽어줘"},
                {"label": "수정할 점 말하기", "action": "message", "value": "안전계획에서 바꾸고 싶은 점이 있어"},
            ],
        },
        {
            "type": "escalation",
            "title": "응답이 없을 때의 단계",
            "description": "무응답만으로 응급상황이라고 단정하지 않습니다.",
            "tags": ["단계적 확인", "119 자동신고 없음"],
            "items": [
                {"label": item["stage"], "value": item["action"]}
                for item in escalation
            ],
            "buttons": [],
        },
    ]


def build_care_safety_plan(
    user_id: str,
    nickname: str = "어르신",
    checkin_time: str = "09:00",
    response_window_hours: int = 2,
    contact_roles: str = "가족, 복지사",
    accessibility_needs: str = "",
    senior_consented: bool = False,
) -> dict[str, Any]:
    """Build a non-operational safety-plan draft without storing personal contacts."""
    user_id = str(user_id or "").strip()
    nickname = str(nickname or "").strip() or "어르신"
    checkin_time = str(checkin_time or "").strip()
    contact_roles = str(contact_roles or "").strip()
    accessibility_needs = str(accessibility_needs or "").strip()

    if not user_id:
        return {"error": "user_id는 필수입니다."}
    if len(user_id) > 128 or len(nickname) > 40:
        return {"error": "user_id는 128자, nickname은 40자 이하여야 합니다."}
    if not _TIME_PATTERN.fullmatch(checkin_time):
        return {"error": "checkin_time은 24시간제 HH:MM 형식이어야 합니다."}
    if len(contact_roles) > 120 or len(accessibility_needs) > 300:
        return {"error": "contact_roles는 120자, accessibility_needs는 300자 이하여야 합니다."}
    if _PHONE_PATTERN.search(contact_roles):
        return {"error": "전화번호는 입력하지 말고 '딸·아들·이웃·복지사'처럼 관계 역할만 적어주세요."}
    if any(char.isdigit() for char in contact_roles) or _EMAIL_PATTERN.search(contact_roles) or _RESIDENT_ID_PATTERN.search(contact_roles):
        return {"error": "주소·이메일·식별번호 같은 개인정보는 입력하지 말고 관계 역할만 적어주세요."}
    if isinstance(response_window_hours, bool):
        return {"error": "response_window_hours는 1~24 사이의 숫자여야 합니다."}
    try:
        window = int(response_window_hours)
    except (TypeError, ValueError):
        return {"error": "response_window_hours는 1~24 사이의 숫자여야 합니다."}
    if not 1 <= window <= 24:
        return {"error": "response_window_hours는 1~24 사이여야 합니다."}
    if not isinstance(senior_consented, bool):
        return {"error": "senior_consented는 true 또는 false여야 합니다."}

    roles = _split_roles(contact_roles) or ["가족", "복지사"]
    escalation = [
        {
            "stage": "정시",
            "after_hours": 0,
            "risk_level": "none",
            "action": "쉬운 문장과 큰 선택지로 안부를 한 번 묻습니다.",
            "human_required": False,
        },
        {
            "stage": f"{window}시간 후",
            "after_hours": window,
            "risk_level": "none",
            "action": "재촉하지 않는 확인 메시지를 한 번 더 보내도록 요청합니다.",
            "human_required": False,
        },
        {
            "stage": f"{window * 2}시간 후",
            "after_hours": window * 2,
            "risk_level": "yellow",
            "action": f"{', '.join(roles)} 중 지정된 사람이 전화 또는 방문 여부를 직접 판단합니다.",
            "human_required": True,
        },
        {
            "stage": "명시적 위급 증상",
            "after_hours": None,
            "risk_level": "red",
            "action": "흉통·호흡곤란·의식저하 등 현재 위급 증상이 있으면 즉시 119 연락을 안내합니다.",
            "human_required": True,
        },
    ]
    plan_seed = "|".join((user_id, checkin_time, str(window), ",".join(roles)))
    plan_id = "care-" + hashlib.sha256(plan_seed.encode("utf-8")).hexdigest()[:10]
    status = "ready_for_human_review" if senior_consented else "draft_requires_senior_consent"
    return {
        "source": "deterministic_safety_plan",
        "plan_id": plan_id,
        "status": status,
        "message": (
            f"{nickname}님이 직접 확인해야 하는 돌봄 안전계획 초안을 만들었습니다."
            if not senior_consented
            else f"{nickname}님 동의가 표시된 안전계획입니다. 실제 연락 역할과 운영자는 최종 확인이 필요합니다."
        ),
        "senior": {"nickname": nickname, "consent_recorded": senior_consented},
        "schedule": {"checkin_time": checkin_time, "response_window_hours": window},
        "contact_roles": roles,
        "quick_replies": ["괜찮아요", "도움이 필요해요", "나중에 답할게요", "오늘은 쉴래요"],
        "accessibility_design": _accessibility_design(accessibility_needs),
        "consent_checklist": [
            "안부 확인 시간과 빈도를 당사자가 선택했는지 확인",
            "가족·복지사에게 공유되는 항목을 당사자가 이해했는지 확인",
            "언제든 '중지'라고 말해 계획을 멈출 수 있음을 안내",
            "실제 연락 역할과 부재 시 대체 담당자를 사람이 확인",
        ],
        "escalation_steps": escalation,
        "kakao_cards": _cards(nickname, checkin_time, window, roles, senior_consented, escalation),
        "privacy_notice": "전화번호·주소·진료기록을 입력하거나 저장하지 않고 관계 역할만 사용했습니다.",
        "limitations": "이 도구는 계획 초안만 만들며 메시지 발송, 전화, 보호자 통보 또는 119 신고를 수행하지 않습니다.",
        "next_action": "당사자에게 계획을 쉬운 말로 읽어드리고 수정·동의 여부를 직접 확인하세요.",
    }
