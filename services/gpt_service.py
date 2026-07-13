"""
돌봄톡 (CareTalk) - GPT 서비스 모듈
===================================
OpenAI GPT-4o-mini API를 활용한 감정 분석, 건강 키워드 추출, 위험 컨텍스트 확인.
Mock 모드 지원: --mock 플래그 활성화 시 실제 API 호출 없이 규칙 기반 응답 반환.

주요 기능:
  - analyze_sentiment(message): 감정 분류 (positive / neutral / negative)
  - extract_health_keywords(message): 건강 관련 키워드 추출
  - confirm_emergency(message, keywords): 위험 컨텍스트 확인 (True/False)
  - generate_checkin_message(nickname): 안부 확인 첫 메시지 생성
  - generate_report_summary(stats, nickname): 주간 리포트 요약 생성

Mock 모드:
  - 규칙 기반 키워드 매칭으로 GPT 호출 없이 동작
  - 테스트 및 개발 단계에서 API 비용 절감

작성일: 2026-06-21
"""

import os
import json
import re
from typing import List, Dict, Any, Optional

from services.usage_guard import (
    live_api_enabled,
    max_output_tokens,
    openai_timeout,
    release_openai_call,
    reserve_openai_call,
)

# OpenAI 클라이언트 (실제 API 호출용)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None  # type: ignore


# ═══════════════════════════════════════════════════════════════════
# Mock 모드 설정
# ═══════════════════════════════════════════════════════════════════
# 환경 변수 MOCK_MODE 또는 생성자 인자로 제어
_MOCK_MODE = os.environ.get("MOCK_MODE", "true").lower() == "true"


def set_mock_mode(enabled: bool) -> None:
    """Mock 모드를 전역적으로 설정/해제합니다."""
    global _MOCK_MODE
    _MOCK_MODE = enabled
    os.environ["MOCK_MODE"] = "true" if enabled else "false"
    if enabled:
        os.environ["LIVE_API_ENABLED"] = "false"


def is_mock_mode() -> bool:
    """현재 Mock 모드 상태를 반환합니다."""
    return _MOCK_MODE


# ═══════════════════════════════════════════════════════════════════
# GPT 서비스 클래스
# ═══════════════════════════════════════════════════════════════════

