import asyncio
import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger("inference_service.pipeline_client")


class CircuitBreakerState(str, Enum):
    CLOSED = "CLOSED"          # Normal operation; downstream receiving requests
    OPEN = "OPEN"              # Downstream failing; fast-failing incoming requests
    HALF_OPEN = "HALF_OPEN"    # Probing downstream with limited trial requests


class PipelineException(Exception):
    """Base exception for pipeline communication issues."""


class DownstreamServiceError(PipelineException):
    """Raised when downstream explicitly returns 5xx or transient failure."""
    def __init__(self, message: str, status_code: int = 503, reason: str = "downstream_timeout"):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class CircuitBreakerOpenError(PipelineException):
    """Raised when request is fast-failed because circuit breaker is in OPEN state."""


class RetriesExhaustedError(PipelineException):
    """Raised when all retry attempts fail."""
    def __init__(self, message: str, attempts: int, last_error: Exception):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


@dataclass
class PipelineResult:
    status: str
    transcript: str
    response_audio_ref: str
    latency_ms: float
    error_reason: str | None = None


class CircuitBreaker:
    """
    Thread-safe / async Circuit Breaker implementation:
    - CLOSED: Normal execution. Consecutive failures > failure_threshold triggers OPEN.
    - OPEN: Rejects calls immediately without network overhead. After recovery_timeout, moves to HALF_OPEN.
    - HALF_OPEN: Allows trial requests. If half_open_success_threshold successes occur, resets to CLOSED.
      Any failure in HALF_OPEN immediately trips back to OPEN.
    """
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 10.0,
        half_open_success_threshold: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold

        self._state = CircuitBreakerState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_state_change = time.time()
        self._trips_count = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitBreakerState:
        # Check for automatic transition from OPEN to HALF_OPEN upon timeout expiry
        if (
            self._state == CircuitBreakerState.OPEN
            and time.time() - self._last_state_change >= self.recovery_timeout
        ):
            self._state = CircuitBreakerState.HALF_OPEN
            self._consecutive_successes = 0
            self._last_state_change = time.time()
            logger.info("Circuit Breaker transitioned to HALF_OPEN (probing downstream)")
        return self._state

    async def can_execute(self) -> bool:
        async with self._lock:
            return self.state != CircuitBreakerState.OPEN

    async def record_success(self):
        async with self._lock:
            current_state = self.state
            if current_state == CircuitBreakerState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.half_open_success_threshold:
                    self._state = CircuitBreakerState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._last_state_change = time.time()
                    logger.info("Circuit Breaker transitioned back to CLOSED (recovered)")
            elif current_state == CircuitBreakerState.CLOSED:
                self._consecutive_failures = 0

    async def record_failure(self):
        async with self._lock:
            current_state = self.state
            self._consecutive_failures += 1
            if current_state == CircuitBreakerState.HALF_OPEN or self._consecutive_failures >= self.failure_threshold:
                self._state = CircuitBreakerState.OPEN
                self._last_state_change = time.time()
                self._trips_count += 1
                self._consecutive_successes = 0
                logger.warning(
                    f"Circuit Breaker tripped to OPEN! Consecutive failures: {self._consecutive_failures}, total trips: {self._trips_count}"
                )

    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "consecutive_failures": self._consecutive_failures,
            "consecutive_successes": self._consecutive_successes,
            "total_trips": self._trips_count,
            "recovery_timeout_s": self.recovery_timeout,
            "failure_threshold": self.failure_threshold,
            "seconds_since_state_change": round(time.time() - self._last_state_change, 2),
        }

    async def reset(self):
        async with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._last_state_change = time.time()


# =====================================================================
# Model-Agnostic Dependency Boundary (Appendix B / Task 3)
# =====================================================================
class BasePipelineClient(ABC):
    """
    Abstract contract for speech & inference pipeline.
    Any concrete implementation (mock HTTP, Whisper + Llama + Piper, etc.)
    plugs into the resilience layer seamlessly.
    """
    @abstractmethod
    async def process_call(self, event_id: str, audio_ref: str = "synthetic") -> PipelineResult:
        """Process STT -> LLM -> TTS inference for given call event."""


