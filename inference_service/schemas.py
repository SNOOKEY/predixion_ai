from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class BorrowerBucket(str, Enum):
    DPD_0_30 = "0-30 DPD"
    DPD_31_60 = "31-60 DPD"
    DPD_61_90 = "61-90 DPD"
    DPD_90_PLUS = "90+ DPD"


class ProductType(str, Enum):
    CREDIT_CARD = "credit_card"
    BNPL = "bnpl"
    PERSONAL_LOAN = "personal_loan"


class CallOutcome(str, Enum):
    CONNECTED = "connected"
    NO_ANSWER = "no_answer"
    PTP_TAKEN = "ptp_taken"
    DISPUTE = "dispute"
    FAILED_DOWNSTREAM = "failed_downstream"


class CollectionsCallEvent(BaseModel):
    """
    Appendix A: Schema representing a collections call event.
    """
    event_id: str = Field(..., description="Unique UUID for the call event")
    campaign_id: str = Field(..., description="Campaign identifier, e.g., CAMP-2026-09-A")
    borrower_bucket: BorrowerBucket = Field(..., description="Days past due bucket")
    product_type: ProductType = Field(..., description="Financial product category")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="ISO-8601 formatted timestamp of the event",
    )
    call_duration_s: int = Field(default=0, ge=0, description="Duration of call in seconds")
    outcome: CallOutcome = Field(default=CallOutcome.CONNECTED, description="Call disposition outcome")
    retry_count: int = Field(default=0, ge=0, description="Total resilience retries attempted")


class CallProcessRequest(BaseModel):
    """
    Inference service request payload wrapping the call event and audio reference.
    """
    event: CollectionsCallEvent
    audio_ref: str = Field(default="synthetic", description="Audio payload or reference pointer")


class CallProcessResponse(BaseModel):
    """
    Response returned by inference service after resilient pipeline processing.
    """
    event_id: str
    status: Literal["success", "degraded_dlq"]
    outcome: CallOutcome
    retries_attempted: int
    circuit_breaker_state: str
    transcript: Optional[str] = None
    response_audio_ref: Optional[str] = None
    total_latency_ms: float
    error_reason: Optional[str] = None


class DLQStatus(str, Enum):
    PENDING = "PENDING"
    REPLAYED = "REPLAYED"
    DISCARDED = "DISCARDED"


class DLQRecord(BaseModel):
    event_id: str
    campaign_id: str
    borrower_bucket: str
    product_type: str
    timestamp: str
    call_duration_s: int
    outcome: str
    retry_count: int
    failure_reason: str
    created_at: str
    status: DLQStatus
    replayed_at: Optional[str] = None
    replay_outcome: Optional[str] = None
    payload: Dict[str, Any]


class DLQStats(BaseModel):
    total_records: int
    pending_records: int
    replayed_records: int
    discarded_records: int
    failure_breakdown: Dict[str, int]


class ReplayResult(BaseModel):
    total_attempted: int
    successful_replays: int
    failed_replays: int
    replayed_events: List[Dict[str, Any]]