class GPTService:
    """
    GPT-4o-mini API 래퍼 클래스.
    Mock 모드에서는 규칙 기반 응답을 반환하여 API 비용 없이 테스트 가능.
    """

    # ── 시스템 프롬프트 (Prompt Caching 최적화) ──────────────────
    # 반복 사용되는 시스템 메시지를 상수로 정의하여
    # OpenAI Prompt Caching 혜택을 최대화합니다.

    SENTIMENT_SYSTEM_PROMPT = (
        "당신은 독거노인 돌봄 AI 에이전트 '돌봄톡'입니다. "
        "사용자의 메시지를 분석하여 감정 상태를 분류하세요.\n\n"
        "분류 기준:\n"
        "- positive: 긍정적 표현 (좋아요, 기뻐요, 행복해요, 고마워요, 괜찮아요 등)\n"
        "- neutral: 중립적 표현 (그저 그래요, 보통이에요, 특별한 일 없어요 등)\n"
        "- negative: 부정적 표현 (아파요, 슬퍼요, 외로워요, 힘들어요, 우울해요 등)\n\n"
        "응답 형식: JSON {\"sentiment\": \"positive|neutral|negative\", \"confidence\": 0.0~1.0, \"reason\": \"분석 근거 한 줄\"}\n"
        "반드시 JSON만 출력하고 다른 텍스트는 포함하지 마세요."
    )

    HEALTH_KEYWORDS_SYSTEM_PROMPT = (
        "당신은 독거노인 돌봄 AI 에이전트 '돌봄톡'입니다. "
        "사용자의 메시지에서 건강 관련 키워드를 추출하세요.\n\n"
        "추출 대상:\n"
        "- 신체 부위: 무릎, 허리, 어깨, 머리, 가슴, 배, 다리, 팔, 손, 발, 치아, 눈, 귀\n"
        "- 증상: 아픔, 통증, 어지러움, 메스꺼움, 피로, 불면, 식욕부진, 기침, 가래, 열\n"
        "- 질병: 고혈압, 당뇨, 관절염, 치매, 우울증, 불안증\n"
        "- 약물: 혈압약, 당뇨약, 진통제, 소화제\n\n"
        "응답 형식: JSON {\"keywords\": [\"키워드1\", \"키워드2\", ...], \"has_health_concern\": true|false}\n"
        "건강 관련 내용이 없으면 빈 배열을 반환하세요. 반드시 JSON만 출력하세요."
    )

    EMERGENCY_SYSTEM_PROMPT = (
        "당신은 독거노인 돌봄 AI 에이전트 '돌봄톡'입니다. "
        "사용자의 메시지와 감지된 위험 키워드를 바탕으로 실제 응급 상황인지 판단하세요.\n\n"
        "판단 기준:\n"
        "- RED (즉시 대응 필요): 의식 소실, 호흡 곤란, 심한 흉통, 낙상으로 인한 부상, 출혈, 뇌졸중 의심 증상\n"
        "- YELLOW (주의 필요): 어지러움, 가벼운 통증, 불면, 식욕부진, 24시간 이상 무응답\n"
        "- NONE: 단순 증상 언급, 과거 이야기, 비응급 상황\n\n"
        "응답 형식: JSON {\"is_emergency\": true|false, \"risk_level\": \"red|yellow|none\", \"reason\": \"판단 근거 한 줄\", \"recommended_action\": \"권장 조치\"}\n"
        "반드시 JSON만 출력하세요."
    )

    REPORT_SYSTEM_PROMPT = (
        "당신은 독거노인 돌봄 AI 에이전트 '돌봄톡'입니다. "
        "노인 사용자의 주간 안부 데이터를 바탕으로 가족용 따뜻한 리포트 요약을 생성하세요.\n\n"
        "요약 기준:\n"
        "- 전체적인 상태를 한 문장으로 요약 (긍정적/주의 필요)\n"
        "- 특이사항이 있으면 부드럽게 언급\n"
        "- 가족이 안심할 수 있는 따뜻한 말투 사용\n"
        "- 100자 이내로 작성\n\n"
        "응답 형식: JSON {\"summary\": \"요약 문자열\", \"mood_emoji\": \"😊|😐|😔\", \"alert\": true|false}\n"
        "반드시 JSON만 출력하세요."
    )

    def __init__(self, api_key: Optional[str] = None, mock_mode: Optional[bool] = None):
        """
        GPTService 초기화.

        Args:
            api_key: OpenAI API 키. 미지정 시 환경 변수 OPENAI_API_KEY 사용.
            mock_mode: Mock 모드 강제 설정. None이면 전역 _MOCK_MODE 따름.
        """
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._mock_mode = mock_mode if mock_mode is not None else _MOCK_MODE

        # 실제 API 클라이언트 초기화 (Mock 모드가 아닐 때만)
        if not self._mock_mode and live_api_enabled() and OPENAI_AVAILABLE and self._api_key:
            self._client = OpenAI(
                api_key=self._api_key,
                timeout=openai_timeout(),
                max_retries=0,
            )
        else:
            self._client = None

    # ── GPT 호출 헬퍼 ────────────────────────────────────────────

    def _call_gpt(self, system_prompt: str, user_message: str, max_tokens: int = 150) -> Dict[str, Any]:
        """
        GPT-4o-mini API를 호출하여 JSON 응답을 파싱합니다.

        Args:
            system_prompt: 시스템 프롬프트 (Prompt Caching 대상)
            user_message: 사용자 메시지
            max_tokens: 최대 응답 토큰 수

        Returns:
            파싱된 JSON 응답 딕셔너리
        """
        if self._mock_mode or self._client is None:
            raise RuntimeError("GPT API를 호출할 수 없습니다. Mock 모드를 활성화하거나 API 키를 설정하세요.")

        denied = reserve_openai_call()
        if denied:
            raise RuntimeError("OpenAI usage guard denied the request")
        try:
            response = self._client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=max_output_tokens(max_tokens),
                temperature=0.3,
                response_format={"type": "json_object"}
            )
        finally:
            release_openai_call()

        content = response.choices[0].message.content
        if content is None:
            raise ValueError("GPT 응답이 비어 있습니다.")

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # JSON 파싱 실패 시 텍스트에서 JSON 추출 시도
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            raise ValueError(f"GPT 응답을 JSON으로 파싱할 수 없습니다: {content[:200]}")

    # ── Mock 응답 생성기 ─────────────────────────────────────────

    def _mock_analyze_sentiment(self, message: str) -> Dict[str, Any]:
        """
        규칙 기반 감정 분석 (Mock 모드).
        한국어 감정 키워드 사전을 기반으로 분류합니다.
        """
        message_lower = message.lower()

        # 부정어+긍정 표현("안 좋아" 등)을 부정으로 선처리하고,
        # 긍정 패턴에 걸리지 않게 메시지에서 제거한다.
        negated_positive_patterns = [
            r'안\s*좋', r'좋지\s*(?:가\s*)?않', r'안\s*괜찮', r'괜찮지\s*않',
            r'못\s*지내', r'잘\s*못\s*잤', r'맛이?\s*없', r'재미\s*없',
        ]
        negated_positive_score = 0
        for pat in negated_positive_patterns:
            if re.search(pat, message_lower):
                negated_positive_score += 1
                message_lower = re.sub(pat, ' ', message_lower)

        # 부정적 키워드
        negative_patterns = [
            r'아파', r'아픔', r'통증', r'슬퍼', r'슬픔', r'외로워', r'외로움',
            r'힘들어', r'힘듦', r'우울', r'불안', r'무서워', r'걱정', r'괴로워',
            r'죽고\s*싶', r'못\s*살겠', r'너무\s*힘들', r'기운이\s*없',
            r'잠이\s*안\s*와', r'밥맛이\s*없', r'입맛이\s*없'
        ]
        # 긍정적 키워드
        positive_patterns = [
            r'좋아', r'좋음', r'기뻐', r'기쁨', r'행복', r'고마워', r'감사',
            r'괜찮아', r'잘\s*있어', r'재미', r'즐거워', r'신나', r'편안',
            r'맛있', r'잘\s*잤', r'푹\s*잤', r'산책', r'운동', r'건강'
        ]

        negative_score = negated_positive_score + sum(1 for pat in negative_patterns if re.search(pat, message_lower))
        positive_score = sum(1 for pat in positive_patterns if re.search(pat, message_lower))

        if negative_score > positive_score:
            sentiment = "negative"
            confidence = min(0.5 + negative_score * 0.15, 0.95)
            reason = f"부정적 표현 {negative_score}개 감지"
        elif positive_score > negative_score:
            sentiment = "positive"
            confidence = min(0.5 + positive_score * 0.15, 0.95)
            reason = f"긍정적 표현 {positive_score}개 감지"
        else:
            sentiment = "neutral"
            confidence = 0.6
            reason = "특별한 감정 표현 없음"

        return {"sentiment": sentiment, "confidence": round(confidence, 2), "reason": reason}

    def _mock_extract_health_keywords(self, message: str) -> Dict[str, Any]:
        """
        규칙 기반 건강 키워드 추출 (Mock 모드).
        사전 정의된 건강 관련 키워드 사전을 기반으로 매칭합니다.
        """
        # 건강 키워드 사전 (한국어)
        health_dict = {
            # 신체 부위
            "무릎": ["무릎"], "허리": ["허리"], "어깨": ["어깨"], "머리": ["머리", "두통"],
            "가슴": ["가슴"], "배": ["배", "복부"], "다리": ["다리"], "팔": ["팔"],
            "손": ["손"], "발": ["발"], "치아": ["치아", "이빨", "잇몸"],
            "눈": ["눈", "시력"], "귀": ["귀", "청력"],
            # 증상
            "통증": ["아파", "아픔", "통증", "쑤셔", "쑤심", "저려", "저림"],
            "어지러움": ["어지러워", "어지럼", "현기증", "빙글빙글"],
            "메스꺼움": ["메스꺼워", "속이\s*울렁", "토할\s*것\s*같"],
            "피로": ["피곤", "피로", "기운이\s*없", "지쳐"],
            "불면": ["잠이\s*안\s*와", "불면", "잠\s*못\s*자"],
            "식욕부진": ["밥맛이\s*없", "입맛이\s*없", "먹기\s*싫"],
            "기침": ["기침", "콜록"],
            "열": ["열이\s*나", "몸살"],
            # 질병
            "고혈압": ["고혈압", "혈압이\s*높"],
            "당뇨": ["당뇨", "혈당이\s*높", "혈당"],
            "관절염": ["관절염", "관절"],
            "치매": ["치매", "깜빡", "기억이\s*안\s*나"],
            "우울증": ["우울", "우울증"],
            # 약물
            "혈압약": ["혈압약"],
            "당뇨약": ["당뇨약"],
            "진통제": ["진통제", "소염제"],
        }

        found_keywords = []
        message_lower = message.lower()

        for keyword, patterns in health_dict.items():
            for pattern in patterns:
                if re.search(pattern, message_lower):
                    found_keywords.append(keyword)
                    break  # 키워드당 한 번만 추가

        has_concern = len(found_keywords) > 0
        return {"keywords": found_keywords, "has_health_concern": has_concern}

    def _mock_confirm_emergency(self, message: str, keywords: List[str]) -> Dict[str, Any]:
        """
        규칙 기반 위험 컨텍스트 확인 (Mock 모드).
        위험 키워드의 심각도와 조합을 기반으로 판단합니다.
        """
        message_lower = message.lower()

        # RED 레벨 키워드 (즉시 대응)
        red_patterns = [
            r'쓰러졌', r'의식이\s*없', r'숨이\s*안\s*쉬', r'호흡', r'질식',
            r'가슴이\s*아파', r'가슴이\s*쥐어짜', r'심장', r'피가\s*나',
            r'출혈', r'다쳤어', r'골절', r'119', r'구급차', r'말이\s*안\s*나와',
            r'몸이\s*안\s*움직', r'마비', r'경련'
        ]
        # YELLOW 레벨 키워드 (주의)
        yellow_patterns = [
            r'어지러워', r'어지럼', r'현기증', r'넘어졌', r'미끄러',
            r'계속\s*아파', r'심하게\s*아파', r'열이\s*나', r'고열',
            r'밥을\s*못\s*먹', r'물도\s*못\s*마시', r'탈수',
            r'잠을\s*못\s*자', r'불면', r'우울', r'외로워'
        ]

        red_score = sum(1 for pat in red_patterns if re.search(pat, message_lower))
        yellow_score = sum(1 for pat in yellow_patterns if re.search(pat, message_lower))

        if red_score > 0:
            risk_level = "red"
            is_emergency = True
            reason = f"응급 상황 키워드 {red_score}개 감지"
            recommended_action = "즉시 119 신고 및 가족/복지사 긴급 알림 발송"
        elif yellow_score > 0:
            risk_level = "yellow"
            is_emergency = True
            reason = f"주의 필요 키워드 {yellow_score}개 감지"
            recommended_action = "가족 및 복지사에게 알림 발송, 1시간 내 재확인"
        else:
            risk_level = "none"
            is_emergency = False
            reason = "응급 상황으로 판단되지 않음"
            recommended_action = "정기 안부 확인 유지"

        return {
            "is_emergency": is_emergency,
            "risk_level": risk_level,
            "reason": reason,
            "recommended_action": recommended_action
        }

    # ── 공개 API 메서드 ──────────────────────────────────────────

    def analyze_sentiment(self, message: str) -> Dict[str, Any]:
        """
        사용자 메시지의 감정을 분석합니다.

        Args:
            message: 사용자 응답 메시지

        Returns:
            {
                "sentiment": "positive" | "neutral" | "negative",
                "confidence": 0.0 ~ 1.0,
                "reason": "분석 근거"
            }
        """
        if self._mock_mode or self._client is None:
            return self._mock_analyze_sentiment(message)

        try:
            return self._call_gpt(self.SENTIMENT_SYSTEM_PROMPT, message, max_tokens=100)
        except Exception:
            return self._mock_analyze_sentiment(message)

    def extract_health_keywords(self, message: str) -> Dict[str, Any]:
        """
        사용자 메시지에서 건강 관련 키워드를 추출합니다.

        Args:
            message: 사용자 응답 메시지

        Returns:
            {
                "keywords": ["키워드1", "키워드2", ...],
                "has_health_concern": true | false
            }
        """
        if self._mock_mode or self._client is None:
            return self._mock_extract_health_keywords(message)

        try:
            return self._call_gpt(self.HEALTH_KEYWORDS_SYSTEM_PROMPT, message, max_tokens=100)
        except Exception:
            return self._mock_extract_health_keywords(message)

    def confirm_emergency(self, message: str, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        위험 키워드가 감지된 메시지의 응급 상황 여부를 확인합니다.

        Args:
            message: 사용자 메시지
            keywords: 1차 필터에서 감지된 위험 키워드 (선택)

        Returns:
            {
                "is_emergency": true | false,
                "risk_level": "red" | "yellow" | "none",
                "reason": "판단 근거",
                "recommended_action": "권장 조치"
            }
        """
        if self._mock_mode or self._client is None:
            return self._mock_confirm_emergency(message, keywords or [])

        # GPT 호출용 메시지 구성
        user_content = f"사용자 메시지: {message}\n감지된 위험 키워드: {keywords or '없음'}"
        try:
            return self._call_gpt(self.EMERGENCY_SYSTEM_PROMPT, user_content, max_tokens=150)
        except Exception:
            return self._mock_confirm_emergency(message, keywords or [])

    def generate_checkin_message(self, nickname: str) -> str:
        """
        안부 확인 첫 메시지를 생성합니다. (Mock 모드에서도 동일하게 동작)

        Args:
            nickname: 사용자 닉네임

        Returns:
            안부 확인 메시지 문자열
        """
        # 안부 메시지는 GPT 호출 없이 템플릿 기반으로 생성 (비용 절감)
        return f"좋은 아침이에요, {nickname}님! 🌞\n오늘 기분은 어떠세요? 😊"

    def generate_report_summary(self, stats: Dict[str, Any], nickname: str) -> Dict[str, Any]:
        """
        주간 리포트 요약을 생성합니다.

        Args:
            stats: get_checkin_stats()의 결과 딕셔너리
            nickname: 사용자 닉네임

        Returns:
            {
                "summary": "요약 문자열",
                "mood_emoji": "😊" | "😐" | "😔",
                "alert": true | false
            }
        """
        if self._mock_mode or self._client is None:
            # Mock 모드: 통계 기반 규칙 요약
            response_rate = stats.get("response_rate", 0)
            sentiment_dist = stats.get("sentiment_distribution", {})
            negative_count = sentiment_dist.get("negative", 0)
            concern_count = len(stats.get("concern_days", []))

            if concern_count > 0 or negative_count >= 3:
                mood_emoji = "😔"
                alert = True
                summary = f"{nickname}님, 이번 주 {concern_count}일 주의 신호가 있었습니다. 가족의 관심이 필요해 보입니다."
            elif negative_count >= 1:
                mood_emoji = "😐"
                alert = False
                summary = f"{nickname}님, 대체로 안정적인 한 주였습니다. 가끔 기분이 좋지 않은 날이 있었지만 전반적으로 괜찮습니다."
            else:
                mood_emoji = "😊"
                alert = False
                summary = f"{nickname}님, 이번 주도 건강하고 활기찬 한 주였습니다! 응답률 {response_rate}%로 잘 지내고 계십니다."

            return {"summary": summary, "mood_emoji": mood_emoji, "alert": alert}

        # 실제 GPT 호출
        stats_json = json.dumps(stats, ensure_ascii=False)
        user_content = f"사용자: {nickname}님\n주간 통계: {stats_json}"
        try:
            return self._call_gpt(self.REPORT_SYSTEM_PROMPT, user_content, max_tokens=150)
        except Exception:
            return GPTService(api_key="", mock_mode=True).generate_report_summary(stats, nickname)


# ═══════════════════════════════════════════════════════════════════
# 모듈 레벨 편의 함수 (전역 Mock 모드 사용)
# ═══════════════════════════════════════════════════════════════════

# 기본 GPTService 인스턴스 (지연 초기화)
_default_service: Optional[GPTService] = None


def _get_service() -> GPTService:
    """기본 GPTService 인스턴스를 반환합니다."""
    global _default_service
    if _default_service is None:
        _default_service = GPTService()
    return _default_service


def analyze_sentiment(message: str) -> Dict[str, Any]:
    """모듈 레벨 감정 분석 함수."""
    return _get_service().analyze_sentiment(message)


def extract_health_keywords(message: str) -> Dict[str, Any]:
    """모듈 레벨 건강 키워드 추출 함수."""
    return _get_service().extract_health_keywords(message)


def confirm_emergency(message: str, keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """모듈 레벨 위험 확인 함수."""
    return _get_service().confirm_emergency(message, keywords)


# ═══════════════════════════════════════════════════════════════════
# 직접 실행 테스트
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Mock 모드로 테스트
    set_mock_mode(True)
    service = GPTService(mock_mode=True)

    print("=" * 60)
    print("🧪 GPT 서비스 Mock 모드 테스트")
    print("=" * 60)

    # 감정 분석 테스트
    test_messages = [
        "오늘 기분이 좋아요! 산책도 하고 맛있는 것도 먹었어요.",
        "그저 그래요. 특별한 일은 없었네요.",
        "무릎이 너무 아파서 잠을 못 잤어요. 우울해요.",
        "어지러워서 쓰러질 것 같아요. 도와주세요!"
    ]

    for msg in test_messages:
        print(f"\n📝 메시지: \"{msg}\"")
        sentiment = service.analyze_sentiment(msg)
        print(f"   감정: {sentiment['sentiment']} (신뢰도: {sentiment['confidence']})")
        print(f"   근거: {sentiment['reason']}")

        keywords = service.extract_health_keywords(msg)
        print(f"   건강 키워드: {keywords['keywords']}")

        emergency = service.confirm_emergency(msg, keywords['keywords'])
        print(f"   위험 레벨: {emergency['risk_level']} (응급: {emergency['is_emergency']})")
        print(f"   권장 조치: {emergency['recommended_action']}")

    # 리포트 요약 테스트
    print(f"\n📊 리포트 요약 테스트:")
    mock_stats = {
        "total_checkins": 7,
        "response_rate": 85.7,
        "sentiment_distribution": {"positive": 4, "neutral": 2, "negative": 1},
        "top_keywords": [{"keyword": "무릎", "count": 2}],
        "concern_days": []
    }
    summary = service.generate_report_summary(mock_stats, "순자님")
    print(f"   요약: {summary['summary']}")
    print(f"   이모지: {summary['mood_emoji']}")
    print(f"   알림: {summary['alert']}")

    print("\n✅ Mock 모드 테스트 완료")
