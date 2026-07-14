#!/usr/bin/env python3
"""Real HTTP and official MCP client integration checks for CareTalk."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


ROOT = Path(__file__).resolve().parent
PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: Any = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  OK  {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}: {str(detail)[:300]}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def prepare_database(db_path: str) -> str:
    from tools.care_circle import manage_care_circle
    from tools.care_routine import manage_care_routine

    senior = "http-integration-senior"
    circle = manage_care_circle(
        "create_invite",
        senior,
        senior,
        nickname="순자",
        senior_consented=True,
        db_path=db_path,
    )
    if circle.get("error"):
        raise RuntimeError(circle["error"])
    configured = manage_care_routine(
        "configure",
        senior,
        senior,
        senior_consented=True,
        phone_activity_enabled=True,
        db_path=db_path,
    )
    if configured.get("error"):
        raise RuntimeError(configured["error"])
    pairing = manage_care_routine(
        "create_device_pairing",
        senior,
        senior,
        device_type="phone",
        device_label="통합 테스트 휴대폰",
        pairing_minutes=10,
        senior_consented=True,
        db_path=db_path,
    )
    if pairing.get("error"):
        raise RuntimeError(pairing["error"])
    return str(pairing["pairing_code"])


def wait_for_server(base_url: str, process: subprocess.Popen[str]) -> dict[str, Any]:
    last_error = ""
    for _ in range(60):
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"server exited early: {stderr[-1200:]}")
        try:
            response = requests.get(base_url + "/health", timeout=1.0)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise RuntimeError("server readiness timeout: " + last_error)


async def check_mcp(base_url: str) -> None:
    async with streamable_http_client(base_url + "/mcp") as (read, write, _):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            check("MCP initialize", bool(initialized.serverInfo.name), initialized)
            tools = await session.list_tools()
            check("MCP tools/list 12개", len(tools.tools) == 12, [tool.name for tool in tools.tools])
            guide = await session.call_tool(
                "care_guide", {"action": "start", "audience": "senior"}
            )
            check("MCP 대표 Tool 호출", guide.isError is False, guide)
            invalid = await session.call_tool(
                "care_routine",
                {"action": "status", "requester_user_id": "missing-senior"},
            )
            check("MCP 오류 isError", invalid.isError is True, invalid)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    temp_dir = tempfile.mkdtemp(prefix="caretalk_http_test_")
    db_path = os.path.join(temp_dir, "integration.db")
    pairing_code = prepare_database(db_path)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "CARETALK_DB_PATH": db_path,
            "CARE_WORKER_ENABLED": "false",
            "CARETALK_DELIVERY_MODE": "outbox",
            "MOCK_MODE": "true",
            "LIVE_API_ENABLED": "false",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--mock",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        print("=" * 60)
        print("돌봄톡 실제 HTTP + 공식 MCP 통합 검증")
        print("=" * 60)
        status = wait_for_server(base_url, process)
        check("상태 API 버전", status.get("version") == "3.3.0", status)
        check(
            "worker 비활성 테스트 격리",
            status.get("worker", {}).get("running") is False,
            status.get("worker"),
        )
        check(
            "기기 API 개인정보 경계",
            status.get("device_bridge", {}).get("exact_location_collected") is False,
            status.get("device_bridge"),
        )

        wrong_type = requests.post(
            base_url + "/device/pair",
            data="{}",
            headers={"Content-Type": "text/plain"},
            timeout=3,
        )
        check("기기 API Content-Type 제한", wrong_type.status_code == 415, wrong_type.text)

        paired_response = requests.post(
            base_url + "/device/pair",
            json={"pairing_code": pairing_code},
            timeout=3,
        )
        paired = paired_response.json()
        device_token = str(paired.get("device_token", ""))
        check(
            "일회용 코드 HTTP 교환",
            paired_response.status_code == 201 and device_token.startswith("ctd_"),
            paired,
        )
        check(
            "토큰 응답 캐시 차단",
            paired_response.headers.get("Cache-Control") == "no-store",
            dict(paired_response.headers),
        )

        unauthorized = requests.post(
            base_url + "/device/activity",
            json={"event_type": "screen_unlock", "event_id": "unauthorized"},
            timeout=3,
        )
        check("기기 토큰 누락 401", unauthorized.status_code == 401, unauthorized.text)

        headers = {"Authorization": "Bearer " + device_token}
        activity_body = {"event_type": "screen_unlock", "event_id": "http-activity-1"}
        activity = requests.post(
            base_url + "/device/activity", json=activity_body, headers=headers, timeout=3
        ).json()
        activity_duplicate = requests.post(
            base_url + "/device/activity", json=activity_body, headers=headers, timeout=3
        ).json()
        check("인증된 활동 HTTP 수취", activity.get("status") == "recorded", activity)
        check(
            "활동 HTTP 중복 방지",
            activity_duplicate.get("status") == "duplicate_ignored",
            activity_duplicate,
        )

        health_body = {
            "event_id": "http-health-1",
            "data_type": "heart_rate",
            "value": 74,
        }
        health_response = requests.post(
            base_url + "/device/health", json=health_body, headers=headers, timeout=3
        )
        health = health_response.json()
        health_duplicate = requests.post(
            base_url + "/device/health", json=health_body, headers=headers, timeout=3
        ).json()
        check(
            "인증된 건강 HTTP 수취",
            health_response.status_code == 201 and health.get("source") == "device",
            health,
        )
        check(
            "건강 HTTP 중복 방지",
            health_duplicate.get("status") == "duplicate_ignored",
            health_duplicate,
        )

        conn = sqlite3.connect(db_path)
        try:
            token_hash = conn.execute(
                "SELECT token_hash FROM care_devices WHERE device_id = ?",
                (paired.get("device_id"),),
            ).fetchone()[0]
        finally:
            conn.close()
        check(
            "기기 토큰 평문 미저장",
            device_token not in token_hash and len(token_hash) == 64,
            token_hash,
        )

        asyncio.run(check_mcp(base_url))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("=" * 60)
    print(f"결과: {PASSED}개 통과 / {FAILED}개 실패")
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
