import json
import logging
import os
import sqlite3
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

# Ensure root workspace directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inference_service.schemas import (
    CallOutcome,
    CollectionsCallEvent,
    DLQRecord,
    DLQStats,
    DLQStatus,
)

logger = logging.getLogger("inference_service.dlq_handler")

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "dead_letters.db",
)


class DLQHandler:
    """
    Persistent Dead-Letter Queue & Call Compliance Audit Store.
    Supports AWS SQS (when SQS_DLQ_URL is configured) with local SQLite fallback/audit logging.
    Guarantees zero dropped or unlogged calls in collections operations.
    """

    def __init__(self, db_path: str | None = None, sqs_queue_url: str | None = None):
        self.db_path = db_path or os.getenv("DLQ_DB_PATH", DEFAULT_DB_PATH)
        self.sqs_queue_url = sqs_queue_url or os.getenv("SQS_DLQ_URL")
        self.aws_region = os.getenv("AWS_REGION", "ap-south-1")
        self._sqs_client = None
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_sqs_client(self):
        """Lazy-initializes thread-safe boto3 SQS client."""
        if self._sqs_client is None:
            try:
                import boto3

                self._sqs_client = boto3.client("sqs", region_name=self.aws_region)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Could not initialize boto3 SQS client: {e}")
                return None
        return self._sqs_client

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        # Enable Write-Ahead Logging (WAL) and busy timeout for concurrent read/write resilience
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        """Creates the dead_letter and call_audit tables."""
        with self._connection() as conn:
            # 1. Dead-Letter Queue Table (for calls that failed downstream and need replay)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letters (
                    event_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    borrower_bucket TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    call_duration_s INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    failure_reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    replayed_at TEXT,
                    replay_outcome TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letters(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dlq_campaign ON dead_letters(campaign_id)"
            )

            # 2. Complete Call Audit Log (Every call processed across all sessions)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS call_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    borrower_bucket TEXT NOT NULL,
                    product_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    retries INTEGER NOT NULL DEFAULT 0,
                    latency_ms REAL NOT NULL DEFAULT 0.0,
                    circuit_breaker_state TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    error_detail TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_event ON call_audit_log(event_id)"
            )
            conn.commit()

    def record_call_audit(
        self,
        event: CollectionsCallEvent,
        status: str,
        outcome: str,
        retries: int,
        latency_ms: float,
        cb_state: str,
        error_detail: str | None = None,
    ):
        """Logs every call event to the compliance audit store."""
        now_iso = datetime.now(UTC).isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO call_audit_log (
                    event_id, campaign_id, borrower_bucket, product_type,
                    status, outcome, retries, latency_ms, circuit_breaker_state,
                    processed_at, error_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.campaign_id,
                    event.borrower_bucket.value
                    if hasattr(event.borrower_bucket, "value")
                    else str(event.borrower_bucket),
                    event.product_type.value
                    if hasattr(event.product_type, "value")
                    else str(event.product_type),
                    status,
                    outcome,
                    retries,
                    latency_ms,
                    cb_state,
                    now_iso,
                    error_detail,
                ),
            )
            conn.commit()

    def record_dead_letter(
        self,
        event: CollectionsCallEvent,
        failure_reason: str,
        retries_attempted: int,
        raw_payload: dict[str, Any] | None = None,
    ) -> DLQRecord:
        """
        Persists a dropped or failed call event into the inspectable DLQ store.
        If SQS_DLQ_URL is configured, delivers to AWS SQS.
        """
        now_iso = datetime.now(UTC).isoformat()
        payload_dict = raw_payload or event.model_dump(mode="json")
        payload_json = json.dumps(payload_dict)

        # 1. Deliver to AWS SQS (Cloud Track)
        if self.sqs_queue_url:
            sqs = self._get_sqs_client()
            if sqs:
                try:
                    msg_body = json.dumps(
                        {
                            "event_id": event.event_id,
                            "campaign_id": event.campaign_id,
                            "borrower_bucket": event.borrower_bucket.value
                            if hasattr(event.borrower_bucket, "value")
                            else str(event.borrower_bucket),
                            "product_type": event.product_type.value
                            if hasattr(event.product_type, "value")
                            else str(event.product_type),
                            "timestamp": event.timestamp.isoformat()
                            if hasattr(event.timestamp, "isoformat")
                            else str(event.timestamp),
                            "call_duration_s": event.call_duration_s,
                            "outcome": CallOutcome.FAILED_DOWNSTREAM.value,
                            "retry_count": retries_attempted,
                            "failure_reason": failure_reason,
                            "created_at": now_iso,
                            "status": "PENDING",
                            "payload": payload_dict,
                        }
                    )
                    resp = sqs.send_message(
                        QueueUrl=self.sqs_queue_url,
                        MessageBody=msg_body,
                        MessageAttributes={
                            "event_id": {
                                "DataType": "String",
                                "StringValue": event.event_id,
                            },
                            "failure_reason": {
                                "DataType": "String",
                                "StringValue": failure_reason,
                            },
                            "campaign_id": {
                                "DataType": "String",
                                "StringValue": event.campaign_id,
                            },
                        },
                    )
                    logger.info(
                        f"Delivered dead-letter event {event.event_id} to AWS SQS ({self.sqs_queue_url}). MessageId: {resp.get('MessageId')}"
                    )
                except Exception:
                    logger.exception(
                        f"Failed to publish dead-letter {event.event_id} to AWS SQS"
                    )

        # 2. Local SQLite Persistence (Compliance audit & inspectability)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO dead_letters (
                    event_id, campaign_id, borrower_bucket, product_type,
                    timestamp, call_duration_s, outcome, retry_count,
                    failure_reason, created_at, status, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    retry_count = retry_count + excluded.retry_count,
                    failure_reason = excluded.failure_reason,
                    created_at = excluded.created_at,
                    status = 'PENDING'
                """,
                (
                    event.event_id,
                    event.campaign_id,
                    event.borrower_bucket.value
                    if hasattr(event.borrower_bucket, "value")
                    else str(event.borrower_bucket),
                    event.product_type.value
                    if hasattr(event.product_type, "value")
                    else str(event.product_type),
                    event.timestamp.isoformat()
                    if hasattr(event.timestamp, "isoformat")
                    else str(event.timestamp),
                    event.call_duration_s,
                    CallOutcome.FAILED_DOWNSTREAM.value,
                    retries_attempted,
                    failure_reason,
                    now_iso,
                    payload_json,
                ),
            )
            conn.commit()

        logger.info(
            f"Logged call event {event.event_id} to DLQ table. Reason: {failure_reason}"
        )

        record = self.get_record(event.event_id)
        if record is None:
            raise RuntimeError(
                f"Failed to retrieve newly inserted DLQ record {event.event_id}"
            )
        return record

    def get_record(self, event_id: str) -> DLQRecord | None:
        """Fetch a specific dead letter entry by event_id."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM dead_letters WHERE event_id = ?", (event_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def list_records(
        self,
        status: str | None = None,
        campaign_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DLQRecord]:
        """Inspect DLQ records with filtering and pagination."""
        query = "SELECT * FROM dead_letters WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status.upper())
        if campaign_id:
            query += " AND campaign_id = ?"
            params.append(campaign_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    def list_audit_logs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Inspect all past processed call events."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM call_audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_audit_summary(self) -> dict:
        """Get aggregate counts across all historical calls in the database."""
        with self._connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM call_audit_log").fetchone()[0]
            success = conn.execute(
                "SELECT COUNT(*) FROM call_audit_log WHERE status = 'success'"
            ).fetchone()[0]
            degraded = conn.execute(
                "SELECT COUNT(*) FROM call_audit_log WHERE status = 'degraded_dlq'"
            ).fetchone()[0]
            avg_lat = conn.execute(
                "SELECT COALESCE(AVG(latency_ms), 0.0) FROM call_audit_log"
            ).fetchone()[0]
            return {
                "total_historical_calls": total,
                "successful_calls": success,
                "degraded_dlq_calls": degraded,
                "average_latency_ms": round(avg_lat, 2),
            }

    def get_stats(self) -> DLQStats:
        """Inspect high-level metrics of dead letter queue (both SQLite and SQS)."""
        sqs_visible = None
        if self.sqs_queue_url:
            sqs = self._get_sqs_client()
            if sqs:
                try:
                    attrs = sqs.get_queue_attributes(
                        QueueUrl=self.sqs_queue_url,
                        AttributeNames=["ApproximateNumberOfMessages"],
                    )
                    sqs_visible = int(
                        attrs.get("Attributes", {}).get(
                            "ApproximateNumberOfMessages", 0
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Could not fetch SQS queue attributes: {exc}")

        with self._connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM dead_letters WHERE status = 'PENDING'"
            ).fetchone()[0]
            replayed = conn.execute(
                "SELECT COUNT(*) FROM dead_letters WHERE status = 'REPLAYED'"
            ).fetchone()[0]
            discarded = conn.execute(
                "SELECT COUNT(*) FROM dead_letters WHERE status = 'DISCARDED'"
            ).fetchone()[0]

            breakdown_cursor = conn.execute(
                "SELECT failure_reason, COUNT(*) FROM dead_letters GROUP BY failure_reason"
            )
            breakdown = {row[0]: row[1] for row in breakdown_cursor.fetchall()}

        return DLQStats(
            total_records=total,
            pending_records=sqs_visible if sqs_visible is not None else pending,
            replayed_records=replayed,
            discarded_records=discarded,
            failure_breakdown=breakdown,
            sqs_queue_url=self.sqs_queue_url,
            sqs_messages_visible=sqs_visible,
        )

    def receive_sqs_messages(
        self, max_messages: int = 10, wait_time_seconds: int = 2
    ) -> list[dict]:
        """Polls messages from SQS DLQ for inspection or replay."""
        if not self.sqs_queue_url:
            return []
        sqs = self._get_sqs_client()
        if not sqs:
            return []
        try:
            resp = sqs.receive_message(
                QueueUrl=self.sqs_queue_url,
                MaxNumberOfMessages=min(max_messages, 10),
                WaitTimeSeconds=wait_time_seconds,
                MessageAttributeNames=["All"],
            )
            messages = resp.get("Messages", [])
            results = []
            for m in messages:
                results.append(
                    {
                        "receipt_handle": m["ReceiptHandle"],
                        "body": json.loads(m["Body"])
                        if isinstance(m["Body"], str)
                        else m["Body"],
                    }
                )
            return results
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error receiving from SQS: {exc}")
            return []

    def delete_sqs_message(self, receipt_handle: str) -> bool:
        """Deletes a replayed message from SQS."""
        if not self.sqs_queue_url:
            return False
        sqs = self._get_sqs_client()
        if not sqs:
            return False
        try:
            sqs.delete_message(
                QueueUrl=self.sqs_queue_url, ReceiptHandle=receipt_handle
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error deleting SQS message: {exc}")
            return False

    def mark_replayed(
        self,
        event_id: str,
        success: bool,
        outcome: str = "connected",
        failure_reason: str | None = None,
    ) -> bool:
        """Mark record as replayed or update failure status."""
        now_iso = datetime.now(UTC).isoformat()
        with self._connection() as conn:
            if success:
                conn.execute(
                    """
                    UPDATE dead_letters
                    SET status = 'REPLAYED',
                        replayed_at = ?,
                        replay_outcome = ?
                    WHERE event_id = ?
                    """,
                    (now_iso, outcome, event_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE dead_letters
                    SET retry_count = retry_count + 1,
                        failure_reason = COALESCE(?, failure_reason),
                        replayed_at = ?,
                        replay_outcome = 'failed_retry'
                    WHERE event_id = ?
                    """,
                    (failure_reason, now_iso, event_id),
                )
            conn.commit()
        return True

    def _row_to_record(self, row: sqlite3.Row) -> DLQRecord:
        return DLQRecord(
            event_id=row["event_id"],
            campaign_id=row["campaign_id"],
            borrower_bucket=row["borrower_bucket"],
            product_type=row["product_type"],
            timestamp=row["timestamp"],
            call_duration_s=row["call_duration_s"],
            outcome=row["outcome"],
            retry_count=row["retry_count"],
            failure_reason=row["failure_reason"],
            created_at=row["created_at"],
            status=DLQStatus(row["status"]),
            replayed_at=row["replayed_at"],
            replay_outcome=row["replay_outcome"],
            payload=json.loads(row["payload"]),
        )


def print_cli_inspection():
    """Terminal viewer for the SQLite database."""
    handler = DLQHandler()
    audit_summary = handler.get_audit_summary()
    dlq_stats = handler.get_stats()
    dlq_records = handler.list_records(limit=20)

    print("\n" + "=" * 80)
    print(f"DATABASE INSPECTION: {handler.db_path}")
    print("=" * 80)
    print("Historical Call Audit Log:")
    print(f"  • Total Calls Recorded : {audit_summary['total_historical_calls']}")
    print(f"  • Successful Calls     : {audit_summary['successful_calls']}")
    print(f"  • Degraded to DLQ      : {audit_summary['degraded_dlq_calls']}")
    print(f"  • Average Latency      : {audit_summary['average_latency_ms']} ms")
    print("-" * 80)
    print("Dead-Letter Queue (DLQ) Status:")
    print(f"  • Total Dead Letters   : {dlq_stats.total_records}")
    print(f"  • Pending Replay       : {dlq_stats.pending_records}")
    print(f"  • Already Replayed     : {dlq_stats.replayed_records}")
    print(f"  • Failure Breakdown    : {dlq_stats.failure_breakdown}")
    print("-" * 80)

    if dlq_records:
        print("Latest Dead-Letter Records (showing up to 20):")
        print(f"{'Event ID':<38} | {'Status':<9} | {'Retries':<7} | {'Reason'}")
        print("-" * 80)
        for r in dlq_records:
            reason = (
                (r.failure_reason[:30] + "..")
                if len(r.failure_reason) > 30
                else r.failure_reason
            )
            print(
                f"{r.event_id:<38} | {r.status.value:<9} | {r.retry_count:<7} | {reason}"
            )
    else:
        print("No dead letters currently in queue.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    print_cli_inspection()
