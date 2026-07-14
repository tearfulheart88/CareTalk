"""Signed delivery webhook adapter for the CareTalk durable outbox.

The MCP server never exposes provider credentials or phone numbers. An operator-owned
HTTPS gateway maps the opaque linked account ID to an approved Kakao AlimTalk or
other consented delivery channel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests


class DeliveryError(Exception):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_timeout() -> float:
    try:
        value = float(os.environ.get("CARETALK_DELIVERY_TIMEOUT_SECONDS", "3"))
    except ValueError:
        value = 3.0
    return max(1.0, min(value, 10.0))


def delivery_mode() -> str:
    return os.environ.get("CARETALK_DELIVERY_MODE", "outbox").strip().lower() or "outbox"


def _validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DeliveryError("전달 웹훅 URL은 사용자 정보·쿼리·프래그먼트가 없는 절대 URL이어야 합니다.")
    if parsed.scheme == "https":
        return
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if (
        parsed.scheme == "http"
        and parsed.hostname in local_hosts
        and _env_bool("CARETALK_ALLOW_INSECURE_LOCAL_WEBHOOK", False)
    ):
        return
    raise DeliveryError("전달 웹훅은 HTTPS만 허용됩니다.")


def delivery_status() -> dict[str, Any]:
    mode = delivery_mode()
    url = os.environ.get("CARETALK_DELIVERY_WEBHOOK_URL", "").strip()
    secret = os.environ.get("CARETALK_DELIVERY_WEBHOOK_SECRET", "")
    host = urlparse(url).hostname if url else ""
    valid_mode = mode in {"outbox", "webhook"}
    ready = valid_mode and (
        mode == "outbox" or (bool(url) and len(secret.encode("utf-8")) >= 32)
    )
    warning = ""
    if not valid_mode:
        warning = "CARETALK_DELIVERY_MODE는 outbox 또는 webhook이어야 합니다."
    elif mode == "outbox":
        warning = "메시지는 영구 대기열에만 저장되며 외부로 발송되지 않습니다."
    elif not url or len(secret.encode("utf-8")) < 32:
        warning = "HTTPS 웹훅 URL과 32바이트 이상의 서명 비밀키가 필요합니다."
    else:
        try:
            _validate_webhook_url(url)
        except DeliveryError as exc:
            ready = False
            warning = str(exc)
    return {
        "mode": mode,
        "ready": ready,
        "webhook_host": host or None,
        "secret_configured": bool(secret),
        "credentials_exposed": False,
        "warning": warning,
    }


@dataclass(frozen=True)
class DeliveryResult:
    provider_message_id: str = ""
    status_code: int = 200


class WebhookDeliveryClient:
    def __init__(self) -> None:
        if delivery_mode() != "webhook":
            raise DeliveryError("웹훅 전달 모드가 아닙니다.")
        self.url = os.environ.get("CARETALK_DELIVERY_WEBHOOK_URL", "").strip()
        self.secret = os.environ.get("CARETALK_DELIVERY_WEBHOOK_SECRET", "").encode("utf-8")
        if not self.url:
            raise DeliveryError("CARETALK_DELIVERY_WEBHOOK_URL이 필요합니다.")
        if len(self.secret) < 32:
            raise DeliveryError("CARETALK_DELIVERY_WEBHOOK_SECRET은 32바이트 이상이어야 합니다.")
        _validate_webhook_url(self.url)
        self.timeout = _safe_timeout()

    @staticmethod
    def _body(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": "1.0",
            "delivery_id": str(item["id"]),
            "idempotency_key": item["dedupe_key"],
            "recipient": {
                "account_user_id": item["recipient_user_id"],
                "identifier_type": "opaque_linked_account",
            },
            "event": {
                "type": item["event_type"],
                "severity": item["severity"],
                "due_at": item["due_at"],
            },
            "payload": item.get("payload") or {},
            "privacy": {
                "phone_number_included": False,
                "provider_credentials_included": False,
            },
        }

    def send(self, item: dict[str, Any]) -> DeliveryResult:
        body = json.dumps(
            self._body(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        digest = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-CareTalk-Signature": "sha256=" + digest,
            "X-CareTalk-Delivery-Id": str(item["id"]),
            "Idempotency-Key": str(item["dedupe_key"]),
            "User-Agent": "CareTalk-Delivery/1.0",
        }
        try:
            response = requests.post(
                self.url,
                data=body,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise DeliveryError(f"전달 게이트웨이 연결 실패: {type(exc).__name__}") from exc
        if not 200 <= response.status_code < 300:
            raise DeliveryError(f"전달 게이트웨이 응답 오류: HTTP {response.status_code}")
        provider_message_id = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                provider_message_id = str(
                    data.get("provider_message_id") or data.get("message_id") or data.get("id") or ""
                )[:200]
        except (ValueError, TypeError):
            pass
        return DeliveryResult(
            provider_message_id=provider_message_id,
            status_code=response.status_code,
        )
