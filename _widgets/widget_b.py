# -*- coding: utf-8 -*-
"""
돌봄톡 (CareTalk) - Widget B: 가족용 "주간 돌봄 리포트"
==========================================================
가족 구성원(자녀)이 부모님의 주간 상태를 한눈에 파악할 수 있는 Widget.
BasicCard + ListCard 조합으로 구성된다.

MVP 스펙 (기획서 섹션 6.2):
  - BasicCard: 주간 요약 (응답률, 감정 추이, 주요 키워드)
  - ListCard: 일별 상태 목록 (최근 7일)
  - 버튼: 전화하기, 상세 리포트 보기

Kakao Tools Widget JSON v2.0 스펙 준수.

작성일: 2026-06-22
"""

import sys
import os
import json
import sqlite3
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

# 프로젝트 루트를 import 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.schema import ensure_schema, get_user, get_checkin_stats

WIDGET_NAME = "family_report_widget"
WIDGET_DESCRIPTION = (
    "가족 구성원용 '주간 돌봄 리포트' Widget. "
    "BasicCard로 주간 요약(응답률·감정 추이·건강 키워드)을 표시하고, "
    "ListCard로 일별 상태 목록을 제공합니다."
)

# 감정 이모지/라벨 (family_report.py와 동일)
SENTIMENT_EMOJI = {
    "positive": "😊", "neutral": "😐", "negative": "😔",
    "concern": "⚠️", "no_response": "❓", "": "❓"
}
SENTIMENT_LABEL = {
    "positive": "좋음", "neutral": "보통", "negative": "나쁨",
    "concern": "주의", "no_response": "무응답", "": "미확인"
}


def _get_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(base_dir), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "caretalk.db")


