from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from inference_service.schemas import (
    BorrowerBucket,
    CallOutcome,
    CollectionsCallEvent,
    DLQRecord,
    DLQStats,
    DLQStatus,
    ProductType,
)

logger = logging.getLogger("inference_service.dlq_handler")

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "dead_letters.db"
)


class DLQHandler:
    """
    Persistent Dead-Letter Queue handler backed by SQLite.
    Provides inspectable storage and replayability for compliance guarantees in collections.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("DLQ_DB_PATH", DEFAULT_DB_PATH)
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Creates the dead_letter table if it does not exist."""
        with self._get_connection() as conn:
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
            conn.commit()

    def record_dead_letter(
        self,
        event: CollectionsCallEvent,
        failure_reason: str,
        retries_attempted: int,
        raw_payload: Optional[Dict[str, Any]] = None,
    ) -> DLQRecord:
        """
        Persists a dropped or failed call event into the inspectable DLQ store.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        payload_dict = raw_payload or event.model_dump(mode="json")
        payload_json = json.dumps(payload_dict)

        with self._get_connection() as conn:
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
                    event.borrower_bucket.value,
                    event.product_type.value,
                    event.timestamp.isoformat() if hasattr(event.timestamp, "isoformat") else str(event.timestamp),
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

        return self.get_record(event.event_id)

    def get_record(self, event_id: str) -> Optional[DLQRecord]:
        """Fetch a specific dead letter entry by event_id."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM dead_letters WHERE event_id = ?", (event_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def list_records(
        self,
        status: Optional[str] = None,
        campaign_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[DLQRecord]:
        """Inspect DLQ records with filtering and pagination."""
        query = "SELECT * FROM dead_letters WHERE 1=1"
        params: List[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status.upper())
        if campaign_id:
            query += " AND campaign_id = ?"
            params.append(campaign_id)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_record(row) for row in rows]

    def get_stats(self) -> DLQStats:
        """Inspect high-level metrics of dead letter queue."""
        with self._get_connection() as conn:
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
            pending_records=pending,
            replayed_records=replayed,
            discarded_records=discarded,
            failure_breakdown=breakdown,
        )

    def mark_replayed(
        self,
        event_id: str,
        success: bool,
        outcome: str = "connected",
        failure_reason: Optional[str] = None,
    ) -> bool:
        """Mark record as replayed or update failure status."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
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
