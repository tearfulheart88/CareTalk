#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
돌봄톡 MCP 서버 모듈: health_log.py
==========================================
건강 데이터(혈압·혈당·체중) 기록 및 추세 분석 도구.
독거노인이 카카오톡 대화로 건강 수치를 입력하면 자동으로 기록하고,
이상 가능성에는 재측정·상담 및 사람이 공유할 수 있는 참고 결과를 제공한다.

Actions:
  - log: 건강 데이터 기록 (user_id, data_type, value)
  - query: 최근 N일 건강 데이터 조회
  - analyze: 추세 분석 + 이상 패턴 감지

Mock 모드: 규칙 기반 판정 (GPT 호출 없이 동작).
Python 3.10+ 호환.
"""

import json
import math
import sqlite3
import re
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# 프로젝트 루트를 import 경로에 추가
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ============================================================
# 상수 정의: 건강 데이터 정상 범위
# ============================================================

# data_type별 정상 범위 (최소, 최대)
NORMAL_RANGES: Dict[str, Dict[str, Any]] = {
    "systolic": {          # 수축기 혈압
        "label": "수축기 혈압",
        "unit": "mmHg",
        "normal_min": 90,
        "normal_max": 119,
        "warning_min": 80,
        # 140 이상은 고혈압 범주이지만 한 번의 측정만으로 RED 응급경보를 만들지 않는다.
        # 180까지는 재측정·의료진 상담 안내, 180 초과부터 위급 증상 확인을 강화한다.
        "warning_max": 180,
    },
    "diastolic": {         # 이완기 혈압
        "label": "이완기 혈압",
        "unit": "mmHg",
        "normal_min": 60,
        "normal_max": 79,
        "warning_min": 50,
        "warning_max": 120,
    },
    "blood_sugar": {       # 혈당 (공복)
        "label": "혈당",
        "unit": "mg/dL",
        "normal_min": 70,
        "normal_max": 140,
        # 공복/식후/개인 치료목표가 없으므로 넓은 참고구간만 사용한다.
        # <54는 즉시 조치가 필요한 저혈당, 300 이상 지속은 응급평가가 필요할 수 있다.
        "warning_min": 54,
        "warning_max": 299,
    },
    "weight": {            # 체중 (kg) — 범위 판정은 개인 기준치 필요
        "label": "체중",
        "unit": "kg",
        "normal_min": 40,
        "normal_max": 100,
        "warning_min": 30,
        "warning_max": 150,
    },
    "temperature": {       # 체온
        "label": "체온",
        "unit": "°C",
        "normal_min": 35.5,
        "normal_max": 37.5,
        "warning_min": 34.0,
        "warning_max": 39.0,
    },
    "heart_rate": {        # 맥박
        "label": "맥박",
        "unit": "bpm",
        "normal_min": 50,
        "normal_max": 100,
        "warning_min": 40,
        "warning_max": 150,
    },
}

# 저장 전 명백한 오입력만 거르는 물리적 범위입니다. 진단 기준이 아닙니다.
PLAUSIBLE_RANGES: Dict[str, tuple[float, float]] = {
    "systolic": (40, 300),
    "diastolic": (20, 200),
    "blood_sugar": (20, 1000),
    "weight": (10, 400),
    "temperature": (25, 45),
    "heart_rate": (20, 300),
}

# data_type 한국어 별칭 → 표준 키 매핑
DATA_TYPE_ALIASES: Dict[str, str] = {
    "혈압": "blood_pressure",
    "수축기": "systolic",
    "이완기": "diastolic",
    "혈당": "blood_sugar",
    "체중": "weight",
    "몸무게": "weight",
    "체온": "temperature",
    "맥박": "heart_rate",
    "심박": "heart_rate",
}

# 위험 레벨별 메시지
RISK_MESSAGES = {
    "recorded": "수치를 기록했습니다. 개인 기준과 이전 기록의 변화 추세를 함께 확인해 주세요.",
    "normal": "참고 범위 안입니다. 같은 조건에서 꾸준히 기록해 주세요.",
    "warning": "참고 범위를 벗어났습니다. 잠시 안정한 뒤 다시 측정하고 의료진과 상담해 주세요.",
    "danger": (
        "크게 벗어난 수치입니다. 즉시 다시 측정하고 의료기관에 문의해 주세요. "
        "의식 저하, 호흡 곤란, 흉통 등 위급 증상이 함께 있으면 119에 연락하세요."
    ),
}

MEASUREMENT_CONTEXT = {
    "systolic": "혈압은 5분 안정 후 1분 간격으로 두 번 재고, 한 번의 높은 수치만으로 응급상황을 단정하지 않습니다.",
    "diastolic": "혈압은 5분 안정 후 1분 간격으로 두 번 재고, 한 번의 높은 수치만으로 응급상황을 단정하지 않습니다.",
    "blood_sugar": "혈당 목표는 공복·식후·당뇨 치료 여부에 따라 달라집니다. 측정 상황과 개인 목표를 의료진에게 확인하세요.",
    "weight": "체중은 개인의 키·질환·평소 기준과 변화 추세 없이 정상 여부를 판정할 수 없습니다.",
    "temperature": "체온은 측정 부위와 기기에 따라 차이가 날 수 있습니다.",
    "heart_rate": "맥박은 휴식 여부·운동·복용약에 따라 달라질 수 있습니다.",
}

REFERENCE_BASIS = {
    "systolic": {
        "blood_pressure": "https://professional.heart.org/en/science-news/2025-high-blood-pressure-guideline/top-things-to-know",
    },
    "diastolic": {
        "blood_pressure": "https://professional.heart.org/en/science-news/2025-high-blood-pressure-guideline/top-things-to-know",
    },
    "blood_sugar": {
        "blood_glucose": "https://diabetesjournals.org/care/article/49/Supplement_1/S132/163927/6-Glycemic-Goals-Hypoglycemia-and-Hyperglycemic",
    },
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
# 자연어 파싱: 사용자 메시지에서 건강 수치 추출
# ============================================================

def _parse_health_value(message: str, data_type: str) -> Optional[float]:
    """
    사용자 메시지에서 건강 수치를 추출한다.
    "혈압 135요" → 135.0
    "혈당 110이에요" → 110.0
    "체중 62kg" → 62.0

    Args:
        message: 사용자 입력 메시지
        data_type: 표준 data_type 키

    Returns:
        추출된 수치 (float), 없으면 None
    """
    # 메시지에서 숫자 패턴 추출
    # 소수점 포함 숫자 (체온 등)
    numbers = re.findall(r'(\d+\.?\d*)', message)
    if not numbers:
        return None

    # data_type에 따라 적절한 숫자 선택
    if data_type == "blood_pressure":
        # "혈압 135/85", "혈압 135 85", "혈압 135에 85" 형식 → 수축기/이완기
        pair = _parse_blood_pressure_pair(message)
        if pair:
            # 첫 번째 숫자를 수축기로 반환 (호출자가 이완기도 필요시 별도 파싱)
            return pair[0]
        # 단일 숫자 → 수축기로 간주
        return float(numbers[0])
    else:
        # 첫 번째 숫자를 값으로 사용
        return float(numbers[0])


def _parse_blood_pressure_pair(message: str) -> Optional[tuple]:
    """
    "혈압 135/85", "혈압 135 85", "혈압 135에 85" 형식에서
    (수축기, 이완기) 튜플을 추출.

    Returns:
        (systolic, diastolic) 튜플, 없으면 None
    """
    target = message.split("혈압", 1)[1] if "혈압" in message else message
    bp_match = re.search(r'(\d{2,3})\s*(?:/|에|대|,|\s+)\s*(\d{2,3})', target)
    if bp_match:
        return (float(bp_match.group(1)), float(bp_match.group(2)))
    return None


def _parse_named_value(message: str, aliases: tuple[str, ...]) -> Optional[float]:
    """Extract a number placed directly after one of the named health metrics."""
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    match = re.search(
        rf"(?:{alias_pattern})\s*(?:은|는|이|가|을|를)?\s*(?:[:=]\s*)?(\d+(?:\.\d+)?)",
        message,
        re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def _resolve_data_type(user_input: str) -> Optional[str]:
    """
    사용자 입력에서 표준 data_type을 판별한다.
    "혈압", "혈당", "체중", "체온", "맥박" 등의 키워드를 매칭.

    Args:
        user_input: 사용자 메시지 또는 data_type 입력값

    Returns:
        표준 data_type 키 (예: "systolic", "blood_sugar"), 없으면 None
    """
    msg_lower = user_input.lower().strip()

    # 직접 표준 키가 입력된 경우
    if msg_lower in NORMAL_RANGES:
        return msg_lower

    # 한국어 별칭 매칭
    for alias, standard in DATA_TYPE_ALIASES.items():
        if alias in msg_lower:
            return standard

    return None


# ============================================================
# 핵심 함수 1: log_health_data
# ============================================================

def log_health_data(
    user_id: str,
    data_type: str,
    value: float,
    nickname: Optional[str] = None,
    db_path: Optional[str] = None,
    source: str = "manual"
) -> Dict[str, Any]:
    """
    건강 데이터를 DB에 기록하고 정상 범위를 판정한다.

    Args:
        user_id: 사용자 ID
        data_type: 건강 데이터 타입 (systolic, diastolic, blood_sugar, weight, temperature, heart_rate)
        value: 건강 수치
        nickname: 사용자 닉네임 (선택)
        db_path: SQLite DB 경로
        source: 입력 경로 (manual=직접 입력, device=기기 연동, ocr=사진 판독)

    Returns:
        {
            "status": "recorded" | "normal" | "warning" | "danger",
            "data_type": str,
            "label": str,
            "value": float,
            "unit": str,
            "normal_range": str,
            "advice": str,
            "trend_alert": str,
            "log_id": int
        }
    """
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    # data_type 검증
    if data_type not in NORMAL_RANGES:
        return {
            "error": f"알 수 없는 data_type: {data_type}. "
                     f"지원: {', '.join(NORMAL_RANGES.keys())}"
        }

    if isinstance(value, bool):
        return {"error": "value는 숫자여야 합니다."}
    try:
        value = float(value)
    except (TypeError, ValueError):
        return {"error": "value는 숫자여야 합니다."}
    if not math.isfinite(value):
        return {"error": "value는 유한한 숫자여야 합니다."}
    plausible_min, plausible_max = PLAUSIBLE_RANGES[data_type]
    if not plausible_min <= value <= plausible_max:
        return {
            "error": (
                f"{NORMAL_RANGES[data_type]['label']} 값이 입력 가능 범위"
                f"({plausible_min:g}~{plausible_max:g})를 벗어났습니다. 단위와 숫자를 확인해 주세요."
            )
        }

    range_info = NORMAL_RANGES[data_type]
    label = range_info["label"]
    unit = range_info["unit"]
    normal_min = range_info["normal_min"]
    normal_max = range_info["normal_max"]
    warning_min = range_info["warning_min"]
    warning_max = range_info["warning_max"]

    # 체중은 키·평소 기준·질환 정보 없이 절대값만으로 정상/위험을 판정하지 않는다.
    if data_type == "weight":
        status = "recorded"
    elif value < warning_min or value > warning_max:
        status = "danger"
    elif value < normal_min or value > normal_max:
        status = "warning"
    else:
        status = "normal"

    normal_range_str = (
        "개인 기준 및 변화 추세 필요"
        if data_type == "weight"
        else f"{normal_min}~{normal_max} {unit}"
    )
    advice = RISK_MESSAGES.get(status, "")

    # DB에 기록
    conn = sqlite3.connect(db_path, timeout=1.0)
    cursor = conn.cursor()

    # 사용자 자동 등록
    if nickname:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, nickname, user_type) VALUES (?, ?, 'senior')",
            (user_id, nickname)
        )

    # normal_range: 정상 범위 내이면 1, 아니면 0. 체중은 절대값 경보에서 제외한다.
    is_normal = 1 if status in ("normal", "recorded") else 0
    if source not in ("manual", "device", "ocr"):
        source = "manual"
    cursor.execute(
        """INSERT INTO health_logs (user_id, data_type, value, normal_range, source)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, data_type, str(value), is_normal, source)
    )
    log_id = cursor.lastrowid

    # danger 수치는 가족 리포트/Widget B용 로컬 플래그로 남긴다.
    # 외부 메시지 발송이나 보호자 자동 통보는 수행하지 않는다.
    if status == "danger":
        cursor.execute(
            """INSERT INTO alerts (user_id, risk_level, keywords, action_taken)
               VALUES (?, 'red', ?, ?)""",
            (
                user_id,
                json.dumps([f"{label} {value}{unit}"], ensure_ascii=False),
                f"건강 수치 위험: {label} {value}{unit} (정상 {normal_min}~{normal_max}{unit}) — {RISK_MESSAGES['danger']}",
            )
        )
    conn.commit()

    # 추세 분석: 최근 7일간 같은 data_type 기록 조회
    cursor.execute(
        """SELECT value, timestamp FROM health_logs
           WHERE user_id = ? AND data_type = ?
           ORDER BY id DESC LIMIT 7""",
        (user_id, data_type)
    )
    recent_rows = cursor.fetchall()
    conn.close()

    # 추세 알림 생성
    trend_alert = ""
    if len(recent_rows) >= 3:
        recent_values = []
        for r in recent_rows:
            try:
                recent_values.append(float(r[0]))
            except (ValueError, TypeError):
                pass

        if len(recent_values) >= 3:
            latest = recent_values[0]
            avg_prev = sum(recent_values[1:]) / len(recent_values[1:])

            # 급격한 변화 감지
            if data_type in ("systolic", "diastolic", "blood_sugar"):
                change_pct = abs(latest - avg_prev) / avg_prev * 100 if avg_prev > 0 else 0
                if change_pct > 20:
                    trend_alert = f"⚠️ 이전 평균({avg_prev:.0f}) 대비 {change_pct:.0f}% 변화 — 주의 필요"
                elif latest > normal_max:
                    trend_alert = f"📈 최근 추세 상승 — 정상 범위 초과 지속"
                elif latest < normal_min:
                    trend_alert = f"📉 최근 추세 하락 — 정상 범위 미만 지속"

    result = {
        "status": status,
        "data_type": data_type,
        "label": label,
        "value": value,
        "unit": unit,
        "normal_range": normal_range_str,
        "advice": advice,
        "trend_alert": trend_alert,
        "log_id": log_id,
        "source": source,
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "medical_notice": "이 기록과 자동 분류는 진단이 아닙니다. 증상이나 개인별 목표치는 의료진 판단을 우선하세요.",
        "measurement_context": MEASUREMENT_CONTEXT.get(data_type, "개인별 기준과 측정 상황을 함께 확인하세요."),
        "reference_basis": REFERENCE_BASIS.get(data_type, {}),
    }

    # 이상 수치면 보건소 무료 서비스 안내를 함께 제공
    # (수치만 알려주고 끝나지 않고, 어디서 무료로 확인/관리할 수 있는지 연결)
    if status in ("warning", "danger"):
        result["facility_tip"] = (
            "가까운 보건소에서 혈압·혈당을 무료로 측정하고 상담받으실 수 있어요. "
            "'근처 보건소 알려줘'라고 말씀하시면 안내해 드릴게요. (health_facility Tool 연계)"
        )

    return result


