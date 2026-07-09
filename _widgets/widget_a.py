# -*- coding: utf-8 -*-
"""
돌봄톡 (CareTalk) - Widget A: 노인용 "오늘의 돌봄"
==================================================
노인 사용자가 복잡한 조작 없이 한눈에 오늘 상태를 확인할 수 있는 Widget.
SimpleText + quickReplies 조합으로 구성됩니다.

MVP 스펙 (기획서 섹션 6.2):
  - SimpleText: 오늘 기분, 날씨, 건강 팁, 안부 연속 기록
  - quickReplies: 건강 체크, 가족에게 전화, 오늘 식단 (최대 3개)

Kakao Tools Widget JSON v2.0 스펙 준수.

작성일: 2026-06-21
"""

import sys
import os
from typing import Optional, Dict, Any, List
from datetime import datetime

# 프로젝트 루트를 import 경로에 추가 (standalone 실행 + 서버 import 모두 대응)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.schema import get_user, get_checkin_stats

WIDGET_NAME = "daily_care_widget"
WIDGET_DESCRIPTION = (
    "노인 사용자용 '오늘의 돌봄' Widget. "
    "SimpleText로 오늘의 안부 상태·날씨·건강 팁을 표시하고, "
    "quickReplies로 건강 체크·가족 전화·식단 추천을 제공합니다."
)


def create_daily_care_widget(
    user_id: str,
    weather_info: Optional[Dict[str, Any]] = None,
    checkin_status: Optional[Dict[str, Any]] = None,
    nickname: Optional[str] = None
) -> Dict[str, Any]:
    """
    '오늘의 돌봄' Widget(카카오 스킬 응답 v2.0)을 생성한다.

    Args:
        user_id: 카카오 채널 사용자 키
        weather_info: {"condition", "temp", "advice"} (None이면 계절별 mock 날씨)
        checkin_status: {"sentiment", "streak"} (None이면 DB에서 조회)
        nickname: 사용자 닉네임 (None이면 DB에서 조회, 그래도 없으면 "어르신")

    Returns:
        카카오 챗봇 스킬 응답 v2.0 JSON (simpleText + quickReplies)
    """
    # ── 닉네임: 인자 우선 → DB 조회 → 기본값 (어떤 경우에도 예외로 죽지 않음) ──
    if nickname is None:
        try:
            user = get_user(user_id)
            nickname = user.get("nickname", "어르신") if user else "어르신"
        except Exception:
            nickname = "어르신"

    # ── 감정: checkin_status 우선 → 오늘자 DB 통계 ──
    if checkin_status is not None and checkin_status.get("sentiment"):
        today_sentiment: Optional[str] = checkin_status.get("sentiment")
    else:
        try:
            today_sentiment = _get_today_sentiment(get_checkin_stats(user_id, days=1))
        except Exception:
            today_sentiment = "neutral"

    # ── 연속 안부 기록: checkin_status 우선 → DB 추정 ──
    if checkin_status is not None and "streak" in checkin_status:
        streak_info = checkin_status["streak"]
    else:
        try:
            streak_info = _get_streak_info(user_id)
        except Exception:
            streak_info = 0

    # ── 날씨: 인자 우선 → 계절별 mock ──
    if weather_info is None:
        weather_info = get_mock_weather()

    weather_condition = weather_info.get("condition", "정보 없음")
    weather_temp = weather_info.get("temp", "N/A")
    weather_advice = weather_info.get("advice", "오늘도 건강한 하루 보내세요!")

    sentiment_label = {
        "positive": "좋아요", "neutral": "보통", "negative": "안 좋음", None: "미확인"
    }
    today_mood = sentiment_label.get(today_sentiment, "보통")

    greeting = _get_time_greeting()
    simple_text = (
        f"{greeting} {nickname}님!\n\n"
        f"오늘 기분: {today_mood}\n"
        f"오늘 날씨: {weather_condition}, {weather_temp}°C\n"
        f"팁: {weather_advice}\n\n"
        f"어제까지 {streak_info}일 연속 안부 확인 완료"
    )

    quick_replies = [
        {"label": "건강 체크", "action": "message", "messageText": "건강 체크할게요"},
        {"label": "가족에게 전화", "action": "message", "messageText": "가족에게 전화"},
        {"label": "오늘 식단", "action": "message", "messageText": "오늘 식단 알려줘"}
    ]

    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": simple_text}}],
            "quickReplies": quick_replies
        }
    }


def _get_time_greeting() -> str:
    """현재 시각대에 맞는 인사말."""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "좋은 아침이에요,"
    elif 12 <= hour < 17:
        return "좋은 오후예요,"
    elif 17 <= hour < 21:
        return "좋은 저녁이에요,"
    else:
        return "편안한 밤이에요,"


def _get_today_sentiment(stats: Dict[str, Any]) -> Optional[str]:
    """오늘자 통계에서 대표 감정을 추출 (negative > positive > neutral 우선순위)."""
    sentiment_dist = stats.get("sentiment_distribution", {})
    if sentiment_dist.get("negative", 0) > 0:
        return "negative"
    elif sentiment_dist.get("positive", 0) > 0:
        return "positive"
    elif sentiment_dist.get("neutral", 0) > 0:
        return "neutral"
    return None


def _get_streak_info(user_id: str) -> int:
    """최근 7일 응답률로 연속 안부 일수를 추정한다 (간이 지표)."""
    stats = get_checkin_stats(user_id, days=7)
    response_rate = stats.get("response_rate", 0)
    if response_rate >= 100:
        return 7
    elif response_rate >= 85:
        return 6
    elif response_rate >= 70:
        return 5
    elif response_rate >= 55:
        return 4
    elif response_rate >= 40:
        return 3
    elif response_rate >= 25:
        return 2
    elif response_rate > 0:
        return 1
    return 0


def _build_error_widget(message: str) -> Dict[str, Any]:
    """오류 안내용 Widget."""
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": f"오류: {message}"}}],
            "quickReplies": [{"label": "다시 시도", "action": "message", "messageText": "다시 시도"}]
        }
    }


def get_mock_weather() -> Dict[str, Any]:
    """API 키 없이 쓰는 계절별 mock 날씨."""
    month = datetime.now().month
    if month in (3, 4, 5):
        return {"condition": "맑음", "temp": 18, "advice": "산책하기 좋은 날이에요!"}
    elif month in (6, 7, 8):
        return {"condition": "맑음", "temp": 28, "advice": "더운 날씨예요. 물을 자주 마시세요."}
    elif month in (9, 10, 11):
        return {"condition": "맑음", "temp": 20, "advice": "선선한 가을 날씨예요."}
    else:
        return {"condition": "흐림", "temp": 2, "advice": "추운 날씨예요. 따뜻하게 입으세요."}


# ──────────────────────────────────────────────────────────────
# 모듈 직접 실행 테스트
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Widget A (오늘의 돌봄) 테스트")
    print("=" * 60)

    # checkin_status를 직접 주입 → DB 없이도 렌더 확인
    widget = create_daily_care_widget(
        user_id="test_user_001",
        nickname="순자",
        checkin_status={"sentiment": "positive", "streak": 7},
    )
    print(json.dumps(widget, ensure_ascii=False, indent=2))
    assert widget["version"] == "2.0"
    assert widget["template"]["outputs"][0]["simpleText"]["text"]
    assert len(widget["template"]["quickReplies"]) == 3
    print("\n✅ Widget A 렌더링 검증 통과")
