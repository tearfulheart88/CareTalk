"""
알림톡(AlimTalk) 발송 서비스 (스켈레톤)
=========================================
돌봄톡(CareTalk) MCP 서버의 카카오 알림톡 연동 모듈입니다.
위험 감지(emergency_detect) 시 가족·복지사에게
긴급 알림을 발송하는 기능을 제공합니다.

카카오 비즈메시지 API를 통해 알림톡을 발송하며,
실제 API 키 없이도 코드 구조를 완성하여
MVP 개발 시 즉시 연동 가능하도록 설계되었습니다.

기획서 참고: AGENTIC_PLAYER10_돌봄톡_v2_기획서.md 섹션 1.2
공식 문서: https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format

알림톡 주요 특징:
    - 카카오톡 비즈니스 채널 등록 필요
    - 사전 승인된 템플릿 기반 발송 (정보성 메시지)
    - 발송 건당 과금: 약 15~20원/건
    - 야간(20시~08시) 발송 제한 (친구톡은 가능)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

# 실제 환경에서는 httpx 또는 requests 라이브러리 사용
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 상수 정의
# ──────────────────────────────────────────────────────────────

# 위험 레벨 정의 (emergency_detect Tool과 일치)
RISK_LEVEL_RED = "red"        # 긴급: 즉시 119 + 가족 알림
RISK_LEVEL_YELLOW = "yellow"  # 주의: 가족·복지사 알림
RISK_LEVEL_NONE = "none"      # 정상: 알림 불필요

# 위험 레벨별 이모지 + 라벨
RISK_LEVEL_DISPLAY: Dict[str, str] = {
    RISK_LEVEL_RED: "🚨 긴급",
    RISK_LEVEL_YELLOW: "⚠️ 주의",
    RISK_LEVEL_NONE: "✅ 정상",
}

# 위험 키워드 → 한글 설명 매핑
# emergency_detect Tool의 1차 정규표현식 필터와 동일한 키워드셋
DANGER_KEYWORD_DESCRIPTIONS: Dict[str, str] = {
    "어지러워": "어지러움 증상",
    "쓰러졌어": "쓰러짐/실신",
    "숨이 안 쉬어져": "호흡 곤란",
    "가슴이 아파": "흉통/심장 이상",
    "다쳤어": "신체 부상",
    "피가 나": "출혈",
    "머리가 아파": "두통",
    "열이 나": "발열",
    "구토": "구토 증상",
    "설사": "설사 증상",
}

# 알림톡 발송 제한 시간 (야간 20시~08시)
# 야간에는 알림톡 대신 친구톡으로 발송하거나 다음날 오전 8시로 지연
NIGHT_START_HOUR = 20  # 20시부터 야간
NIGHT_END_HOUR = 8     # 08시까지 야간


# ──────────────────────────────────────────────────────────────
# 알림톡 API 함수
# ──────────────────────────────────────────────────────────────

def send_alimtalk(
    api_key: str,
    sender_key: str,
    phone_number: str,
    template_code: str,
    message: str,
    title: Optional[str] = None,
    buttons: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    카카오 알림톡을 발송합니다.

    카카오 비즈메시지 API를 통해 사전 승인된 템플릿 기반으로
    정보성 메시지를 발송합니다. 위험 감지 시 가족·복지사에게
    긴급 알림을 보내는 용도로 사용됩니다.

    실제 사용법:
        1. [카카오 비즈니스](https://business.kakao.com/)에서 비즈니스 채널 개설
        2. 알림톡 템플릿 등록 및 승인 (카카오 검수, 약 1~3일 소요)
        3. 발신 프로필(sender_key) 생성
        4. 아래 함수로 알림톡 발송

        result = send_alimtalk(
            api_key="YOUR_BIZ_API_KEY",
            sender_key="YOUR_SENDER_KEY",
            phone_number="01012345678",
            template_code="EMERGENCY_001",
            message="[돌봄톡] 김순자님에게 위험 신호가 감지되었습니다.",
            title="긴급 돌봄 알림",
        )

    API 엔드포인트 (카카오 비즈메시지 v2):
        POST https://api.kakao.com/v2/api/talk/message/send
        Header:
            Authorization: Bearer {API_KEY}
            Content-Type: application/json

    Args:
        api_key: 카카오 비즈메시지 API 키 (비즈니스 채널에서 발급)
        sender_key: 발신 프로필 키 (비즈니스 채널에 등록된 발신 프로필)
        phone_number: 수신자 전화번호 (국가번호 포함, 예: "01012345678")
        template_code: 사전 승인된 알림톡 템플릿 코드 (예: "EMERGENCY_001")
        message: 발송할 메시지 내용 (템플릿에 정의된 변수 치환된 최종 텍스트)
        title: 알림톡 제목 (선택, 템플릿에 따라 다름)
        buttons: 알림톡 하단 버튼 리스트 (선택).
                 예: [{"name": "전화하기", "type": "WL", "url_mobile": "tel:01012345678"}]

    Returns:
        발송 결과 딕셔너리 (성공 시):
        {
            "code": 0,
            "message": "success",
            "message_id": "MSG1234567890"
        }

    Raises:
        RuntimeError: API 호출 실패 시 (잘못된 키, 템플릿 미승인, 야간 발송 제한 등)
        ImportError: requests 라이브러리가 설치되지 않은 경우
    """
    if not HAS_REQUESTS:
        raise ImportError(
            "requests 라이브러리가 필요합니다. 'pip install requests'로 설치하세요."
        )

    # ── 요청 페이로드 구성 ──
    # 카카오 비즈메시지 API v2 스펙에 맞춰 JSON body 구성
    payload: Dict[str, Any] = {
        "sender_key": sender_key,
        "phone_number": phone_number,
        "template_code": template_code,
        "message": message,
    }

    if title:
        payload["title"] = title

    if buttons:
        payload["buttons"] = buttons

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 카카오 비즈메시지 API 엔드포인트
    api_url = "https://api.kakao.com/v2/api/talk/message/send"

    logger.info(
        f"알림톡 발송 요청: template_code={template_code}, "
        f"phone_number={phone_number[-4:]}****"
    )

    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()

        if result.get("code") == 0:
            logger.info(f"알림톡 발송 성공: message_id={result.get('message_id')}")
        else:
            logger.warning(f"알림톡 발송 응답: code={result.get('code')}, message={result.get('message')}")

        return result

    except requests.exceptions.RequestException as e:
        logger.error(f"알림톡 발송 실패: {e}")
        raise RuntimeError(f"알림톡 발송 중 오류 발생: {e}") from e


