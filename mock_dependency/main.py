import asyncio
import os
import random
from typing import Optional

from fastapi import FastAPI, Response, status
from pydantic import BaseModel, Field
import uvicorn

app = FastAPI(
    title="Mock STT/LLM/TTS Pipeline Service",
    description="Simulates downstream speech/inference pipeline with realistic lognormal latency and injected failures.",
    version="1.0.0",
)


class PipelineProcessRequest(BaseModel):
    event_id: str = Field(..., description="Unique UUID of the call event")
    audio_ref: str = Field(default="synthetic", description="Reference to audio input payload")


class PipelineSuccessResponse(BaseModel):
    status: str = "ok"
    transcript: str = "synthetic transcript"
    response_audio_ref: str = "synthetic"
    latency_ms: float


class PipelineErrorResponse(BaseModel):
    status: str = "error"
    reason: str = "downstream_timeout"


# Default failure rate is 20% per Appendix B
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.20"))


@app.get("/health", tags=["System"])
async def health_check():
    """Health check endpoint for Docker / ALB / container monitoring."""
    return {"status": "healthy", "service": "mock-pipeline-dependency"}


@app.post(
    "/pipeline/process",
    response_model=PipelineSuccessResponse,
    responses={
        503: {
            "model": PipelineErrorResponse,
            "description": "Downstream timeout or transient dependency failure",
        }
    },
    tags=["Pipeline"],
)
async def process_pipeline(
    payload: PipelineProcessRequest,
    response: Response,
    force_failure: Optional[bool] = None,
):
    """
    Simulates STT -> LLM -> TTS pipeline call:
    - 20% independent failure rate returning HTTP 503.
    - Lognormal latency targeting p50 ~ 300ms, p90 ~ 700ms, p99 ~ 1200ms.
    """
    # 1. Failure injection check (20% probability or explicit query override for tests)
    should_fail = (
        force_failure
        if force_failure is not None
        else (random.random() < FAILURE_RATE)
    )

    if should_fail:
        # Simulate small network drop or immediate timeout before failing
        await asyncio.sleep(0.05)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return PipelineErrorResponse(status="error", reason="downstream_timeout")

    # 2. Simulate realistic pipeline latency (Appendix B: mu=5.7, sigma=0.35 in ms)
    # Target distribution: p50 ~ 300ms, p90 ~ 700ms, p99 ~ 1200ms
    latency_ms = random.lognormvariate(mu=5.7, sigma=0.35)

    # Non-blocking async sleep to simulate remote I/O processing
    await asyncio.sleep(latency_ms / 1000.0)

    return PipelineSuccessResponse(
        status="ok",
        transcript="synthetic transcript",
        response_audio_ref="synthetic",
        latency_ms=round(latency_ms, 2),
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("mock_dependency.main:app", host="0.0.0.0", port=port, reload=True)
