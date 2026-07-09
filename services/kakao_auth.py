"""
카카오 로그인 OAuth 2.0 서비스 (스켈레톤)
==========================================
돌봄톡(CareTalk) MCP 서버의 카카오 로그인 연동 모듈입니다.
가족 구성원의 계정 연동 및 사용자 식별을 위한
카카오 OAuth 2.0 REST API 래퍼를 제공합니다.

실제 API 키 없이도 코드 구조를 완성하여
MVP 개발 시 즉시 연동 가능하도록 설계되었습니다.

기획서 참고: AGENTIC_PLAYER10_돌봄톡_v2_기획서.md 섹션 1.2
공식 문서: https://developers.kakao.com/docs/latest/ko/kakaologin/rest-api

주요 API 엔드포인트:
    - 인가코드 요청: https://kauth.kakao.com/oauth/authorize
    - 토큰 발급:     https://kauth.kakao.com/oauth/token (POST)
    - 사용자 정보:    https://kapi.kakao.com/v2/user/me (GET/POST)
    - 토큰 갱신:      https://kauth.kakao.com/oauth/token (grant_type=refresh_token)
    - 로그아웃:       https://kapi.kakao.com/v1/user/logout (POST)
    - 연결 해제:      https://kapi.kakao.com/v1/user/unlink (POST)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

# 실제 환경에서는 httpx 또는 requests 라이브러리 사용
# MVP 단계에서는 requests로 충분, 본선에서 httpx(async)로 전환 가능
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# 상수 정의
# ──────────────────────────────────────────────────────────────

# 카카오 OAuth 2.0 엔드포인트 (공식 문서 기준)
KAKAO_AUTH_BASE = "https://kauth.kakao.com"
KAKAO_API_BASE = "https://kapi.kakao.com"

AUTHORIZE_URL = f"{KAKAO_AUTH_BASE}/oauth/authorize"
TOKEN_URL = f"{KAKAO_AUTH_BASE}/oauth/token"
USER_INFO_URL = f"{KAKAO_API_BASE}/v2/user/me"
LOGOUT_URL = f"{KAKAO_API_BASE}/v1/user/logout"
UNLINK_URL = f"{KAKAO_API_BASE}/v1/user/unlink"

# 카카오 로그인 동의 항목 (scope)
# 돌봄톡 MVP에서 필요한 최소한의 정보만 요청 (개인정보 최소 수집 원칙)
SCOPE_PROFILE = "profile_nickname"          # 닉네임 (필수)
SCOPE_ACCOUNT_EMAIL = "account_email"       # 이메일 (선택)
SCOPE_GENDER = "gender"                     # 성별 (선택)
SCOPE_AGE_RANGE = "age_range"               # 연령대 (선택)
SCOPE_BIRTHDAY = "birthday"                 # 생일 (선택)
SCOPE_PHONE = "phone_number"                # 전화번호 (선택, 알림톡 수신용)

# 돌봄톡 기본 scope: 닉네임 + 전화번호 (알림톡 발송에 필요)
DEFAULT_SCOPE = [SCOPE_PROFILE, SCOPE_PHONE]


# ──────────────────────────────────────────────────────────────
# 카카오 로그인 API 함수
# ──────────────────────────────────────────────────────────────

def get_auth_url(
    rest_api_key: str,
    redirect_uri: str,
    scope: Optional[list] = None,
    state: Optional[str] = None,
) -> str:
    """
    카카오 로그인 인가코드 요청 URL을 생성합니다.

    사용자를 카카오 로그인 페이지로 리디렉션할 때 사용합니다.
    사용자가 로그인에 성공하면 redirect_uri로 인가코드(authorization code)가 전달됩니다.

    실제 사용법:
        1. [카카오 개발자 센터](https://developers.kakao.com/)에서 앱 등록
        2. 앱 설정 → 플랫폼 → Web 플랫폼 등록 → Redirect URI 설정
        3. 아래 함수로 인가 URL 생성 후 사용자 브라우저 리디렉션

        auth_url = get_auth_url(
            rest_api_key="YOUR_REST_API_KEY",
            redirect_uri="https://caretalk.example.com/oauth/callback",
            scope=["profile_nickname", "phone_number"],
        )
        # → https://kauth.kakao.com/oauth/authorize?client_id=...&redirect_uri=...&response_type=code&scope=...

    Args:
        rest_api_key: 카카오 개발자 센터에서 발급받은 REST API 키 (앱 키)
        redirect_uri: 인가코드를 받을 콜백 URL (카카오 개발자 센터에 등록된 URI와 일치해야 함)
        scope: 동의 항목 리스트. None이면 기본값 ["profile_nickname", "phone_number"] 사용
        state: CSRF 방지용 상태 토큰 (선택). None이면 생략

    Returns:
        카카오 로그인 페이지 URL 문자열

    Raises:
        ValueError: rest_api_key 또는 redirect_uri가 빈 문자열인 경우
    """
    if not rest_api_key:
        raise ValueError("rest_api_key는 필수입니다. 카카오 개발자 센터에서 발급받은 REST API 키를 입력하세요.")
    if not redirect_uri:
        raise ValueError("redirect_uri는 필수입니다. 카카오 개발자 센터에 등록된 Redirect URI를 입력하세요.")

    # 기본 scope 설정
    if scope is None:
        scope = DEFAULT_SCOPE

    # OAuth 2.0 인가코드 요청 파라미터
    params: Dict[str, str] = {
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "response_type": "code",            # 인가코드 방식 (Authorization Code Grant)
    }

    # scope는 공백 구분자로 연결 (카카오 API 규격)
    if scope:
        params["scope"] = " ".join(scope)

    # state 파라미터 (CSRF 방지)
    if state:
        params["state"] = state

    # URL 조립
    auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"
    logger.info(f"카카오 로그인 인가 URL 생성 완료: redirect_uri={redirect_uri}, scope={scope}")

    return auth_url


def get_token(
    rest_api_key: str,
    redirect_uri: str,
    auth_code: str,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    인가코드로 카카오 토큰(access_token + refresh_token)을 발급받습니다.

    사용자가 카카오 로그인 후 redirect_uri로 전달된 인가코드를
    access_token으로 교환합니다. access_token은 이후 사용자 정보 조회 등
    모든 API 호출에 사용됩니다.

    실제 사용법:
        # Flask/FastAPI 콜백 엔드포인트에서
        @app.route("/oauth/callback")
        def callback():
            auth_code = request.args.get("code")
            token_data = get_token(
                rest_api_key="YOUR_REST_API_KEY",
                redirect_uri="https://caretalk.example.com/oauth/callback",
                auth_code=auth_code,
            )
            access_token = token_data["access_token"]
            # → DB에 저장하거나 세션에 보관

    POST 요청:
        URL: https://kauth.kakao.com/oauth/token
        Content-Type: application/x-www-form-urlencoded
        파라미터:
            grant_type=authorization_code
            client_id={REST_API_KEY}
            redirect_uri={REDIRECT_URI}
            code={AUTH_CODE}
            client_secret={CLIENT_SECRET}  # 선택 (보안 강화용)

    Args:
        rest_api_key: 카카오 REST API 키
        redirect_uri: 인가코드 요청 시 사용한 것과 동일한 Redirect URI
        auth_code: 카카오 로그인 후 콜백으로 전달받은 인가코드
        client_secret: 카카오 Client Secret (선택). 보안 강화를 위해 활성화 권장

    Returns:
        토큰 정보 딕셔너리:
        {
            "access_token": "액세스 토큰 (API 호출용, 유효기간 12시간)",
            "token_type": "bearer",
            "refresh_token": "리프레시 토큰 (갱신용, 유효기간 2개월)",
            "expires_in": 43200,  # 초 단위 (12시간)
            "scope": "profile_nickname phone_number",
            "refresh_token_expires_in": 5184000,  # 초 단위 (60일)
        }

    Raises:
        RuntimeError: API 호출 실패 시 (네트워크 오류, 잘못된 인가코드 등)
        ImportError: requests 라이브러리가 설치되지 않은 경우
    """
    if not HAS_REQUESTS:
        raise ImportError(
            "requests 라이브러리가 필요합니다. 'pip install requests'로 설치하세요."
        )

    # POST 요청 파라미터 구성
    payload: Dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": redirect_uri,
        "code": auth_code,
    }

    if client_secret:
        payload["client_secret"] = client_secret

    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }

    logger.info(f"카카오 토큰 발급 요청: redirect_uri={redirect_uri}")

    try:
        response = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=10)
        response.raise_for_status()
        token_data = response.json()

        logger.info(
            f"토큰 발급 성공: expires_in={token_data.get('expires_in')}초, "
            f"scope={token_data.get('scope')}"
        )
        return token_data

    except requests.exceptions.RequestException as e:
        logger.error(f"카카오 토큰 발급 실패: {e}")
        raise RuntimeError(f"카카오 토큰 발급 중 오류 발생: {e}") from e