class HttpMockPipelineClient(BasePipelineClient):
    """
    Concrete client calling the mock HTTP service specified in Appendix B.
    """
    def __init__(self, base_url: str | None = None, timeout_s: float = 5.0):
        self.base_url = (base_url or os.getenv("MOCK_PIPELINE_URL", "http://localhost:8001")).rstrip("/")
        self.timeout_s = timeout_s

    async def process_call(self, event_id: str, audio_ref: str = "synthetic") -> PipelineResult:
        endpoint = f"{self.base_url}/pipeline/process"
        payload = {"event_id": event_id, "audio_ref": audio_ref}

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            try:
                response = await client.post(endpoint, json=payload)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise DownstreamServiceError(
                    message=f"Connection failure to downstream: {exc}",
                    status_code=503,
                    reason="connection_error"
                ) from exc

        if response.status_code == 200:
            data = response.json()
            return PipelineResult(
                status=data.get("status", "ok"),
                transcript=data.get("transcript", ""),
                response_audio_ref=data.get("response_audio_ref", ""),
                latency_ms=data.get("latency_ms", 0.0),
            )
        elif response.status_code == 503:
            err_data = response.json() if response.headers.get("content-type") == "application/json" else {}
            reason = err_data.get("reason", "downstream_timeout")
            raise DownstreamServiceError(
                message=f"Downstream service returned 503: {reason}",
                status_code=503,
                reason=reason,
            )
        else:
            raise DownstreamServiceError(
                message=f"Unexpected downstream response HTTP {response.status_code}",
                status_code=response.status_code,
                reason="unexpected_status",
            )


class OpenWeightPipelineClientStub(BasePipelineClient):
    """
    Reference production adapter demonstrating how local open-weight models
    (faster-whisper, Mistral-7B / Llama-3.1, Piper / XTTS-v2) swap in
    without touching resilience logic.
    """
    async def process_call(self, event_id: str, audio_ref: str = "synthetic") -> PipelineResult:
        # In production on AWS GPU (g5.xlarge):
        # 1. STT: faster_whisper_model.transcribe(audio_ref) -> transcript
        # 2. LLM: vllm_engine.generate(transcript, prompt_template) -> agent_reply
        # 3. TTS: piper_synthesizer.synthesize(agent_reply) -> output_audio
        raise NotImplementedError("Use HttpMockPipelineClient for Round 1 local track.")


# =====================================================================
# Resilience Layer: Retries with Exponential Backoff + Circuit Breaker
# =====================================================================
class ResilientPipelineClient(BasePipelineClient):
    """
    Resilience decorator wrapping any BasePipelineClient:
    - Evaluates Circuit Breaker before executing.
    - Implements retries with exponential backoff and randomized jitter:
        delay = min(max_delay, base_delay * (backoff_factor ** attempt)) + uniform(0, jitter)
    - Records failures/successes to dynamically adjust circuit breaker state.
    """
    def __init__(
        self,
        underlying_client: BasePipelineClient,
        circuit_breaker: CircuitBreaker | None = None,
        max_retries: int = 3,
        base_delay_s: float = 0.2,
        max_delay_s: float = 2.0,
        backoff_factor: float = 2.0,
        jitter_s: float = 0.1,
    ):
        self.client = underlying_client
        self.cb = circuit_breaker or CircuitBreaker()
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.backoff_factor = backoff_factor
        self.jitter_s = jitter_s

    async def process_call(self, event_id: str, audio_ref: str = "synthetic") -> PipelineResult:
        # 1. Circuit breaker gate
        if not await self.cb.can_execute():
            raise CircuitBreakerOpenError(
                f"Circuit breaker is OPEN. Downstream service unavailable. Fast-failing event {event_id}."
            )

        last_exception: Exception | None = None
        attempt = 0

        while attempt <= self.max_retries:
            try:
                result = await self.client.process_call(event_id, audio_ref)
                # Successful call
                await self.cb.record_success()
                return result

            except (DownstreamServiceError, httpx.RequestError) as exc:
                last_exception = exc
                await self.cb.record_failure()
                logger.warning(
                    f"Call {event_id} attempt {attempt + 1}/{self.max_retries + 1} failed: {exc}"
                )

                # Check if circuit tripped during retry cycle
                if self.cb.state == CircuitBreakerState.OPEN:
                    logger.warning(f"Circuit breaker tripped open during retries for {event_id}. Fast-exiting.")
                    break

                if attempt < self.max_retries:
                    # Exponential backoff with full jitter to avoid stampede
                    calculated_delay = self.base_delay_s * (self.backoff_factor ** attempt)
                    capped_delay = min(self.max_delay_s, calculated_delay)
                    jittered_delay = capped_delay + random.uniform(0, self.jitter_s)
                    await asyncio.sleep(jittered_delay)

                attempt += 1

        raise RetriesExhaustedError(
            message=f"All {self.max_retries + 1} attempts exhausted for event {event_id}.",
            attempts=attempt,
            last_error=last_exception or Exception("Unknown downstream error"),
        )
