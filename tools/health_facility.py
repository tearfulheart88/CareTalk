#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
돌봄톡 MCP 서버 모듈: health_facility.py
==========================================
어르신 무료 건강 서비스(보건소·치매안심센터 등) 안내 및 알림 도구.
- 지역별 보건소/무료 측정소 검색
- 어르신 대상 무료 건강 프로그램 안내 (독감접종, 국가건강검진 등)
- 최근 건강 기록(health_logs) 기반 맞춤 시설/프로그램 추천
- 정기 알림 메시지 생성 (카카오 스킬 응답 v2.0 JSON)

Actions:
  - search: 지역명으로 시설 검색 (region)
  - programs: 어르신 무료 건강 프로그램 전체 안내
  - recommend: user_id의 최근 이상 수치 기반 맞춤 추천
  - notify: 시기별(독감 시즌 등) 알림 메시지 JSON 생성

데이터: 데모용 내장 샘플. 실서비스에서는 공공데이터포털
       '전국 보건소 표준데이터' API 연동으로 교체한다 (README 로드맵 참고).
Python 3.10+ 호환.
"""

import json
import sqlite3
import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List

# 프로젝트 루트를 import 경로에 추가
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ============================================================
# 데모용 시설 데이터 (실서비스: 공공데이터포털 API 연동)
# ============================================================

FACILITIES: List[Dict[str, Any]] = [
    {"name": "종로구보건소", "type": "보건소", "region": "서울 종로구", "address": "서울 종로구 자하문로19길 36", "phone": "02-2148-3520",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "한방 진료", "치매 조기검진"]},
    {"name": "마포구보건소", "type": "보건소", "region": "서울 마포구", "address": "서울 마포구 월드컵로 212", "phone": "02-3153-9037",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "폐렴구균 무료접종(65세+)", "방문건강관리"]},
    {"name": "강남구보건소", "type": "보건소", "region": "서울 강남구", "address": "서울 강남구 선릉로 668", "phone": "02-3423-5555",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "골밀도 검사", "치매 조기검진"]},
    {"name": "노원구보건소", "type": "보건소", "region": "서울 노원구", "address": "서울 노원구 노해로 437", "phone": "02-2116-3114",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "어르신 운동교실", "방문건강관리"]},
    {"name": "수원시 장안구보건소", "type": "보건소", "region": "경기 수원시", "address": "경기 수원시 장안구 송원로 101", "phone": "031-228-5800",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "고혈압·당뇨 등록관리"]},
    {"name": "성남시 분당구보건소", "type": "보건소", "region": "경기 성남시", "address": "경기 성남시 분당구 양현로 311", "phone": "031-729-3990",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "치매안심센터 연계"]},
    {"name": "부산 해운대구보건소", "type": "보건소", "region": "부산 해운대구", "address": "부산 해운대구 재반로 116", "phone": "051-749-7500",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "경로당 순회 건강교실"]},
    {"name": "대구 수성구보건소", "type": "보건소", "region": "대구 수성구", "address": "대구 수성구 수성로 213", "phone": "053-666-3100",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "한방 진료"]},
    {"name": "인천 남동구보건소", "type": "보건소", "region": "인천 남동구", "address": "인천 남동구 소래로 633", "phone": "032-453-5000",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "방문건강관리"]},
    {"name": "대전 서구보건소", "type": "보건소", "region": "대전 서구", "address": "대전 서구 만년로68번길 15", "phone": "042-288-4500",
     "free_services": ["혈압·혈당 무료 측정", "어르신 독감 무료접종(65세+)", "고혈압·당뇨 등록관리"]},
    {"name": "광주 북구치매안심센터", "type": "치매안심센터", "region": "광주 북구", "address": "광주 북구 우치로 77", "phone": "062-410-8830",
     "free_services": ["치매 조기검진(60세+)", "인지강화 프로그램", "가족 상담"]},
    {"name": "서울 마포구치매안심센터", "type": "치매안심센터", "region": "서울 마포구", "address": "서울 마포구 마포대로 195", "phone": "02-3153-0741",
     "free_services": ["치매 조기검진(60세+)", "기억학교", "가족 카페"]},
]

# 전국 공통 어르신 무료 건강 프로그램
COMMON_PROGRAMS: List[Dict[str, str]] = [
    {"name": "국가건강검진", "target": "만 66세+ (생애전환기 포함, 2년 주기)", "where": "지정 검진기관·보건소",
     "note": "본인부담 없음. 국민건강보험공단 안내문 확인"},
    {"name": "어르신 독감 무료접종", "target": "만 65세 이상", "where": "보건소·지정 의료기관",
     "note": "매년 10~11월. 신분증 지참", "season_months": "10,11"},
    {"name": "폐렴구균 무료접종", "target": "만 65세 이상 (1회)", "where": "보건소",
     "note": "상시. 미접종자 대상"},
    {"name": "혈압·혈당 무료 측정", "target": "누구나", "where": "가까운 보건소",
     "note": "상시. 측정 후 무료 상담 가능"},
    {"name": "치매 조기검진", "target": "만 60세 이상", "where": "치매안심센터",
     "note": "무료 선별검사. 이상 시 정밀검사 연계"},
    {"name": "고혈압·당뇨 등록관리", "target": "고혈압·당뇨 진단자", "where": "보건소",
     "note": "등록 시 정기 검사·교육·문자 알림 무료"},
    {"name": "노인 안검진·개안수술 지원", "target": "만 60세 이상 저소득층", "where": "보건소 신청",
     "note": "백내장 등 수술비 지원"},
]

# data_type → 추천 프로그램/서비스 매핑
RECOMMEND_MAP: Dict[str, Dict[str, str]] = {
    "systolic": {"program": "고혈압·당뇨 등록관리", "service": "혈압·혈당 무료 측정",
                 "reason": "혈압 수치가 정상 범위를 벗어난 기록이 있어요"},
    "diastolic": {"program": "고혈압·당뇨 등록관리", "service": "혈압·혈당 무료 측정",
                  "reason": "혈압 수치가 정상 범위를 벗어난 기록이 있어요"},
    "blood_sugar": {"program": "고혈압·당뇨 등록관리", "service": "혈압·혈당 무료 측정",
                    "reason": "혈당 수치가 정상 범위를 벗어난 기록이 있어요"},
    "temperature": {"program": "국가건강검진", "service": "보건소 진료 상담",
                    "reason": "체온 이상 기록이 있어요"},
    "heart_rate": {"program": "국가건강검진", "service": "혈압·혈당 무료 측정",
                   "reason": "맥박 이상 기록이 있어요"},
    "weight": {"program": "국가건강검진", "service": "보건소 영양 상담",
               "reason": "체중 변화 기록이 있어요"},
}


# ============================================================
# 데이터베이스 헬퍼
# ============================================================

def _get_db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(base_dir), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "caretalk.db")


def _ensure_tables(db_path: str) -> None:
    from db.schema import ensure_schema
    ensure_schema(db_path)


# ============================================================
# 핵심 함수 1: search_facilities
# ============================================================

def search_facilities(
    region: str = "",
    facility_type: str = "",
) -> Dict[str, Any]:
    """
    지역명(시/구 일부)으로 어르신 무료 건강 서비스 시설을 검색한다.

    Args:
        region: 지역명 일부 (예: "마포", "서울", "수원"). 빈 값이면 전체.
        facility_type: "보건소" | "치매안심센터" (선택)

    Returns:
        {"facilities": [...], "count": int, "region": str, "data_note": str}
    """
    results = []
    for f in FACILITIES:
        if region and region.strip() not in f["region"] and region.strip() not in f["name"]:
            continue
        if facility_type and f["type"] != facility_type:
            continue
        results.append(f)

    return {
        "facilities": results,
        "count": len(results),
        "region": region or "전체",
        "data_note": "데모용 샘플 데이터입니다. 실서비스는 공공데이터포털 '전국 보건소 표준데이터' API 연동 예정.",
    }


# ============================================================
# 핵심 함수 2: list_free_programs
# ============================================================

def list_free_programs() -> Dict[str, Any]:
    """
    전국 공통 어르신 무료 건강 프로그램 목록을 반환한다.
    현재 시기(월)에 해당하는 프로그램은 in_season으로 표시.
    """
    month = datetime.now().month
    programs = []
    for p in COMMON_PROGRAMS:
        item = dict(p)
        season = p.get("season_months", "")
        item["in_season"] = str(month) in season.split(",") if season else True
        programs.append(item)

    return {
        "programs": programs,
        "count": len(programs),
        "note": "모두 무료 또는 국가 지원 프로그램입니다. 자세한 일정은 관할 보건소에 확인하세요.",
    }


# ============================================================
# 핵심 함수 3: recommend_for_user
# ============================================================

def recommend_for_user(
    user_id: str,
    region: str = "",
    days: int = 14,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    사용자의 최근 건강 기록(health_logs)에서 이상 수치를 찾아
    맞춤 무료 서비스/프로그램과 주변 시설을 추천한다.

    Args:
        user_id: 사용자 ID (필수)
        region: 지역명 (선택 — 있으면 해당 지역 시설 함께 추천)
        days: 조회 기간 (기본 14일)
        db_path: SQLite DB 경로

    Returns:
        {"recommendations": [...], "facilities": [...], "based_on": [...]}
    """
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    conn = sqlite3.connect(db_path, timeout=1.0)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT DISTINCT data_type FROM health_logs
           WHERE user_id = ? AND normal_range = 0
             AND timestamp >= datetime('now', 'localtime', ? || ' days')""",
        (user_id, f"-{days}")
    )
    abnormal_types = [r[0] for r in cursor.fetchall()]
    conn.close()

    recommendations = []
    seen_programs = set()
    for dt in abnormal_types:
        rec = RECOMMEND_MAP.get(dt)
        if not rec or rec["program"] in seen_programs:
            continue
        seen_programs.add(rec["program"])
        recommendations.append({
            "reason": rec["reason"],
            "recommended_program": rec["program"],
            "recommended_service": rec["service"],
            "cost": "무료",
        })

    # 이상 기록이 없으면 일반 예방 안내
    if not recommendations:
        recommendations.append({
            "reason": "최근 이상 수치는 없어요. 예방 차원의 정기 확인을 추천해요",
            "recommended_program": "국가건강검진",
            "recommended_service": "혈압·혈당 무료 측정",
            "cost": "무료",
        })

    facilities = search_facilities(region)["facilities"] if region else []

    return {
        "recommendations": recommendations,
        "facilities": facilities,
        "based_on": abnormal_types,
        "period_days": days,
        "message": "가까운 보건소에서 모두 무료로 이용하실 수 있어요. 방문 전 전화 확인을 권장해요.",
    }


# ============================================================
# 핵심 함수 4: build_notify_message
# ============================================================

def build_notify_message(
    user_id: str,
    nickname: str = "어르신",
    region: str = "",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    시기·건강 기록 기반 무료 건강 서비스 알림 메시지를 생성한다.
    카카오 챗봇 스킬 응답 v2.0 JSON(simpleText + quickReplies) 포함.

    Args:
        user_id: 사용자 ID (필수)
        nickname: 닉네임
        region: 지역명 (선택)
        db_path: SQLite DB 경로

    Returns:
        {"message_json": {...}, "text": str, "highlight": str}
    """
    db_path = _get_db_path(db_path)
    rec = recommend_for_user(user_id, region=region, db_path=db_path)

    month = datetime.now().month
    lines = [f"{nickname}님, 무료 건강 서비스 알림이에요 🏥"]

    # 시즌 안내 (독감 접종 기간)
    highlight = ""
    if month in (10, 11):
        highlight = "지금은 어르신 독감 무료접종 기간이에요 (65세 이상, 보건소·지정 의료기관)"
        lines.append(f"💉 {highlight}")

    top = rec["recommendations"][0]
    lines.append(f"• {top['reason']}")
    lines.append(f"→ '{top['recommended_program']}' / '{top['recommended_service']}' 모두 무료예요.")

    if rec["facilities"]:
        f = rec["facilities"][0]
        lines.append(f"📍 가까운 곳: {f['name']} ({f['address']}, ☎ {f['phone']})")
    lines.append("방문 전에 전화로 확인해 보세요. 제가 가족에게도 알려드릴까요?")

    text = "\n".join(lines)
    message_json = {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": [
                {"label": "근처 보건소 더 보기", "action": "message", "messageText": "근처 보건소 알려줘"},
                {"label": "가족에게 알리기", "action": "message", "messageText": "가족에게 알려줘"},
                {"label": "괜찮아요", "action": "message", "messageText": "괜찮아요"},
            ],
        },
    }

    return {
        "message_json": message_json,
        "text": text,
        "highlight": highlight,
        "recommendations": rec["recommendations"],
    }


