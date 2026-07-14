"""Senior-friendly first-run guide and FAQ for CareTalk."""

from __future__ import annotations

from typing import Any, Literal


GuideAction = Literal["start", "examples", "faq", "accessibility", "privacy"]
GuideAudience = Literal["senior", "family", "helper"]


_FAQ: list[dict[str, Any]] = [
    {
        "question": "돌봄톡이 자동으로 119에 신고하나요?",
        "answer": "아니요. 돌봄톡은 위험 신호를 알려주고 119 연락을 권하지만 전화·신고·출동을 자동으로 완료하지 않습니다. 위급하면 본인이나 곁에 있는 사람이 즉시 119에 연락해야 합니다.",
        "keywords": ("119", "신고", "출동", "응급", "자동"),
        "next": "응급 상황에서 어떻게 해야 해?",
    },
    {
        "question": "글을 길게 쓰지 않아도 되나요?",
        "answer": "네. 안부·식사·약·통증·도움 요청을 미리 만든 버튼으로 답할 수 있습니다. 말하기 어려우면 '도움이 필요해요' 하나만 눌러도 됩니다.",
        "keywords": ("글", "쓰기", "입력", "버튼", "말", "힘들"),
        "next": "누르기만 하는 안부를 시작해줘",
    },
    {
        "question": "누가 내 정보를 보나요?",
        "answer": "어르신이 일회용 코드로 직접 연결한 계정만 허용된 요약을 볼 수 있습니다. 계정마다 요약·활동 부재·응급 알림·일정 관리 권한을 따로 정하고 언제든 연결을 끊을 수 있습니다.",
        "keywords": ("정보", "누가", "개인", "저장", "기록", "보안", "가족"),
        "next": "개인정보 원칙을 보여줘",
    },
    {
        "question": "가족은 어떻게 연결하나요?",
        "answer": "어르신이 공유 범위를 확인하고 한 번만 쓸 수 있는 초대코드를 만듭니다. 지정한 가족이 코드를 입력하면 연결되며, 단톡방처럼 대화를 모두 공개하지 않고 허용된 요약과 알림만 전달합니다.",
        "keywords": ("가족", "연결", "초대", "코드", "계정", "권한"),
        "next": "가족 연결을 시작해줘",
    },
    {
        "question": "휴대폰을 계속 감시하나요?",
        "answer": "아니요. 동의한 경우에만 마지막 화면 사용과 휴대폰 이동 시각을 기록하며, 정확한 위치·화면 내용·원시 센서값은 저장하지 않습니다. 활동 부재는 배터리나 통신 문제일 수도 있어 먼저 본인 확인 후 가족에게 안내하고, 어르신이 언제든 중지할 수 있습니다.",
        "keywords": ("휴대폰", "핸드폰", "감시", "이동", "활동", "위치", "센서"),
        "next": "휴대폰 활동 확인 설정을 보여줘",
    },
    {
        "question": "웨어러블을 꼭 차야 하나요?",
        "answer": "아니요. 웨어러블 없이도 휴대폰 화면 사용·이동과 원터치 안부로 기본 돌봄이 작동합니다. 원할 때만 동의 후 웨어러블 동기화와 맥박 같은 기기 건강 기록을 더할 수 있습니다.",
        "keywords": ("웨어러블", "시계", "워치", "착용", "심박", "맥박"),
        "next": "웨어러블 없이 설정해줘",
    },
    {
        "question": "건강 판정은 진단인가요?",
        "answer": "아니요. 혈압·혈당·증상 분류는 재측정과 사람 확인을 돕는 참고입니다. 의료진의 진단과 개인별 기준을 대신하지 않습니다.",
        "keywords": ("진단", "의사", "혈압", "혈당", "건강", "정확"),
        "next": "건강 수치 기록 방법 알려줘",
    },
    {
        "question": "API나 AI가 없어도 되나요?",
        "answer": "네. 핵심 안부, 위험 키워드, 건강 기록, 안전계획과 안내는 규칙 기반으로 작동합니다. AI가 없거나 실패해도 안전한 기본 응답으로 이어집니다.",
        "keywords": ("api", "ai", "키", "인터넷", "오류", "없"),
        "next": "AI 없이 가능한 기능 보여줘",
    },
]


def _message_reply(label: str, text: str | None = None) -> dict[str, str]:
    return {"label": label, "action": "message", "messageText": text or label}


def _card(
    title: str,
    description: str,
    *,
    items: list[dict[str, str]] | None = None,
    tags: list[str] | None = None,
    buttons: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "guide",
        "title": title,
        "description": description,
        "items": items or [],
        "tags": tags or [],
        "buttons": buttons or [],
    }