def get_user_info(access_token: str) -> Dict[str, Any]:
    """
    카카오 사용자 정보를 조회합니다.

    발급받은 access_token으로 사용자의 카카오 계정 정보를 가져옵니다.
    동의한 scope에 따라 조회 가능한 정보가 달라집니다.

    실제 사용법:
        user_info = get_user_info(access_token="USER_ACCESS_TOKEN")
        nickname = user_info["properties"]["nickname"]
        email = user_info.get("kakao_account", {}).get("email")

    요청 방식:
        GET/POST https://kapi.kakao.com/v2/user/me
        Header: Authorization: Bearer {ACCESS_TOKEN}

    Args:
        access_token: get_token()으로 발급받은 액세스 토큰

    Returns:
        사용자 정보 딕셔너리:
        {
            "id": 1234567890,  # 카카오 회원번호 (앱별로 다름)
            "properties": {
                "nickname": "홍길동",
                "profile_image": "https://...",
                "thumbnail_image": "https://..."
            },
            "kakao_account": {
                "profile_nickname_needs_agreement": false,
                "profile": {"nickname": "홍길동"},
                "email": "user@example.com",  # scope에 email 포함 시
                "phone_number": "+82 10-1234-5678",  # scope에 phone_number 포함 시
                "gender": "male",  # scope에 gender 포함 시
                "age_range": "30~39",  # scope에 age_range 포함 시
                "birthday": "0101"  # scope에 birthday 포함 시
            }
        }

    Raises:
        RuntimeError: API 호출 실패 시 (만료된 토큰, 네트워크 오류 등)
        ImportError: requests 라이브러리가 설치되지 않은 경우
    """
    if not HAS_REQUESTS:
        raise ImportError(
            "requests 라이브러리가 필요합니다. 'pip install requests'로 설치하세요."
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }

    logger.info("카카오 사용자 정보 조회 요청")

    try:
        response = requests.get(USER_INFO_URL, headers=headers, timeout=10)
        response.raise_for_status()
        user_data = response.json()

        nickname = user_data.get("properties", {}).get("nickname", "알 수 없음")
        logger.info(f"사용자 정보 조회 성공: nickname={nickname}, id={user_data.get('id')}")
        return user_data

    except requests.exceptions.RequestException as e:
        logger.error(f"카카오 사용자 정보 조회 실패: {e}")
        raise RuntimeError(f"카카오 사용자 정보 조회 중 오류 발생: {e}") from e


