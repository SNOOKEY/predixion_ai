import logging
import os
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, status
import uvicorn

from inference_service.dlq_handler import DLQHandler
from inference_service.pipeline_client import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    HttpMockPipelineClient,
    ResilientPipelineClient,
    RetriesExhaustedError,
)
from inference_service.schemas import (
    CallOutcome,
    CallProcessRequest,
    CallProcessResponse,
    CollectionsCallEvent,
    DLQRecord,
    DLQStats,
    ReplayResult,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("inference_service")

app = FastAPI(
    title="Collections Voice-Agent Resilient Inference Service",
    description="Production-grade collections voice inference with circuit breaking, retries, and dead-letter handling.",
    version="1.0.0",
)

# Global shared resilience and DLQ components
circuit_breaker = CircuitBreaker(
    failure_threshold=int(os.getenv("CB_FAILURE_THRESHOLD", "5")),
    recovery_timeout=float(os.getenv("CB_RECOVERY_TIMEOUT_S", "10.0")),
    half_open_success_threshold=int(os.getenv("CB_HALF_OPEN_SUCCESS", "2")),
)

mock_url = os.getenv("MOCK_PIPELINE_URL", "http://localhost:8001")
base_client = HttpMockPipelineClient(base_url=mock_url, timeout_s=5.0)

resilient_client = ResilientPipelineClient(
    underlying_client=base_client,
    circuit_breaker=circuit_breaker,
    max_retries=int(os.getenv("MAX_RETRIES", "3")),
    base_delay_s=float(os.getenv("RETRY_BASE_DELAY_S", "0.2")),
    max_delay_s=float(os.getenv("RETRY_MAX_DELAY_S", "2.0")),
    backoff_factor=float(os.getenv("RETRY_BACKOFF_FACTOR", "2.0")),
    jitter_s=0.1,
)

dlq_handler = DLQHandler()

# Metrics counters
metrics_counter = {
    "total_calls": 0,
    "successful_calls": 0,
    "degraded_calls": 0,
    "retried_calls": 0,
}


@app.get("/health", tags=["Monitoring"])
async def health():
    return {
        "status": "healthy",
        "service": "collections-inference-service",
        "circuit_breaker_state": circuit_breaker.state.value,
    }


@app.get("/metrics", tags=["Monitoring"])
async def metrics():
    stats = dlq_handler.get_stats()
    return {
        "calls": metrics_counter,
        "circuit_breaker": circuit_breaker.get_status(),
        "dlq": stats.model_dump(),
    }


@app.post(
    "/calls/process",
    response_model=CallProcessResponse,
    status_code=status.HTTP_200_OK,
    tags=["Calls"],
)
async def process_call_event(request: CallProcessRequest):
    """
    Ingests a collections call event, routes through resilient STT/LLM/TTS pipeline.
    On downstream failure or circuit trip, degrades gracefully and routes to DLQ for zero call drops.
    """
    start_time = time.time()
    event = request.event
    audio_ref = request.audio_ref
    metrics_counter["total_calls"] += 1

    try:
        pipeline_res = await resilient_client.process_call(
            event_id=event.event_id, audio_ref=audio_ref
        )
        total_latency = (time.time() - start_time) * 1000.0
        metrics_counter["successful_calls"] += 1

        return CallProcessResponse(
            event_id=event.event_id,
            status="success",
            outcome=event.outcome,
            retries_attempted=0,
            circuit_breaker_state=circuit_breaker.state.value,
            transcript=pipeline_res.transcript,
            response_audio_ref=pipeline_res.response_audio_ref,
            total_latency_ms=round(total_latency, 2),
        )

    except CircuitBreakerOpenError as cb_err:
        total_latency = (time.time() - start_time) * 1000.0
        metrics_counter["degraded_calls"] += 1

        # Route immediately to DLQ
        event.outcome = CallOutcome.FAILED_DOWNSTREAM
        dlq_handler.record_dead_letter(
            event=event,
            failure_reason="circuit_breaker_open",
            retries_attempted=0,
            raw_payload=request.model_dump(mode="json"),
        )

        return CallProcessResponse(
            event_id=event.event_id,
            status="degraded_dlq",
            outcome=CallOutcome.FAILED_DOWNSTREAM,
            retries_attempted=0,
            circuit_breaker_state=circuit_breaker.state.value,
            total_latency_ms=round(total_latency, 2),
            error_reason=str(cb_err),
        )

    except RetriesExhaustedError as rx_err:
        total_latency = (time.time() - start_time) * 1000.0
        metrics_counter["degraded_calls"] += 1
        metrics_counter["retried_calls"] += rx_err.attempts

        # Route to DLQ after retries exhausted
        event.outcome = CallOutcome.FAILED_DOWNSTREAM
        event.retry_count = rx_err.attempts
        dlq_handler.record_dead_letter(
            event=event,
            failure_reason=str(rx_err.last_error),
            retries_attempted=rx_err.attempts,
            raw_payload=request.model_dump(mode="json"),
        )

        return CallProcessResponse(
            event_id=event.event_id,
            status="degraded_dlq",
            outcome=CallOutcome.FAILED_DOWNSTREAM,
            retries_attempted=rx_err.attempts,
            circuit_breaker_state=circuit_breaker.state.value,
            total_latency_ms=round(total_latency, 2),
            error_reason=str(rx_err),
        )


# =====================================================================
# Dead-Letter Queue (DLQ) Inspection & Replay (Task 2)
# =====================================================================
@app.get("/dlq", response_model=List[DLQRecord], tags=["DLQ"])
async def list_dead_letters(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, REPLAYED, DISCARDED"),
    campaign_id: Optional[str] = Query(None, description="Filter by campaign ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Inspect dead letters stored in the SQLite persistence layer."""
    return dlq_handler.list_records(
        status=status, campaign_id=campaign_id, limit=limit, offset=offset
    )


@app.get("/dlq/stats", response_model=DLQStats, tags=["DLQ"])
async def get_dlq_stats():
    """Retrieve DLQ statistics and failure reasons breakdown."""
    return dlq_handler.get_stats()


@app.post("/dlq/replay", response_model=ReplayResult, tags=["DLQ"])
async def replay_dead_letters(
    campaign_id: Optional[str] = Query(None, description="Replay pending events for specific campaign"),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Replays pending dead letter items through the resilient pipeline.
    If recovered, marks item as REPLAYED in compliance store.
    """
    pending_records = dlq_handler.list_records(
        status="PENDING", campaign_id=campaign_id, limit=limit, offset=0
    )

    successful = 0
    failed = 0
    replayed_details = []

    for record in pending_records:
        try:
            audio_ref = record.payload.get("audio_ref", "synthetic")
            res = await resilient_client.process_call(
                event_id=record.event_id, audio_ref=audio_ref
            )
            dlq_handler.mark_replayed(
                event_id=record.event_id,
                success=True,
                outcome=record.outcome or "connected",
            )
            successful += 1
            replayed_details.append(
                {"event_id": record.event_id, "status": "replayed", "latency_ms": res.latency_ms}
            )
        except Exception as exc:
            dlq_handler.mark_replayed(
                event_id=record.event_id,
                success=False,
                failure_reason=str(exc),
            )
            failed += 1
            replayed_details.append(
                {"event_id": record.event_id, "status": "failed_again", "error": str(exc)}
            )

    return ReplayResult(
        total_attempted=len(pending_records),
        successful_replays=successful,
        failed_replays=failed,
        replayed_events=replayed_details,
    )


@app.post("/dlq/replay/{event_id}", tags=["DLQ"])
async def replay_single_dead_letter(event_id: str):
    """Replay a specific dead letter entry by event ID."""
    record = dlq_handler.get_record(event_id)
    if not record:
        raise HTTPException(status_code=404, detail="Dead letter event not found")

    try:
        audio_ref = record.payload.get("audio_ref", "synthetic")
        res = await resilient_client.process_call(
            event_id=record.event_id, audio_ref=audio_ref
        )
        dlq_handler.mark_replayed(
            event_id=record.event_id,
            success=True,
            outcome=record.outcome or "connected",
        )
        return {
            "event_id": event_id,
            "status": "replayed",
            "latency_ms": res.latency_ms,
        }
    except Exception as exc:
        dlq_handler.mark_replayed(
            event_id=record.event_id,
            success=False,
            failure_reason=str(exc),
        )
        return {
            "event_id": event_id,
            "status": "failed_retry",
            "error": str(exc),
        }


# =====================================================================
# Circuit Breaker Admin Endpoints
# =====================================================================
@app.get("/circuit-breaker/status", tags=["Circuit Breaker"])
async def get_circuit_breaker_status():
    """Inspect real-time circuit breaker state, trip count, and failure tallies."""
    return circuit_breaker.get_status()


@app.post("/circuit-breaker/reset", tags=["Circuit Breaker"])
async def reset_circuit_breaker():
    """Manually reset the circuit breaker back to CLOSED state."""
    await circuit_breaker.reset()
    return {"status": "reset", "circuit_breaker_state": circuit_breaker.state.value}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("inference_service.main:app", host="0.0.0.0", port=port, reload=True)