# ──────────────────────────────────────────────────────────────
# 메시지 포맷팅 함수
# ──────────────────────────────────────────────────────────────

def format_emergency_message(
    risk_level: str,
    user_name: str,
    detected_keywords: List[str],
    user_message: Optional[str] = None,
) -> str:
    """
    위험 감지 시 가족·복지사에게 발송할 긴급 알림 메시지를 포맷팅합니다.

    emergency_detect Tool의 출력을 기반으로
    사람이 읽기 쉬운 자연어 알림 메시지를 생성합니다.

    실제 사용법:
        msg = format_emergency_message(
            risk_level="red",
            user_name="김순자",
            detected_keywords=["쓰러졌어", "어지러워"],
            user_message="아이고, 쓰러졌어. 너무 어지러워...",
        )
        # → send_alimtalk()의 message 파라미터로 전달

    Args:
        risk_level: 위험 레벨 ("red" 또는 "yellow")
        user_name: 노인 사용자 이름 (닉네임)
        detected_keywords: 감지된 위험 키워드 리스트
        user_message: 사용자의 원본 메시지 (선택). 포함 시 맥락 제공

    Returns:
        포맷팅된 긴급 알림 메시지 문자열
    """
    level_display = RISK_LEVEL_DISPLAY.get(risk_level, risk_level)

    # ── 키워드 설명 변환 ──
    keyword_descs = []
    for kw in detected_keywords:
        desc = DANGER_KEYWORD_DESCRIPTIONS.get(kw, kw)
        keyword_descs.append(desc)

    keywords_text = ", ".join(keyword_descs) if keyword_descs else "위험 신호"

    # ── 메시지 본문 조립 ──
    lines = [
        f"[돌봄톡] {level_display} 알림",
        "",
        f"{user_name}님에게 위험 신호가 감지되었습니다.",
        f"감지 키워드: {keywords_text}",
    ]

    if user_message:
        # 사용자 메시지가 너무 길면 앞부분만 포함 (개인정보 보호 + 가독성)
        truncated = user_message[:100] + "..." if len(user_message) > 100 else user_message
        lines.append(f"사용자 메시지: \"{truncated}\"")

    if risk_level == RISK_LEVEL_RED:
        lines.append("")
        lines.append("🚨 즉시 119 신고 또는 직접 방문 확인이 필요합니다.")
        lines.append("긴급 상황으로 판단되니 신속히 대응해 주세요.")
    elif risk_level == RISK_LEVEL_YELLOW:
        lines.append("")
        lines.append("⚠️ 사용자 상태 확인이 필요합니다.")
        lines.append("전화 또는 방문을 통해 안부를 확인해 주세요.")

    lines.append("")
    lines.append("— 돌봄톡(CareTalk) AI 돌봄 에이전트")

    return "\n".join(lines)