def refresh_token(
    rest_api_key: str,
    refresh_token_value: str,
    client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    만료된 access_token을 refresh_token으로 갱신합니다.

    access_token 유효기간은 12시간이므로,
    장기간 서비스 이용을 위해 refresh_token으로 갱신해야 합니다.
    refresh_token 유효기간은 2개월입니다.

    실제 사용법:
        new_token = refresh_token(
            rest_api_key="YOUR_REST_API_KEY",
            refresh_token_value="STORED_REFRESH_TOKEN",
        )
        # → 새 access_token + 갱신된 refresh_token 반환

    POST 요청:
        URL: https://kauth.kakao.com/oauth/token
        파라미터:
            grant_type=refresh_token
            client_id={REST_API_KEY}
            refresh_token={REFRESH_TOKEN}
            client_secret={CLIENT_SECRET}  # 선택

    Args:
        rest_api_key: 카카오 REST API 키
        refresh_token_value: 저장된 리프레시 토큰
        client_secret: 카카오 Client Secret (선택)

    Returns:
        갱신된 토큰 정보 딕셔너리 (get_token()과 동일 구조).
        refresh_token도 함께 갱신될 수 있으므로 DB 업데이트 필요.

    Raises:
        RuntimeError: API 호출 실패 시 (만료된 refresh_token 등)
        ImportError: requests 라이브러리가 설치되지 않은 경우
    """
    if not HAS_REQUESTS:
        raise ImportError(
            "requests 라이브러리가 필요합니다. 'pip install requests'로 설치하세요."
        )

    payload: Dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token_value,
    }

    if client_secret:
        payload["client_secret"] = client_secret

    headers = {
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }

    logger.info("카카오 토큰 갱신 요청")

    try:
        response = requests.post(TOKEN_URL, data=payload, headers=headers, timeout=10)
        response.raise_for_status()
        token_data = response.json()

        logger.info(
            f"토큰 갱신 성공: new_expires_in={token_data.get('expires_in')}초"
        )
        return token_data

    except requests.exceptions.RequestException as e:
        logger.error(f"카카오 토큰 갱신 실패: {e}")
        raise RuntimeError(f"카카오 토큰 갱신 중 오류 발생: {e}") from e


def logout_user(access_token: str) -> Dict[str, Any]:
    """
    카카오 로그아웃을 수행합니다. (access_token 만료)

    실제 사용법:
        logout_user(access_token="USER_ACCESS_TOKEN")

    POST 요청:
        URL: https://kapi.kakao.com/v1/user/logout
        Header: Authorization: Bearer {ACCESS_TOKEN}

    Args:
        access_token: 로그아웃할 사용자의 액세스 토큰

    Returns:
        {"id": 1234567890}  # 로그아웃된 사용자 ID

    Raises:
        RuntimeError: API 호출 실패 시
        ImportError: requests 라이브러리가 설치되지 않은 경우
    """
    if not HAS_REQUESTS:
        raise ImportError(
            "requests 라이브러리가 필요합니다. 'pip install requests'로 설치하세요."
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    logger.info("카카오 로그아웃 요청")

    try:
        response = requests.post(LOGOUT_URL, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
        logger.info(f"로그아웃 성공: id={result.get('id')}")
        return result

    except requests.exceptions.RequestException as e:
        logger.error(f"카카오 로그아웃 실패: {e}")
        raise RuntimeError(f"카카오 로그아웃 중 오류 발생: {e}") from e


def unlink_user(access_token: str) -> Dict[str, Any]:
    """
    카카오 연결 해제(앱 탈퇴)를 수행합니다.

    사용자가 돌봄톡 서비스에서 탈퇴할 때 호출합니다.
    연결 해제 시 해당 앱의 카카오 회원번호(id)가 만료되며,
    재가입 시 새로운 회원번호가 발급됩니다.

    실제 사용법:
        unlink_user(access_token="USER_ACCESS_TOKEN")

    POST 요청:
        URL: https://kapi.kakao.com/v1/user/unlink
        Header: Authorization: Bearer {ACCESS_TOKEN}

    Args:
        access_token: 연결 해제할 사용자의 액세스 토큰

    Returns:
        {"id": 1234567890}  # 연결 해제된 사용자 ID

    Raises:
        RuntimeError: API 호출 실패 시
        ImportError: requests 라이브러리가 설치되지 않은 경우
    """
    if not HAS_REQUESTS:
        raise ImportError(
            "requests 라이브러리가 필요합니다. 'pip install requests'로 설치하세요."
        )

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    logger.info("카카오 연결 해제 요청")

    try:
        response = requests.post(UNLINK_URL, headers=headers, timeout=10)
        response.raise_for_status()
        result = response.json()
        logger.info(f"연결 해제 성공: id={result.get('id')}")
        return result

    except requests.exceptions.RequestException as e:
        logger.error(f"카카오 연결 해제 실패: {e}")
        raise RuntimeError(f"카카오 연결 해제 중 오류 발생: {e}") from e


# ──────────────────────────────────────────────────────────────
# 모듈 직접 실행 테스트 (API 키 없이 URL 생성만 검증)
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("카카오 로그인 모듈 테스트 (API 키 없이 URL 생성 검증)")
    print("=" * 60)

    # ── 테스트 1: 인가 URL 생성 ──
    print("\n[테스트 1] get_auth_url - 기본 scope")
    try:
        url = get_auth_url(
            rest_api_key="test_api_key_12345",
            redirect_uri="https://caretalk.example.com/oauth/callback",
        )
        print(f"생성된 URL:\n{url}")
        # URL에 필수 파라미터가 포함되어 있는지 확인
        assert "client_id=test_api_key_12345" in url
        assert "response_type=code" in url
        assert "redirect_uri=https%3A%2F%2Fcaretalk.example.com%2Foauth%2Fcallback" in url
        assert "scope=profile_nickname+phone_number" in url
        print("✅ URL 파라미터 검증 통과")
    except Exception as e:
        print(f"❌ 실패: {e}")

    # ── 테스트 2: 인가 URL 생성 - 커스텀 scope + state ──
    print("\n[테스트 2] get_auth_url - 커스텀 scope + state")
    try:
        url = get_auth_url(
            rest_api_key="custom_key_67890",
            redirect_uri="https://example.com/callback",
            scope=["profile_nickname", "account_email", "phone_number"],
            state="csrf_token_abc123",
        )
        print(f"생성된 URL:\n{url}")
        assert "scope=profile_nickname+account_email+phone_number" in url
        assert "state=csrf_token_abc123" in url
        print("✅ 커스텀 파라미터 검증 통과")
    except Exception as e:
        print(f"❌ 실패: {e}")

    # ── 테스트 3: 필수 파라미터 누락 시 ValueError ──
    print("\n[테스트 3] get_auth_url - rest_api_key 누락 → ValueError")
    try:
        get_auth_url(rest_api_key="", redirect_uri="https://example.com/callback")
        print("❌ ValueError가 발생해야 하는데 발생하지 않음")
    except ValueError as e:
        print(f"✅ 예상된 ValueError: {e}")

    # ── 테스트 4: redirect_uri 누락 → ValueError ──
    print("\n[테스트 4] get_auth_url - redirect_uri 누락 → ValueError")
    try:
        get_auth_url(rest_api_key="test_key", redirect_uri="")
        print("❌ ValueError가 발생해야 하는데 발생하지 않음")
    except ValueError as e:
        print(f"✅ 예상된 ValueError: {e}")

    # ── 테스트 5: requests 미설치 시 get_token 동작 ──
    print("\n[테스트 5] get_token - requests 미설치 시 ImportError")
    if not HAS_REQUESTS:
        try:
            get_token(
                rest_api_key="test",
                redirect_uri="https://example.com",
                auth_code="test_code",
            )
            print("❌ ImportError가 발생해야 하는데 발생하지 않음")
        except ImportError as e:
            print(f"✅ 예상된 ImportError: {e}")
    else:
        print("ℹ️ requests 설치됨 — 실제 API 호출은 API 키가 필요하므로 스킵")

    print("\n" + "=" * 60)
    print("카카오 로그인 모듈 테스트 완료!")
    print("=" * 60)