def _faq_matches(question: str) -> list[dict[str, Any]]:
    query = str(question or "").lower().strip()
    if not query:
        return _FAQ
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in _FAQ:
        score = sum(1 for keyword in item["keywords"] if keyword in query)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:2]] or _FAQ[:3]


def _kakao_message(text: str, replies: list[str]) -> dict[str, Any]:
    return {
        "version": "2.0",
        "template": {
            "outputs": [{"simpleText": {"text": text}}],
            "quickReplies": [_message_reply(reply) for reply in replies[:10]],
        },
    }


def build_care_guide(
    action: GuideAction = "start",
    question: str = "",
    audience: GuideAudience = "senior",
) -> dict[str, Any]:
    """돌봄톡의 목적, 한 번 누르는 사용법, 접근성, FAQ와 개인정보 원칙을 안내합니다."""
    action = str(action or "start").strip().lower()
    question = str(question or "").strip()
    audience = str(audience or "senior").strip().lower()
    if action not in {"start", "examples", "faq", "accessibility", "privacy"}:
        return {"error": "action은 start, examples, faq, accessibility, privacy 중 하나여야 합니다."}
    if audience not in {"senior", "family", "helper"}:
        return {"error": "audience는 senior, family, helper 중 하나여야 합니다."}
    if len(question) > 300:
        return {"error": "question은 300자 이하여야 합니다."}

    if action == "start" and audience == "senior":
        message = (
            "안녕하세요. 돌봄톡이에요.\n\n"
            "매일 안부를 쉽게 확인하고,\n"
            "도움이 필요할 때 사람이 다시 확인하도록 도와드려요.\n\n"
            "길게 쓰지 않아도 됩니다.\n"
            "아래 버튼 하나만 눌러 주세요.\n\n"
            "돌봄톡은 자동으로 119에 신고하지 않아요."
        )
        replies = ["오늘은 괜찮아요", "밥 먹었어요", "조금 아파요", "도움이 필요해요", "가족 연결하기"]
        cards = [
            _card(
                "한 번만 눌러도 안부 완료",
                "기분, 식사, 약, 통증, 도움 요청을 큰 선택지로 답합니다.",
                items=[
                    {"label": "괜찮을 때", "value": "오늘은 괜찮아요"},
                    {"label": "불편할 때", "value": "조금 아파요"},
                    {"label": "급할 때", "value": "도움이 필요해요"},
                ],
                tags=["짧은 문장", "한 번 누르기", "사람 확인"],
                buttons=[{"label": "안부 시작", "action": "message", "value": "오늘 안부 확인 시작해줘"}],
            ),
            _card(
                "돌봄의 마지막은 사람",
                "AI는 신호를 정리합니다. 가족·복지사·의료진이 확인하고 결정합니다.",
                items=[
                    {"label": "일상", "value": "안부와 건강 기록"},
                    {"label": "걱정", "value": "가족·복지사 확인 제안"},
                    {"label": "위급", "value": "119 직접 연락 안내"},
                ],
                tags=["당사자 동의", "자동 신고 안 함"],
                buttons=[{"label": "자주 묻는 질문", "action": "message", "value": "돌봄톡 FAQ 보여줘"}],
            ),
            _card(
                "가족에게 필요한 요약만",
                "지정한 가족 계정에 정해진 시간의 중간·하루 요약과 허용된 알림만 전달합니다.",
                items=[
                    {"label": "연결", "value": "어르신이 만든 일회용 코드"},
                    {"label": "공유", "value": "계정별 요약·알림 권한"},
                    {"label": "해제", "value": "언제든 즉시 연결 끊기"},
                ],
                tags=["단톡방 아님", "최소 공유", "동의 철회"],
                buttons=[{"label": "가족 연결", "action": "message", "value": "가족 연결을 시작해줘"}],
            ),
        ]
    elif action == "start":
        role = "가족" if audience == "family" else "복지사·돌봄 담당자"
        message = (
            f"{role}용 안내입니다.\n\n"
            "당사자 동의를 먼저 확인하고, 안부 버튼과 건강 기록을 통해 변화를 살펴봅니다.\n"
            "위험 신호는 자동 신고가 아니라 실제 사람의 확인과 연락으로 이어져야 합니다."
        )
        replies = ["가족 연결하기", "예약 안부 설정", "오늘 안부 보기", "가족 리포트", "개인정보 원칙"]
        cards = [
            _card(
                "동의부터 시작하는 돌봄",
                "시간, 응답 여유, 확인할 사람의 역할, 접근성 요구를 당사자와 함께 정합니다.",
                items=[
                    {"label": "동의", "value": "누가 어떤 정보를 볼지 확인"},
                    {"label": "확인", "value": "무응답·불편·위급 단계 구분"},
                    {"label": "행동", "value": "가족·복지사·119 직접 연락"},
                ],
                tags=["human-in-the-loop", "최소수집"],
                buttons=[{"label": "안전계획", "action": "message", "value": "동의 기반 안전계획을 만들어줘"}],
            ),
            _card(
                "정해진 시간에 묻고 요약합니다",
                "아침·오후·저녁 질문을 어르신에게 보내고, 중간·하루 요약은 허용된 가족 계정에만 준비합니다.",
                items=[
                    {"label": "질문", "value": "원터치 안부·식사·복약·통증"},
                    {"label": "요약", "value": "원문 대신 필요한 상태만"},
                    {"label": "활동 부재", "value": "본인 확인 후 가족 안내"},
                    {"label": "웨어러블", "value": "없어도 사용, 원할 때만 연동"},
                ],
                tags=["예약 돌봄", "최소 활동 신호"],
                buttons=[{"label": "예약 설정", "action": "message", "value": "예약 안부를 설정해줘"}],
            ),
        ]
    elif action == "examples":
        examples = [
            ("안부", "오늘은 괜찮아요"),
            ("식사", "밥 먹었어요"),
            ("복약", "약 먹었어요"),
            ("통증", "무릎이 조금 아파요"),
            ("정서", "오늘 조금 외로워요"),
            ("도움", "도움이 필요해요"),
        ]
        message = "말을 길게 하지 않아도 괜찮아요. 지금 상황과 가까운 버튼을 눌러 주세요."
        replies = [text for _, text in examples[:5]]
        cards = [
            _card(
                label,
                text,
                tags=["추천 답변"],
                buttons=[{"label": "이 답변 보내기", "action": "message", "value": text}],
            )
            for label, text in examples
        ]
    elif action == "accessibility":
        message = (
            "보기 쉽고 누르기 쉽게 사용하세요.\n\n"
            "1. 카카오톡 글자 크기를 크게 조정해 주세요.\n"
            "2. 긴 글 대신 아래 선택 버튼을 이용하세요.\n"
            "3. 잘못 눌러도 다시 선택할 수 있어요.\n"
            "4. 어려우면 '도움이 필요해요'를 누르세요."
        )
        replies = ["큰 글씨 안내", "버튼으로 답할게요", "잘못 눌렀어요", "도움이 필요해요", "처음으로"]
        cards = [
            _card(
                "어르신 우선 화면 원칙",
                "핵심 문장을 짧게 나누고 자주 쓰는 답변을 먼저 보여줍니다.",
                items=[
                    {"label": "읽기", "value": "짧은 문장과 높은 대비"},
                    {"label": "누르기", "value": "고정된 큰 선택지"},
                    {"label": "실수", "value": "취소·다시 선택 가능"},
                ],
                tags=["큰 글씨 권장", "원터치", "쉬운 말"],
            )
        ]
    elif action == "privacy":
        message = "실제 이름, 전화번호, 상세 주소, 진료기록은 공개 데모에 입력하지 마세요. 가족 연결과 휴대폰 활동 확인은 어르신 동의와 계정별 권한을 먼저 정합니다."
        replies = ["누가 볼 수 있나요?", "기록 삭제", "가족 리포트 범위", "안전계획", "처음으로"]
        cards = [
            _card(
                "최소한만 기록합니다",
                "돌봄에 필요한 안부와 건강 변화만 다루고, 운영 전 권한·암호화·삭제기한을 갖춰야 합니다.",
                items=[
                    {"label": "입력 금지", "value": "전화번호, 상세 주소, 주민번호, 비밀번호"},
                    {"label": "동의 확인", "value": "열람자, 알림 단계, 보존기간"},
                    {"label": "활동 신호", "value": "시각만 저장, 위치·화면 내용 제외"},
                    {"label": "사용자 권리", "value": "열람·수정·동의 철회·삭제 요청"},
                ],
                tags=["최소수집", "동의", "삭제권"],
            )
        ]
    else:
        matches = _faq_matches(question)
        message = "궁금한 내용을 골라 보세요. 질문을 직접 적으면 가장 가까운 답만 보여드려요."
        replies = [item["question"] for item in _FAQ[:5]]
        cards = [
            _card(
                item["question"],
                item["answer"],
                tags=["자주 묻는 질문"],
                buttons=[{"label": "이어가기", "action": "message", "value": item["next"]}],
            )
            for item in matches
        ]

    return {
        "source": "built_in_guide",
        "action": action,
        "audience": audience,
        "message": message,
        "purpose": "어르신의 짧은 선택과 최소 활동 신호를 지정한 가족·복지사의 실제 확인으로 연결합니다.",
        "mock_mode_independent": True,
        "accessibility": {
            "large_text_recommended": True,
            "short_sentence_mode": True,
            "one_tap_replies": True,
            "automatic_emergency_dispatch": False,
        },
        "kakao_cards": cards,
        "quick_replies": replies,
        "message_json": _kakao_message(message, replies),
    }