def format_report_message(
    user_name: str,
    summary_text: str,
    report_period: str = "weekly",
    alert_items: Optional[List[str]] = None,
) -> str:
    """
    가족용 주간 리포트 알림 메시지를 포맷팅합니다.

    family_report Tool의 출력을 기반으로
    알림톡으로 발송할 간결한 리포트 요약 메시지를 생성합니다.

    실제 사용법:
        msg = format_report_message(
            user_name="김순자",
            summary_text="안부 응답률 100%, 주간 기분 양호, 혈압 안정적",
            report_period="weekly",
            alert_items=["금요일 우울감 표현", "수요일 혈압 145"],
        )
        # → send_alimtalk()의 message 파라미터로 전달

    Args:
        user_name: 노인 사용자 이름 (닉네임)
        summary_text: family_report Tool에서 생성된 요약 텍스트
        report_period: 리포트 기간 ("daily" 또는 "weekly")
        alert_items: 주의 항목 리스트 (선택). 이상 패턴이 있을 경우 포함

    Returns:
        포맷팅된 리포트 알림 메시지 문자열
    """
    period_display = "일일" if report_period == "daily" else "주간"

    lines = [
        f"[돌봄톡] {user_name}님 {period_display} 돌봄 리포트",
        "",
        summary_text,
    ]

    if alert_items:
        lines.append("")
        lines.append("⚠️ 주의 항목:")
        for item in alert_items:
            lines.append(f"  • {item}")

    lines.append("")
    lines.append("💡 자세한 내용은 돌봄톡 Widget에서 확인하실 수 있습니다.")
    lines.append("")
    lines.append("— 돌봄톡(CareTalk) AI 돌봄 에이전트")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────────────────────────

def is_night_time() -> bool:
    """
    현재 시간이 알림톡 발송 제한 시간(야간 20시~08시)인지 확인합니다.

    알림톡은 야간 발송이 제한되므로, 야간에는 친구톡으로 대체하거나
    다음날 오전 8시로 발송을 지연해야 합니다.

    Returns:
        True이면 야간 시간대 (알림톡 발송 제한)
    """
    import datetime
    now = datetime.datetime.now()
    current_hour = now.hour
    return current_hour >= NIGHT_START_HOUR or current_hour < NIGHT_END_HOUR


# ──────────────────────────────────────────────────────────────
# 모듈 직접 실행 테스트
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("알림톡 모듈 테스트 (메시지 포맷팅 검증)")
    print("=" * 60)

    # ── 테스트 1: 긴급 알림 메시지 (RED) ──
    print("\n[테스트 1] format_emergency_message - RED 레벨")
    red_msg = format_emergency_message(
        risk_level="red",
        user_name="김순자",
        detected_keywords=["쓰러졌어", "어지러워"],
        user_message="아이고, 쓰러졌어. 너무 어지러워...",
    )
    print(red_msg)
    print("─" * 40)

    # ── 테스트 2: 긴급 알림 메시지 (YELLOW) ──
    print("\n[테스트 2] format_emergency_message - YELLOW 레벨")
    yellow_msg = format_emergency_message(
        risk_level="yellow",
        user_name="박영희",
        detected_keywords=["머리가 아파", "열이 나"],
    )
    print(yellow_msg)
    print("─" * 40)

    # ── 테스트 3: 긴급 알림 메시지 (YELLOW, 단일 키워드) ──
    print("\n[테스트 3] format_emergency_message - YELLOW, 단일 키워드")
    single_msg = format_emergency_message(
        risk_level="yellow",
        user_name="이철수",
        detected_keywords=["어지러워"],
        user_message="오늘 좀 어지러워서 누워있었어",
    )
    print(single_msg)
    print("─" * 40)

    # ── 테스트 4: 주간 리포트 메시지 ──
    print("\n[테스트 4] format_report_message - 주간 리포트")
    report_msg = format_report_message(
        user_name="김순자",
        summary_text="안부 응답률: 100% (7/7일)\n주간 기분: 😊😊😊😊😐😊😊\n혈압 추이: 안정적 (128~135)",
        report_period="weekly",
        alert_items=["금요일 우울감 표현", "수요일 혈압 145"],
    )
    print(report_msg)
    print("─" * 40)

    # ── 테스트 5: 일일 리포트 메시지 (주의 항목 없음) ──
    print("\n[테스트 5] format_report_message - 일일 리포트 (정상)")
    daily_msg = format_report_message(
        user_name="박영희",
        summary_text="오늘 기분: 😊 좋아요\n건강 키워드: 없음\n특이사항: 없음",
        report_period="daily",
    )
    print(daily_msg)
    print("─" * 40)

    # ── 테스트 6: 야간 시간 확인 ──
    print("\n[테스트 6] is_night_time()")
    night = is_night_time()
    print(f"현재 야간 시간대: {night}")
    if night:
        print("⚠️ 알림톡 발송 제한 시간입니다. 친구톡으로 대체하거나 지연 발송하세요.")
    else:
        print("✅ 알림톡 발송 가능 시간입니다.")

    # ── 테스트 7: requests 미설치 시 send_alimtalk 동작 ──
    print("\n[테스트 7] send_alimtalk - requests 미설치 시 ImportError")
    if not HAS_REQUESTS:
        try:
            send_alimtalk(
                api_key="test",
                sender_key="test",
                phone_number="01012345678",
                template_code="TEST",
                message="test",
            )
            print("❌ ImportError가 발생해야 하는데 발생하지 않음")
        except ImportError as e:
            print(f"✅ 예상된 ImportError: {e}")
    else:
        print("ℹ️ requests 설치됨 — 실제 API 호출은 API 키가 필요하므로 스킵")

    print("\n" + "=" * 60)
    print("알림톡 모듈 테스트 완료!")
    print("=" * 60)
