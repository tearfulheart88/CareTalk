#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
돌봄톡 MCP 서버 모듈: emergency_detect.py
==========================================
위험 신호 실시간 감지 및 긴급 레벨 판정 도구.
- detect_emergency: 사용자 메시지에서 위험 키워드 감지 → 위험 레벨 판정
- check_silence_alert: 24시간 무응답 시 YELLOW 경보

Mock 모드: --mock 플래그로 GPT API 호출 없이 키워드 매칭만으로 판정.
Python 3.11+ 호환.
"""

import json
import sqlite3
import re
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

# 프로젝트 루트를 import 경로에 추가 (standalone 실행 + 서버 import 모두 대응)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ============================================================
# 상수 정의
# ============================================================

# RED 레벨 위험 키워드 (즉시 119 + 가족 알림)
# ⚠️ 단독 명사("의식", "응급", "구조", "살려")는 "혼자 살려고", "구조조정",
#    "응급실 다녀왔어" 같은 일상 표현에 오탐이 커서 증상/요청 표현 단위로만 매칭한다.
RED_KEYWORDS = [
    "쓰러졌어", "쓰러졌", "쓰러지", "의식을 잃", "의식이 없",
    "숨이 안 쉬어져", "숨을 못 쉬", "호흡 곤란", "호흡이 멈",
    "심장 마비", "심장이 멈", "심장 발작",
    "피가 많이 나", "피를 많이", "출혈", "과다 출혈",
    "못 일어나", "일어나지 못", "몸을 못 움직", "마비",
    "119", "구급차", "응급실 가야", "응급 상황", "구조해",
    "살려줘", "살려 줘", "살려주", "사람 살려"
]

# YELLOW 레벨 위험 키워드 (가족·복지사 알림)
# "심장"/"호흡"/"피가" 단독 매칭은 일상 대화 오탐이 커서 증상 표현으로 한정.
YELLOW_KEYWORDS = [
    "어지러워", "어지럽", "현기증", "빙빙",
    "가슴이 아파", "가슴 통증", "가슴이 답답", "흉통",
    "다쳤어", "다쳤", "부상", "골절", "넘어졌",
    "피가 나", "피 나", "상처",
    "심장이 아파", "심장이 이상", "가슴이 두근", "심계항진",
    "숨이 차", "숨이 가쁘",
    "고열", "열이 39", "열이 40",
    "감각이 없",
    "극심한 통증", "참을 수 없는", "죽을 것 같"
]

# 컨텍스트 패턴 (오탐 방지용) — 두 그룹으로 나눈다.
# 1) 과거/타인 이야기: RED→YELLOW, YELLOW→NONE 하향 허용
CONTEXT_PAST_THIRD_PATTERNS = [
    r"(?:예전에|옛날에|지난\s*주에|어제\s*TV에서|뉴스에서|드라마에서|영화에서).*",
    r".*(?:봤어|봤다|들은\s*얘기|이야기|소문).*",
    r"(?:남의|다른\s*사람|이웃|친구가|아들이|딸이).*(?:쓰러졌|아팠|다쳤)",
]
# 2) 안심/농담 표현: YELLOW→NONE만 허용. RED는 절대 하향하지 않는다.
#    어르신은 실제 위급 상황에서도 "괜찮아지겠지"라고 말하는 경우가 많다.
#    "괜찮아(?!지)"로 "괜찮아지겠지/괜찮아질" 같은 희망 표현은 안심으로 취급하지 않음.
CONTEXT_REASSURANCE_PATTERNS = [
    r".*(?:농담|장난|거짓말|아니야).*",
    r".*괜찮아(?!지)",
]

# ============================================================
# 데이터베이스 헬퍼
# ============================================================

def _get_db_path(db_path: Optional[str] = None) -> str:
    """SQLite DB 경로 반환. 없으면 기본 경로 사용."""
    if db_path:
        return db_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(base_dir), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "caretalk.db")


def _ensure_tables(db_path: str) -> None:
    """필요한 테이블이 없으면 생성한다 (단일 스키마: db/schema.py)."""
    from db.schema import ensure_schema
    ensure_schema(db_path)


# ============================================================
# 1차 필터: 정규표현식 키워드 매칭
# ============================================================

def _keyword_match(message: str) -> Tuple[List[str], str]:
    """
    사전 정의된 위험 키워드로 1차 정규표현식 매칭을 수행한다.

    Args:
        message: 사용자 메시지 전문

    Returns:
        (detected_keywords, preliminary_level) 튜플
        preliminary_level: "red" | "yellow" | "none"
    """
    msg_lower = message.lower().strip()
    detected = []

    # RED 키워드 검출
    red_found = []
    for kw in RED_KEYWORDS:
        if kw in msg_lower:
            red_found.append(kw)
            detected.append(kw)

    # YELLOW 키워드 검출
    yellow_found = []
    for kw in YELLOW_KEYWORDS:
        if kw in msg_lower:
            yellow_found.append(kw)
            detected.append(kw)

    # 중복 제거
    detected = list(set(detected))

    # 예비 레벨 판정
    if red_found:
        preliminary_level = "red"
    elif yellow_found:
        preliminary_level = "yellow"
    else:
        preliminary_level = "none"

    return detected, preliminary_level


# ============================================================
# 2차 필터: 컨텍스트 확인 (오탐 방지)
# ============================================================

def _check_context_safe(message: str) -> Tuple[bool, bool]:
    """
    메시지의 오탐 가능성 컨텍스트를 확인한다.

    Args:
        message: 사용자 메시지 전문

    Returns:
        (past_third, reassurance) 튜플
        past_third: 과거 경험담/TV/타인 이야기 (RED까지 하향 가능)
        reassurance: 안심/농담 표현 (YELLOW만 하향, RED는 유지)
    """
    past_third = any(re.search(p, message, re.IGNORECASE) for p in CONTEXT_PAST_THIRD_PATTERNS)
    reassurance = any(re.search(p, message, re.IGNORECASE) for p in CONTEXT_REASSURANCE_PATTERNS)
    return past_third, reassurance


def _gpt_context_check(message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    GPT-4o-mini로 컨텍스트 확인 (오탐 방지 2차 필터).

    Args:
        message: 사용자 메시지 전문
        context: 최근 24시간 응답 이력 (선택)

    Returns:
        {
            "is_real_emergency": bool,
            "adjusted_level": "red" | "yellow" | "none",
            "explanation": "..."
        }
    """
    try:
        import openai

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")

        client = openai.OpenAI(api_key=api_key)

        # 컨텍스트 정보 구성
        context_str = ""
        if context:
            recent_messages = context.get("recent_messages", [])
            recent_sentiments = context.get("recent_sentiments", [])
            context_str = f"""
최근 24시간 응답 이력:
- 메시지: {json.dumps(recent_messages, ensure_ascii=False)}
- 감정 상태: {json.dumps(recent_sentiments, ensure_ascii=False)}
"""

        system_prompt = """당신은 독거노인 돌봄 AI 에이전트의 위험 감지 시스템입니다.
사용자의 메시지에 위험 키워드가 감지되었을 때, 이것이 실제 응급 상황인지 판별하세요.

다음 JSON 형식으로 반환하세요:
{
  "is_real_emergency": true 또는 false,
  "adjusted_level": "red" | "yellow" | "none",
  "explanation": "판단 근거를 한 문장으로 설명"
}

판별 기준:
- 실제 응급: 사용자가 현재 겪고 있는 증상이나 사고를 직접 보고하는 경우
- 오탐(False Alarm): 과거 경험담, TV/뉴스 내용, 타인 이야기, 농담, 비유적 표현인 경우
- 최근 응답 이력이 있고 모두 정상이었다면 오탐 가능성이 더 높음
- 사용자가 "아파요", "도와주세요" 등 직접적인 도움 요청을 하면 실제 응급 가능성이 매우 높음

긴급 레벨 조정:
- red: 생명 위협 상황 (의식 소실, 호흡 곤란, 심장 마비, 심각한 출혈, 119 요청)
- yellow: 주의 필요 상황 (어지러움, 가슴 통증, 낙상, 경미한 부상, 고열)
- none: 오탐으로 판단됨
"""

        user_content = f"사용자 메시지: {message}{context_str}"

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"}
        )

        result = json.loads(response.choices[0].message.content)
        return {
            "is_real_emergency": result.get("is_real_emergency", False),
            "adjusted_level": result.get("adjusted_level", "none"),
            "explanation": result.get("explanation", "")
        }

    except ImportError:
        print("[경고] openai 패키지가 설치되지 않았습니다. 컨텍스트 확인을 건너뜁니다.",
              file=sys.stderr)
        return {
            "is_real_emergency": True,
            "adjusted_level": "yellow",
            "explanation": "GPT 분석 불가 — 키워드 매칭 결과를 그대로 사용합니다."
        }
    except Exception as e:
        print(f"[오류] GPT 컨텍스트 확인 실패: {e}. 키워드 매칭 결과를 그대로 사용합니다.",
              file=sys.stderr)
        return {
            "is_real_emergency": True,
            "adjusted_level": "yellow",
            "explanation": f"GPT 분석 오류: {str(e)[:100]}"
        }