# ============================================================
# CLI 진입점 (테스트용)
# ============================================================

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    print("=" * 60)
    print("돌봄톡 health_facility 모듈 테스트")
    print("=" * 60)

    print("\n[테스트 1] search_facilities(region='마포')")
    r1 = search_facilities("마포")
    print(f"  count: {r1['count']}")
    for f in r1["facilities"]:
        print(f"  - {f['name']} ({f['type']}) ☎ {f['phone']}")

    print("\n[테스트 2] list_free_programs()")
    r2 = list_free_programs()
    print(f"  count: {r2['count']}")
    for p in r2["programs"][:4]:
        print(f"  - {p['name']} — {p['target']}")

    print("\n[테스트 3] recommend_for_user (혈압 이상 기록 후)")
    from tools.health_log import log_health_data
    import tempfile
    tmp_db = os.path.join(tempfile.gettempdir(), "caretalk_facility_test.db")
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    log_health_data("test_fac_001", "systolic", 152, "순자", db_path=tmp_db)
    r3 = recommend_for_user("test_fac_001", region="마포", db_path=tmp_db)
    print(f"  based_on: {r3['based_on']}")
    for rec in r3["recommendations"]:
        print(f"  - {rec['reason']} → {rec['recommended_program']} ({rec['cost']})")

    print("\n[테스트 4] build_notify_message")
    r4 = build_notify_message("test_fac_001", "순자", region="마포", db_path=tmp_db)
    print("  " + r4["text"].replace("\n", "\n  "))

    print("\n" + "=" * 60)
    print("모든 테스트 완료!")
    print("=" * 60)