def create_family_report_widget(
    user_id: str,
    nickname: Optional[str] = None,
    days: int = 7,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    가족용 '주간 돌봄 리포트' Widget(카카오 스킬 응답 v2.0)을 생성한다.
    BasicCard(주간 요약) + ListCard(일별 상태) 조합.

    Args:
        user_id: 노인 사용자 ID
        nickname: 노인 닉네임 (None이면 DB 조회)
        days: 리포트 기간 (일, 기본 7)
        db_path: SQLite DB 경로

    Returns:
        카카오 챗봇 스킬 응답 v2.0 JSON (BasicCard + ListCard)
    """
    db_path = _get_db_path(db_path)
    ensure_schema(db_path)

    # 닉네임 조회
    if nickname is None:
        try:
            user = get_user(user_id, db_path=db_path)
            nickname = user.get("nickname", "어르신") if user else "어르신"
        except Exception:
            nickname = "어르신"

    # 주간 통계 조회
    stats = get_checkin_stats(user_id, days=days, db_path=db_path)

    # 일별 체크인 기록 조회
    daily_records = _get_daily_records(user_id, db_path, days)

    # 위험 감지 이력 조회
    emergency_events = _get_emergency_events(user_id, db_path, days)

    # 건강 이상 수치 조회
    health_abnormal_count = _count_health_abnormal(user_id, db_path, days)

    # BasicCard: 주간 요약
    basic_card = _build_summary_card(nickname, stats, emergency_events, days, health_abnormal_count)

    # ListCard: 일별 상태
    list_card = _build_daily_list_card(nickname, daily_records)

    # 버튼
    buttons = [
        {
            "label": "📞 전화하기",
            "action": "phone",
            "phoneNumber": "010-0000-0000"
        },
        {
            "label": "📋 상세 리포트",
            "action": "message",
            "messageText": "상세 리포트 보기"
        }
    ]

    # ListCard에 버튼 추가
    list_card["listCard"]["buttons"] = buttons

    return {
        "version": "2.0",
        "template": {
            "outputs": [basic_card, list_card]
        }
    }


def _build_summary_card(
    nickname: str,
    stats: Dict[str, Any],
    emergency_events: List[Dict[str, Any]],
    days: int,
    health_abnormal_count: int = 0
) -> Dict[str, Any]:
    """
    주간 요약 BasicCard를 생성한다.
    """
    total = stats.get("total_checkins", 0)
    response_rate = stats.get("response_rate", 0)
    sentiment_dist = stats.get("sentiment_distribution", {})
    top_keywords = stats.get("top_keywords", [])
    concern_days = stats.get("concern_days", [])

    positive = sentiment_dist.get("positive", 0)
    neutral = sentiment_dist.get("neutral", 0)
    negative = sentiment_dist.get("negative", 0)

    # 감정 추이 이모지
    trend = ""
    if positive > negative and positive > neutral:
        trend = "😊 긍정적"
    elif negative > positive:
        trend = "😔 부정적"
    else:
        trend = "😐 보통"

    # 제목 (50자 제한)
    period_end = datetime.now().strftime("%Y-%m-%d")
    period_days = max(1, days)
    period_start = (datetime.now() - timedelta(days=period_days - 1)).strftime("%Y-%m-%d")
    title = f"👵 {nickname}님 주간 돌봄 리포트"
    title = title[:50]

    # 설명 (400자 제한)
    desc_lines = [
        f"기간: {period_start} ~ {period_end}",
        f"안부 응답률: {response_rate}% ({total}일 중)",
        f"주간 기분: {trend}",
        f"긍정 {positive}회 / 보통 {neutral}회 / 나쁨 {negative}회",
    ]

    # 건강 키워드
    if top_keywords:
        kw_str = ", ".join(f"{k['keyword']}({k['count']}회)" for k in top_keywords[:3])
        desc_lines.append(f"주요 건강 키워드: {kw_str}")

    # 주의 날
    if concern_days:
        desc_lines.append(f"⚠️ 주의 필요일: {len(concern_days)}일")

    # 위험 감지 이력
    if emergency_events:
        red_count = sum(1 for e in emergency_events if e.get("risk_level") == "red")
        yellow_count = sum(1 for e in emergency_events if e.get("risk_level") == "yellow")
        if red_count:
            desc_lines.append(f"🚨 긴급(RED): {red_count}건")
        if yellow_count:
            desc_lines.append(f"⚠️ 주의(YELLOW): {yellow_count}건")

    # 건강 이상 수치 (health_log 기록)
    if health_abnormal_count:
        desc_lines.append(f"🩺 건강 수치 이상: {health_abnormal_count}건")

    description = "\n".join(desc_lines)[:400]

    return {
        "basicCard": {
            "title": title,
            "description": description,
            "thumbnail": {
                "imageUrl": "https://caretalk.kakao.com/family_report.png",
                "fixedRatio": True
            }
        }
    }


def _build_daily_list_card(
    nickname: str,
    daily_records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    일별 상태 ListCard를 생성한다.
    """
    items = []

    for rec in daily_records[:7]:  # 최대 7일
        date = rec.get("date", "")
        sentiment = rec.get("sentiment", "")
        status = rec.get("status", "")
        message = rec.get("message", "")

        emoji = SENTIMENT_EMOJI.get(sentiment, "❓")
        label = SENTIMENT_LABEL.get(sentiment, "미확인")

        # 날짜 포맷 (MM-DD)
        short_date = date[5:] if len(date) >= 10 else date

        # 상태 표시
        if status == "no_response" or status == "initiated":
            status_text = "❓ 무응답"
        elif status == "concern":
            status_text = f"{emoji} {label} (주의)"
        else:
            status_text = f"{emoji} {label}"

        # 메시지 요약 (30자)
        msg_short = message[:30] + "..." if message and len(message) > 30 else (message or "(응답 없음)")

        items.append({
            "title": f"{short_date} {status_text}",
            "description": msg_short
        })

    if not items:
        items.append({
            "title": "기록 없음",
            "description": "이번 주 체크인 기록이 없습니다."
        })

    return {
        "listCard": {
            "title": f"📋 일별 상태 ({nickname}님)",
            "items": items
        }
    }


def _get_daily_records(
    user_id: str,
    db_path: str,
    days: int
) -> List[Dict[str, Any]]:
    """
    최근 N일간의 일별 체크인 기록을 조회한다.
    """
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT checkin_date, sentiment, status, user_message, response_received
               FROM checkins
               WHERE user_id = ? AND checkin_date >= ? AND checkin_date <= ?
               ORDER BY checkin_date DESC, id DESC""",
            (user_id, start_date, end_date)
        )
        rows = cursor.fetchall()
        conn.close()

        records = []
        for r in rows:
            records.append({
                "date": r[0],
                "sentiment": r[1] or "",
                "status": r[2] or "",
                "message": r[3] or "",
                "responded": r[4] == 1
            })
        return records
    except Exception:
        return []


def _count_health_abnormal(
    user_id: str,
    db_path: str,
    days: int
) -> int:
    """
    최근 N일간 정상 범위를 벗어난 건강 수치 기록 건수를 조회한다.
    """
    try:
        start_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT COUNT(*) FROM health_logs
               WHERE user_id = ? AND normal_range = 0 AND timestamp >= ?""",
            (user_id, start_str)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def _get_emergency_events(
    user_id: str,
    db_path: str,
    days: int
) -> List[Dict[str, Any]]:
    """
    최근 N일간의 위험 감지 이력을 조회한다.
    """
    try:
        start_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
        end_str = datetime.now().strftime("%Y-%m-%d 23:59:59")

        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT risk_level, detected_keywords, created_at
               FROM emergency_logs
               WHERE user_id = ? AND created_at >= ? AND created_at <= ?
                     AND risk_level != 'none'
               ORDER BY created_at DESC""",
            (user_id, start_str, end_str)
        )
        rows = cursor.fetchall()
        conn.close()

        events = []
        for r in rows:
            try:
                keywords = json.loads(r[1]) if r[1] else []
            except (json.JSONDecodeError, TypeError):
                keywords = []
            events.append({
                "risk_level": r[0],
                "detected_keywords": keywords,
                "created_at": r[2]
            })
        return events
    except Exception:
        return []


# ──────────────────────────────────────────────────────────────
# 모듈 직접 실행 테스트
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Widget B (가족용 주간 돌봄 리포트) 테스트")
    print("=" * 60)

    widget = create_family_report_widget(
        user_id="test_family_001",
        nickname="순자",
        days=7
    )
    print(json.dumps(widget, ensure_ascii=False, indent=2))

    # 검증
    assert widget["version"] == "2.0"
    outputs = widget["template"]["outputs"]
    assert len(outputs) == 2  # BasicCard + ListCard
    assert "basicCard" in outputs[0]
    assert "listCard" in outputs[1]
    print("\n✅ Widget B 렌더링 검증 통과")
