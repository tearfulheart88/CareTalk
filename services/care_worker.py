"""Background scheduler and durable outbox delivery worker for CareTalk."""

from __future__ import annotations

import atexit
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from db.schema import DB_PATH, ensure_schema
from services.notification_delivery import (
    DeliveryError,
    WebhookDeliveryClient,
    delivery_mode,
    delivery_status,
)
from tools.care_routine import (
    claim_pending_notifications,
    mark_notification_delivery,
    run_due_care_tasks,
)


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CareWorker:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.interval_seconds = _env_int("CARE_WORKER_INTERVAL_SECONDS", 30, 5, 3600)
        self.batch_size = _env_int("CARE_WORKER_BATCH_SIZE", 50, 1, 200)
        self.lease_seconds = _env_int("CARE_WORKER_LEASE_SECONDS", 90, 30, 600)
        self.max_attempts = _env_int("CARE_WORKER_MAX_ATTEMPTS", 5, 1, 20)
        self.retry_base_seconds = _env_int("CARE_WORKER_RETRY_BASE_SECONDS", 30, 5, 600)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "enabled": True,
            "running": False,
            "last_cycle_started_at": None,
            "last_cycle_completed_at": None,
            "last_error": "",
            "routines_processed": 0,
            "events_queued": 0,
            "deliveries_sent": 0,
            "deliveries_retried": 0,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        ensure_schema(self.db_path)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="caretalk-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _set_state(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def _run(self) -> None:
        self._set_state(running=True)
        try:
            while not self._stop_event.is_set():
                try:
                    self.run_cycle()
                except Exception as exc:  # Keep the next scheduled cycle alive.
                    logger.exception("CareTalk worker cycle failed")
                    self._set_state(last_error=f"{type(exc).__name__}: {str(exc)[:300]}")
                self._stop_event.wait(self.interval_seconds)
        finally:
            self._set_state(running=False)

    def run_cycle(self) -> dict[str, int]:
        started_at = _iso_now()
        self._set_state(last_cycle_started_at=started_at, last_error="")
        conn = sqlite3.connect(self.db_path, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            senior_ids = [
                row["senior_user_id"]
                for row in conn.execute(
                    """SELECT senior_user_id FROM care_routines
                       WHERE senior_consented = 1 ORDER BY senior_user_id"""
                ).fetchall()
            ]
        finally:
            conn.close()

        queued_count = 0
        routine_errors: list[str] = []
        for senior_user_id in senior_ids:
            try:
                result = run_due_care_tasks(
                    senior_user_id,
                    senior_user_id,
                    db_path=self.db_path,
                )
            except Exception as exc:
                logger.exception("Care routine execution failed")
                routine_errors.append(f"{type(exc).__name__}: routine execution failed")
                continue
            if result.get("error"):
                routine_errors.append(str(result["error"]))
            else:
                queued_count += int(result.get("queued_count", 0))

        sent_count = 0
        retry_count = 0
        if delivery_mode() == "webhook":
            try:
                client = WebhookDeliveryClient()
            except DeliveryError as exc:
                client = None
                routine_errors.append(str(exc))
            items = (
                claim_pending_notifications(
                    limit=self.batch_size,
                    lease_seconds=self.lease_seconds,
                    max_attempts=self.max_attempts,
                    db_path=self.db_path,
                )
                if client is not None
                else []
            )
            for item in items:
                try:
                    delivered = client.send(item)
                    if mark_notification_delivery(
                        item["id"],
                        "sent",
                        claim_token=item["claim_token"],
                        provider_message_id=delivered.provider_message_id,
                        max_attempts=self.max_attempts,
                        base_delay_seconds=self.retry_base_seconds,
                        db_path=self.db_path,
                    ):
                        sent_count += 1
                except DeliveryError as exc:
                    marked = mark_notification_delivery(
                        item["id"],
                        "failed",
                        claim_token=item["claim_token"],
                        error=str(exc),
                        max_attempts=self.max_attempts,
                        base_delay_seconds=self.retry_base_seconds,
                        db_path=self.db_path,
                    )
                    if marked:
                        retry_count += 1
                    logger.warning("Delivery %s failed: %s", item["id"], exc)
                except Exception as exc:
                    marked = mark_notification_delivery(
                        item["id"],
                        "failed",
                        claim_token=item["claim_token"],
                        error=f"{type(exc).__name__}: {str(exc)[:240]}",
                        max_attempts=self.max_attempts,
                        base_delay_seconds=self.retry_base_seconds,
                        db_path=self.db_path,
                    )
                    if marked:
                        retry_count += 1
                    logger.exception("Unexpected delivery failure for %s", item["id"])

        error_text = "; ".join(routine_errors[:3])
        self._set_state(
            last_cycle_completed_at=_iso_now(),
            last_error=error_text,
            routines_processed=len(senior_ids),
            events_queued=queued_count,
            deliveries_sent=sent_count,
            deliveries_retried=retry_count,
        )
        return {
            "routines_processed": len(senior_ids),
            "events_queued": queued_count,
            "deliveries_sent": sent_count,
            "deliveries_retried": retry_count,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._state)
        result.update(
            {
                "interval_seconds": self.interval_seconds,
                "batch_size": self.batch_size,
                "delivery": delivery_status(),
            }
        )
        result["outbox"] = _outbox_counts(self.db_path)
        return result


def _outbox_counts(db_path: str) -> dict[str, int]:
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=2.0)
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM care_outbox GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    counts = {str(status): int(count) for status, count in rows}
    return {
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "sent": counts.get("sent", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
        "acknowledged": counts.get("acknowledged", 0),
    }


_WORKER: Optional[CareWorker] = None
_WORKER_LOCK = threading.Lock()


def start_care_worker(db_path: str = DB_PATH) -> CareWorker:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = CareWorker(db_path)
            atexit.register(_WORKER.stop)
        _WORKER.start()
        return _WORKER


def care_worker_status(db_path: str = DB_PATH, enabled: bool = False) -> dict[str, Any]:
    if _WORKER is not None:
        return _WORKER.status()
    return {
        "enabled": enabled,
        "running": False,
        "last_cycle_started_at": None,
        "last_cycle_completed_at": None,
        "last_error": "",
        "delivery": delivery_status(),
        "outbox": _outbox_counts(db_path),
    }