# ============================================================
# 핵심 함수 1: detect_emergency
# ============================================================

def detect_emergency(
    user_id: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    mock: bool = False,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    사용자 메시지에서 위험 신호를 실시간 감지하고 긴급 레벨을 판정한다.

    처리 흐름:
    1. 1차 필터: 정규표현식 키워드 매칭
    2. 컨텍스트 안전 패턴 확인 (규칙 기반 오탐 방지)
    3. 2차 필터 (비Mock): GPT-4o-mini 컨텍스트 확인
    4. 최종 위험 레벨 판정 + 알림 대상 결정

    Args:
        user_id: 카카오 채널 사용자 키 (필수)
        message: 사용자 메시지 전문 (필수)
        context: 최근 24시간 응답 이력 (선택)
        mock: True면 GPT API 호출 없이 키워드 매칭만으로 판정 (기본값: False)
        db_path: SQLite DB 경로 (선택)

    Returns:
        {
            "risk_level": "none" | "yellow" | "red",
            "detected_keywords": [...],
            "recommended_action": "...",
            "notify_targets": [...],
            "mock_mode": bool,
            "context_safe": bool,
            "explanation": "..."
        }
    """
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    if not message or not message.strip():
        return {
            "risk_level": "none",
            "detected_keywords": [],
            "recommended_action": "메시지가 비어있습니다.",
            "notify_targets": [],
            "mock_mode": mock,
            "context_safe": True,
            "explanation": "빈 메시지"
        }

    # 1차 필터: 키워드 매칭
    detected_keywords, preliminary_level = _keyword_match(message)

    # 위험 키워드가 전혀 없으면 바로 none 반환
    if preliminary_level == "none":
        return {
            "risk_level": "none",
            "detected_keywords": [],
            "recommended_action": "위험 신호 없음 — 정상 응답으로 처리",
            "notify_targets": [],
            "mock_mode": mock,
            "context_safe": True,
            "explanation": "위험 키워드 미감지"
        }

    # 컨텍스트 패턴 확인 (규칙 기반)
    context_past_third, context_reassurance = _check_context_safe(message)
    context_safe = context_past_third or context_reassurance

    # 최종 레벨 판정
    final_level = preliminary_level
    explanation = ""

    if mock:
        # Mock 모드: 키워드 매칭만으로 판정
        # RED 키워드가 하나라도 있으면 RED
        red_count = sum(1 for kw in detected_keywords if kw in RED_KEYWORDS)
        yellow_count = sum(1 for kw in detected_keywords if kw in YELLOW_KEYWORDS)

        if red_count > 0:
            final_level = "red"
            explanation = f"Mock 모드: RED 키워드 {red_count}개 감지"
        elif yellow_count > 0:
            final_level = "yellow"
            explanation = f"Mock 모드: YELLOW 키워드 {yellow_count}개 감지"
        else:
            final_level = "none"
            explanation = "Mock 모드: 위험 키워드 불명확"

        # 과거/타인 이야기 → 한 단계 하향 (RED→YELLOW, YELLOW→NONE)
        if context_past_third and final_level != "none":
            if final_level == "red":
                final_level = "yellow"
                explanation += " (과거/타인 이야기 컨텍스트 → RED→YELLOW로 하향 조정)"
            elif final_level == "yellow":
                final_level = "none"
                explanation += " (과거/타인 이야기 컨텍스트 → YELLOW→NONE으로 하향 조정)"
        # 안심/농담 표현 → YELLOW만 하향. RED는 유지 (위급 상황에서도
        # "괜찮아"라고 말하는 어르신 특성상 RED 하향은 위험)
        elif context_reassurance and final_level == "yellow":
            final_level = "none"
            explanation += " (안심 표현 컨텍스트 → YELLOW→NONE으로 하향 조정)"
    else:
        # 실제 모드: GPT-4o-mini 컨텍스트 확인
        gpt_result = _gpt_context_check(message, context)
        is_real = gpt_result["is_real_emergency"]
        adjusted = gpt_result["adjusted_level"]
        explanation = gpt_result["explanation"]

        if not is_real:
            # GPT가 오탐으로 판단
            final_level = "none"
            context_safe = True
        else:
            # GPT가 실제 위험으로 판단 → 조정된 레벨 사용
            final_level = adjusted
            context_safe = False

    # 알림 대상 및 권장 조치 결정
    notify_targets = []
    recommended_action = ""

    if final_level == "red":
        notify_targets = ["119", "가족", "복지사"]
        recommended_action = (
            "🚨 RED 경보: 즉시 119 신고 + 가족 긴급 알림톡 발송 + 복지사 연락.\n"
            f"감지 키워드: {', '.join(detected_keywords)}\n"
            "사용자에게 '도움이 오고 있습니다. 침착하게 기다려주세요.' 메시지 전송"
        )
    elif final_level == "yellow":
        notify_targets = ["가족", "복지사"]
        recommended_action = (
            "⚠️ YELLOW 경보: 가족 알림톡 발송 + 복지사 확인 요청.\n"
            f"감지 키워드: {', '.join(detected_keywords)}\n"
            "30분 후 재확인 체크인 예약"
        )
    else:
        notify_targets = []
        recommended_action = (
            f"오탐으로 판단됨: {explanation}\n"
            "정상 응답으로 처리 — daily_checkin 흐름 계속"
        )

    # DB에 위험 감지 기록 저장
    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO emergency_logs
        (user_id, message, risk_level, detected_keywords,
         recommended_action, notify_targets, context_safe, mock_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, message, final_level,
        json.dumps(detected_keywords, ensure_ascii=False),
        recommended_action,
        json.dumps(notify_targets, ensure_ascii=False),
        1 if context_safe else 0,
        1 if mock else 0
    ))
    conn.commit()
    conn.close()

    return {
        "risk_level": final_level,
        "detected_keywords": detected_keywords,
        "recommended_action": recommended_action,
        "notify_targets": notify_targets,
        "mock_mode": mock,
        "context_safe": context_safe,
        "explanation": explanation
    }


# ============================================================
# 핵심 함수 2: check_silence_alert
# ============================================================

def check_silence_alert(
    user_id: str,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    24시간 이상 응답이 없는 사용자에게 YELLOW 경보를 반환한다.

    Args:
        user_id: 카카오 채널 사용자 키 (필수)
        db_path: SQLite DB 경로 (선택)

    Returns:
        {
            "risk_level": "yellow" | "none",
            "hours_silent": int,
            "last_activity": "YYYY-MM-DD HH:MM:SS" | None,
            "recommended_action": "...",
            "notify_targets": [...]
        }
    """
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    conn = sqlite3.connect(db_path, timeout=30)
    cursor = conn.cursor()

    # 마지막 응답 시간 조회 (checkin_responses 테이블)
    cursor.execute("""
        SELECT created_at, message FROM checkin_responses
        WHERE user_id = ?
        ORDER BY id DESC LIMIT 1
    """, (user_id,))
    response_row = cursor.fetchone()

    # 응답 기록이 없으면 checkins 테이블에서 마지막 initiated 시간 조회
    if not response_row:
        cursor.execute("""
            SELECT created_at, checkin_date, checkin_time FROM checkins
            WHERE user_id = ?
            ORDER BY id DESC LIMIT 1
        """, (user_id,))
        checkin_row = cursor.fetchone()

        if not checkin_row:
            conn.close()
            return {
                "risk_level": "none",
                "hours_silent": 0,
                "last_activity": None,
                "recommended_action": "사용자 활동 기록 없음 — 첫 체크인 필요",
                "notify_targets": []
            }

        created_at = checkin_row[0]
    else:
        created_at = response_row[0]

    conn.close()

    # 경과 시간 계산
    try:
        last_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # ISO 형식 시도
        try:
            last_dt = datetime.fromisoformat(created_at)
        except ValueError:
            last_dt = datetime.now()

    now = datetime.now()
    elapsed = now - last_dt
    hours_silent = int(elapsed.total_seconds() / 3600)

    # 24시간 이상 무응답 → YELLOW
    if hours_silent >= 24:
        risk_level = "yellow"
        notify_targets = ["가족", "복지사"]
        recommended_action = (
            f"⚠️ 무응답 경보: {hours_silent}시간 동안 응답 없음.\n"
            f"마지막 활동: {created_at}\n"
            "가족 알림톡 발송 + 복지사 방문 확인 요청"
        )

        # DB에 무응답 경보 기록
        conn = sqlite3.connect(db_path, timeout=30)
        cursor = conn.cursor()
        # 중복 기록 방지: 최근 24시간 내 unresolved alert 확인
        cursor.execute("""
            SELECT id FROM silence_alerts
            WHERE user_id = ? AND resolved = 0
            AND created_at > datetime('now', 'localtime', '-24 hours')
            ORDER BY id DESC LIMIT 1
        """, (user_id,))
        existing = cursor.fetchone()

        if not existing:
            cursor.execute("""
                INSERT INTO silence_alerts
                (user_id, alert_level, last_activity, hours_silent)
                VALUES (?, 'yellow', ?, ?)
            """, (user_id, created_at, hours_silent))
            conn.commit()
        conn.close()
    else:
        risk_level = "none"
        notify_targets = []
        recommended_action = (
            f"정상 범위: 마지막 활동 후 {hours_silent}시간 경과. "
            f"24시간 경과 시 YELLOW 경보 발령 예정."
        )

    return {
        "risk_level": risk_level,
        "hours_silent": hours_silent,
        "last_activity": created_at,
        "recommended_action": recommended_action,
        "notify_targets": notify_targets
    }


# ============================================================
# CLI 진입점 (테스트용)
# ============================================================

if __name__ == "__main__":
    """
    직접 실행 시 간단한 테스트를 수행한다.
    사용법:
        python emergency_detect.py              # 기본 테스트
        python emergency_detect.py --mock       # Mock 모드 테스트
    """
    mock_mode = "--mock" in sys.argv

    print("=" * 60)
    print("돌봄톡 emergency_detect 모듈 테스트")
    print(f"Mock 모드: {mock_mode}")
    print("=" * 60)

    # 테스트 1: RED 위험 감지
    print("\n[테스트 1] detect_emergency(message='쓰러졌어... 숨이 안 쉬어져요')")
    result1 = detect_emergency(
        user_id="test_user_001",
        message="쓰러졌어... 숨이 안 쉬어져요",
        mock=mock_mode
    )
    print(f"  risk_level: {result1['risk_level']}")
    print(f"  detected_keywords: {result1['detected_keywords']}")
    print(f"  notify_targets: {result1['notify_targets']}")
    print(f"  recommended_action: {result1['recommended_action'][:100]}...")
    print(f"  context_safe: {result1['context_safe']}")

    # 테스트 2: YELLOW 위험 감지
    print("\n[테스트 2] detect_emergency(message='어지러워... 가슴이 좀 아파요')")
    result2 = detect_emergency(
        user_id="test_user_001",
        message="어지러워... 가슴이 좀 아파요",
        mock=mock_mode
    )
    print(f"  risk_level: {result2['risk_level']}")
    print(f"  detected_keywords: {result2['detected_keywords']}")
    print(f"  notify_targets: {result2['notify_targets']}")
    print(f"  context_safe: {result2['context_safe']}")

    # 테스트 3: 오탐 방지 (과거 이야기)
    print("\n[테스트 3] detect_emergency(message='예전에 이웃 할아버지가 쓰러졌었대')")
    result3 = detect_emergency(
        user_id="test_user_001",
        message="예전에 이웃 할아버지가 쓰러졌었대",
        mock=mock_mode
    )
    print(f"  risk_level: {result3['risk_level']}")
    print(f"  detected_keywords: {result3['detected_keywords']}")
    print(f"  context_safe: {result3['context_safe']}")
    print(f"  explanation: {result3['explanation']}")

    # 테스트 4: 위험 없음 (정상 메시지)
    print("\n[테스트 4] detect_emergency(message='좋아요! 오늘 산책 다녀왔어요')")
    result4 = detect_emergency(
        user_id="test_user_001",
        message="좋아요! 오늘 산책 다녀왔어요",
        mock=mock_mode
    )
    print(f"  risk_level: {result4['risk_level']}")
    print(f"  detected_keywords: {result4['detected_keywords']}")

    # 테스트 5: 119 직접 요청
    print("\n[테스트 5] detect_emergency(message='119 불러주세요... 가슴이 너무 아파요')")
    result5 = detect_emergency(
        user_id="test_user_001",
        message="119 불러주세요... 가슴이 너무 아파요",
        mock=mock_mode
    )
    print(f"  risk_level: {result5['risk_level']}")
    print(f"  detected_keywords: {result5['detected_keywords']}")
    print(f"  notify_targets: {result5['notify_targets']}")

    # 테스트 6: check_silence_alert
    print("\n[테스트 6] check_silence_alert(user_id='test_user_001')")
    result6 = check_silence_alert(user_id="test_user_001")
    print(f"  risk_level: {result6['risk_level']}")
    print(f"  hours_silent: {result6['hours_silent']}")
    print(f"  last_activity: {result6['last_activity']}")
    print(f"  recommended_action: {result6['recommended_action'][:100]}...")

    # 테스트 7: check_silence_alert (존재하지 않는 사용자)
    print("\n[테스트 7] check_silence_alert(user_id='unknown_user')")
    result7 = check_silence_alert(user_id="unknown_user")
    print(f"  risk_level: {result7['risk_level']}")
    print(f"  hours_silent: {result7['hours_silent']}")

    print("\n" + "=" * 60)
    print("모든 테스트 완료!")
    print("=" * 60)