# ============================================================
# 핵심 함수 2: query_health_data
# ============================================================

def query_health_data(
    user_id: str,
    data_type: Optional[str] = None,
    days: int = 7,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    최근 N일간의 건강 데이터 기록을 조회한다.

    Args:
        user_id: 사용자 ID
        data_type: 특정 data_type만 조회 (선택, None이면 전체)
        days: 조회 기간 (일, 기본 7)
        db_path: SQLite DB 경로

    Returns:
        {
            "user_id": str,
            "records": [...],
            "summary": {...},
            "days_queried": int
        }
    """
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 7
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    conn = sqlite3.connect(db_path, timeout=1.0)
    cursor = conn.cursor()

    if data_type:
        cursor.execute(
            """SELECT id, data_type, value, normal_range, timestamp
               FROM health_logs
               WHERE user_id = ? AND data_type = ? AND timestamp >= ?
               ORDER BY id DESC""",
            (user_id, data_type, start_date)
        )
    else:
        cursor.execute(
            """SELECT id, data_type, value, normal_range, timestamp
               FROM health_logs
               WHERE user_id = ? AND timestamp >= ?
               ORDER BY id DESC""",
            (user_id, start_date)
        )

    rows = cursor.fetchall()
    conn.close()

    # 레코드 가공
    records = []
    for r in rows:
        records.append({
            "id": r[0],
            "data_type": r[1],
            "label": NORMAL_RANGES.get(r[1], {}).get("label", r[1]),
            "value": r[2],
            "unit": NORMAL_RANGES.get(r[1], {}).get("unit", ""),
            "normal": r[3] == 1,
            "recorded_at": r[4]
        })

    # 요약 통계
    type_counts: Dict[str, int] = {}
    abnormal_count = 0
    for rec in records:
        type_counts[rec["data_type"]] = type_counts.get(rec["data_type"], 0) + 1
        if not rec["normal"]:
            abnormal_count += 1

    return {
        "user_id": user_id,
        "records": records,
        "total_count": len(records),
        "abnormal_count": abnormal_count,
        "type_counts": type_counts,
        "days_queried": days
    }


# ============================================================
# 핵심 함수 3: analyze_health_trend
# ============================================================

def analyze_health_trend(
    user_id: str,
    data_type: Optional[str] = None,
    days: int = 14,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    건강 데이터의 추세를 분석하여 이상 패턴을 감지한다.

    Args:
        user_id: 사용자 ID
        data_type: 분석할 data_type (선택, None이면 전체)
        days: 분석 기간 (일, 기본 14)
        db_path: SQLite DB 경로

    Returns:
        {
            "analysis_date": str,
            "patterns": [...],
            "overall_status": "stable" | "attention_needed" | "concerning",
            "recommendation": str
        }
    """
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 14
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

    conn = sqlite3.connect(db_path, timeout=1.0)
    cursor = conn.cursor()

    # 분석할 data_type 목록
    if data_type:
        types_to_analyze = [data_type]
    else:
        # 기록이 있는 모든 data_type
        cursor.execute(
            "SELECT DISTINCT data_type FROM health_logs WHERE user_id = ? AND timestamp >= ?",
            (user_id, start_date)
        )
        types_to_analyze = [r[0] for r in cursor.fetchall()]

    patterns = []
    overall_severity = "stable"

    for dt in types_to_analyze:
        cursor.execute(
            """SELECT value, timestamp FROM health_logs
               WHERE user_id = ? AND data_type = ? AND timestamp >= ?
               ORDER BY id ASC""",
            (user_id, dt, start_date)
        )
        rows = cursor.fetchall()

        if len(rows) < 2:
            continue

        values = []
        for r in rows:
            try:
                values.append(float(r[0]))
            except (ValueError, TypeError):
                pass

        if len(values) < 2:
            continue

        range_info = NORMAL_RANGES.get(dt, {})
        label = range_info.get("label", dt)
        unit = range_info.get("unit", "")
        n_min = range_info.get("normal_min")
        n_max = range_info.get("normal_max")

        latest = values[-1]
        earliest = values[0]
        avg = sum(values) / len(values)
        change = latest - earliest
        change_pct = (change / earliest * 100) if earliest != 0 else 0

        # 패턴 감지
        pattern_type = "stable"
        severity = "normal"
        description = ""

        if dt == "weight":
            if abs(change_pct) > 5:
                pattern_type = "meaningful_change"
                severity = "attention"
                direction = "증가" if change > 0 else "감소"
                description = (
                    f"{label}: 기록 기간 중 {abs(change_pct):.1f}% {direction} "
                    f"({earliest:.1f}→{latest:.1f}{unit}) — 개인 기준과 측정 조건 확인 필요"
                )
            else:
                description = f"{label}: 절대값 판정 없이 변화 추세만 기록 중 (평균 {avg:.1f}{unit})"

            patterns.append({
                "data_type": dt,
                "label": label,
                "pattern": pattern_type,
                "severity": severity,
                "description": description,
                "latest_value": latest,
                "average": round(avg, 1),
                "change": round(change, 1),
                "change_pct": round(change_pct, 1),
                "record_count": len(values),
            })
            if severity == "attention" and overall_severity != "concerning":
                overall_severity = "attention_needed"
            continue

        # 지속적 이상
        abnormal_count = 0
        if n_min is not None and n_max is not None:
            for v in values:
                if v < n_min or v > n_max:
                    abnormal_count += 1

        if abnormal_count == len(values):
            pattern_type = "persistent_abnormal"
            severity = "concerning"
            description = f"{label}: {len(values)}회 전 기록이 정상 범위 벗어남 ({latest}{unit})"
        elif abnormal_count > len(values) / 2:
            pattern_type = "frequent_abnormal"
            severity = "attention"
            description = f"{label}: {abnormal_count}/{len(values)}회 정상 범위 벗어남"
        elif abs(change_pct) > 20:
            pattern_type = "rapid_change"
            severity = "attention"
            direction = "상승" if change > 0 else "하락"
            description = f"{label}: {abs(change_pct):.0f}% {direction} ({earliest:.0f}→{latest:.0f}{unit})"
        else:
            pattern_type = "stable"
            severity = "normal"
            description = f"{label}: 안정적 추세 (평균 {avg:.0f}{unit})"

        patterns.append({
            "data_type": dt,
            "label": label,
            "pattern": pattern_type,
            "severity": severity,
            "description": description,
            "latest_value": latest,
            "average": round(avg, 1),
            "change": round(change, 1),
            "change_pct": round(change_pct, 1),
            "record_count": len(values)
        })

        # 전체 심각도 업데이트
        if severity == "concerning":
            overall_severity = "concerning"
        elif severity == "attention" and overall_severity != "concerning":
            overall_severity = "attention_needed"

    conn.close()

    # 권장 사항
    if overall_severity == "concerning":
        recommendation = (
            "🚨 지속적인 건강 수치 이상이 감지되었습니다. "
            "기록을 가족·돌봄 담당자와 즉시 공유하고, 의료기관에 문의하세요."
        )
    elif overall_severity == "attention_needed":
        recommendation = (
            "⚠️ 일부 건강 수치에 주의가 필요합니다. "
            "기록을 가족·돌봄 담당자와 공유하고, 정기 검진을 권장합니다."
        )
    else:
        recommendation = "✅ 전반적으로 안정적인 건강 상태입니다. 꾸준한 기록 부탁드려요!"

    return {
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "period_days": days,
        "patterns": patterns,
        "overall_status": overall_severity,
        "recommendation": recommendation
    }


# ============================================================
# 자연어 입력 처리 (log_from_message)
# ============================================================

def log_from_message(
    user_id: str,
    message: str,
    nickname: Optional[str] = None,
    db_path: Optional[str] = None,
    source: str = "manual"
) -> Dict[str, Any]:
    """
    사용자의 자연어 메시지에서 건강 수치를 추출하여 기록한다.
    "오늘 혈압 135/85요" → 수축기 135, 이완기 85 기록
    "혈당 110이에요" → 혈당 110 기록

    Args:
        user_id: 사용자 ID
        message: 사용자 자연어 메시지
        nickname: 닉네임 (선택)
        db_path: DB 경로

    Returns:
        log_health_data 결과 (단일 또는 복수), 또는 파싱 실패 에러
    """
    db_path = _get_db_path(db_path)
    _ensure_tables(db_path)

    parsed: Dict[str, float] = {}
    results: List[Dict[str, Any]] = []

    pair = _parse_blood_pressure_pair(message) if "혈압" in message else None
    if pair:
        parsed["systolic"], parsed["diastolic"] = pair
    else:
        for data_type, aliases in (
            ("systolic", ("수축기",)),
            ("diastolic", ("이완기",)),
        ):
            value = _parse_named_value(message, aliases)
            if value is not None:
                parsed[data_type] = value

        # "혈압 135"처럼 한 값만 있는 기존 입력은 수축기로 유지한다.
        if "혈압" in message and "systolic" not in parsed:
            value = _parse_named_value(message, ("혈압",))
            if value is not None:
                parsed["systolic"] = value

    for data_type, aliases in (
        ("blood_sugar", ("혈당",)),
        ("weight", ("체중", "몸무게")),
        ("temperature", ("체온",)),
        ("heart_rate", ("맥박", "심박")),
    ):
        value = _parse_named_value(message, aliases)
        if value is not None:
            parsed[data_type] = value

    if not parsed:
        resolved_type = _resolve_data_type(message)
        if not resolved_type:
            return {
                "error": "건강 데이터 타입을 인식할 수 없습니다. "
                         "혈압, 혈당, 체중, 체온, 맥박 중 하나를 입력해 주세요."
            }
        fallback_type = "systolic" if resolved_type == "blood_pressure" else resolved_type
        value = _parse_health_value(message, fallback_type)
        if value is None:
            return {
                "error": f"메시지에서 {fallback_type} 수치를 찾을 수 없습니다. "
                         "예: '혈당 110이에요', '체중 62kg'"
            }
        parsed[fallback_type] = value

    for data_type, value in parsed.items():
        results.append(log_health_data(user_id, data_type, value, nickname, db_path, source))

    if list(parsed) == ["systolic", "diastolic"]:
        systolic = parsed["systolic"]
        diastolic = parsed["diastolic"]
        message_text = (
            f"혈압 {int(systolic)}/{int(diastolic)} 기록 완료! "
            f"수축기: {results[0].get('status','?')}, 이완기: {results[1].get('status','?')}"
        )
    else:
        summaries = [
            f"{result.get('label', data_type)} {value:g}{result.get('unit', '')}"
            for (data_type, value), result in zip(parsed.items(), results)
        ]
        message_text = f"건강 수치 {len(results)}건 기록 완료: " + ", ".join(summaries)

    return {"parsed": parsed, "results": results, "message": message_text}


# ============================================================
# CLI 진입점 (테스트용)
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("돌봄톡 health_log 모듈 테스트 (Mock 모드)")
    print("=" * 60)

    # 테스트 1: 혈압 기록 (정상)
    print("\n[테스트 1] log_health_data - 혈압 정상 (120/80)")
    r1 = log_health_data("test_health_001", "systolic", 120, "순자")
    print(f"  status: {r1['status']}, value: {r1['value']}, range: {r1['normal_range']}")
    r1b = log_health_data("test_health_001", "diastolic", 80, "순자")
    print(f"  status: {r1b['status']}, value: {r1b['value']}")

    # 테스트 2: 혈압 기록 (위험)
    print("\n[테스트 2] log_health_data - 혈압 위험 (185/110)")
    r2 = log_health_data("test_health_001", "systolic", 185, "순자")
    print(f"  status: {r2['status']}, advice: {r2['advice']}")

    # 테스트 3: 혈당 기록
    print("\n[테스트 3] log_health_data - 혈당 (95)")
    r3 = log_health_data("test_health_001", "blood_sugar", 95, "순자")
    print(f"  status: {r3['status']}, value: {r3['value']}, unit: {r3['unit']}")

    # 테스트 4: 자연어 파싱
    print("\n[테스트 4] log_from_message - '오늘 혈압 135/85요'")
    r4 = log_from_message("test_health_001", "오늘 혈압 135/85요", "순자")
    print(f"  parsed: {r4.get('parsed')}")
    print(f"  message: {r4.get('message')}")

    # 테스트 5: 자연어 파싱 (혈당)
    print("\n[테스트 5] log_from_message - '혈당 180이에요'")
    r5 = log_from_message("test_health_001", "혈당 180이에요", "순자")
    print(f"  parsed: {r5.get('parsed')}")
    for res in r5.get("results", []):
        print(f"  status: {res.get('status')}, advice: {res.get('advice')}")

    # 테스트 6: 데이터 조회
    print("\n[테스트 6] query_health_data - 최근 7일")
    r6 = query_health_data("test_health_001", days=7)
    print(f"  total: {r6['total_count']}, abnormal: {r6['abnormal_count']}")
    print(f"  type_counts: {r6['type_counts']}")

    # 테스트 7: 추세 분석
    print("\n[테스트 7] analyze_health_trend")
    r7 = analyze_health_trend("test_health_001")
    print(f"  overall_status: {r7['overall_status']}")
    print(f"  recommendation: {r7['recommendation'][:80]}")
    for p in r7.get("patterns", []):
        print(f"  - {p['label']}: {p['pattern']} ({p['severity']}) — {p['description']}")

    print("\n" + "=" * 60)
    print("모든 테스트 완료!")
    print("=" * 60)
