import asyncio
import gc
import os
import tempfile
import unittest

from inference_service.dlq_handler import DLQHandler
from inference_service.pipeline_client import (
    BasePipelineClient,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitBreakerState,
    DownstreamServiceError,
    PipelineResult,
    ResilientPipelineClient,
    RetriesExhaustedError,
)
from inference_service.schemas import BorrowerBucket, CallOutcome, CollectionsCallEvent, ProductType


class MockFlakyClient(BasePipelineClient):
    """Simulates a downstream client with controllable failure counts."""
    def __init__(self, fail_first_n_times: int = 0):
        self.fail_first_n_times = fail_first_n_times
        self.call_count = 0

    async def process_call(self, event_id: str, audio_ref: str = "synthetic") -> PipelineResult:
        self.call_count += 1
        if self.call_count <= self.fail_first_n_times:
            raise DownstreamServiceError(
                message="Injected downstream 503 failure",
                status_code=503,
                reason="downstream_timeout"
            )
        return PipelineResult(
            status="ok",
            transcript="test transcript",
            response_audio_ref="test_audio",
            latency_ms=150.0,
        )


class TestResilienceLayer(unittest.IsolatedAsyncioTestCase):

    async def test_retry_success_after_transient_failures(self):
        """Verify that transient 503 errors trigger retries with backoff and succeed."""
        flaky = MockFlakyClient(fail_first_n_times=2)
        cb = CircuitBreaker(failure_threshold=5)
        resilient = ResilientPipelineClient(
            underlying_client=flaky,
            circuit_breaker=cb,
            max_retries=3,
            base_delay_s=0.01,
            max_delay_s=0.05,
            jitter_s=0.01,
        )

        result = await resilient.process_call("evt-001")
        self.assertEqual(result.status, "ok")
        self.assertEqual(flaky.call_count, 3)
        self.assertEqual(cb.state, CircuitBreakerState.CLOSED)

    async def test_retry_exhaustion(self):
        """Verify that persistent downstream failures exhaust retries and raise RetriesExhaustedError."""
        flaky = MockFlakyClient(fail_first_n_times=10)
        cb = CircuitBreaker(failure_threshold=10)
        resilient = ResilientPipelineClient(
            underlying_client=flaky,
            circuit_breaker=cb,
            max_retries=2,
            base_delay_s=0.01,
            max_delay_s=0.05,
        )

        with self.assertRaises(RetriesExhaustedError) as ctx:
            await resilient.process_call("evt-002")

        # Initial attempt + 2 retries = 3 attempts total
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertEqual(flaky.call_count, 3)

    async def test_circuit_breaker_tripping_and_fast_fail(self):
        """Verify circuit breaker trips from CLOSED to OPEN after failure_threshold is reached, then fast-fails."""
        flaky = MockFlakyClient(fail_first_n_times=20)
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
        resilient = ResilientPipelineClient(
            underlying_client=flaky,
            circuit_breaker=cb,
            max_retries=0,  # 1 attempt per call
        )

        # 3 consecutive failed calls trip breaker
        for i in range(3):
            with self.assertRaises(RetriesExhaustedError):
                await resilient.process_call(f"evt-fail-{i}")

        self.assertEqual(cb.state, CircuitBreakerState.OPEN)
        initial_flaky_calls = flaky.call_count

        # 4th call should fast-fail without touching downstream
        with self.assertRaises(CircuitBreakerOpenError):
            await resilient.process_call("evt-fast-fail")

        self.assertEqual(flaky.call_count, initial_flaky_calls)  # No network call wasted!

    async def test_circuit_breaker_recovery_to_closed(self):
        """Verify transition from OPEN -> HALF_OPEN -> CLOSED upon cooldown and trial success."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1, half_open_success_threshold=2)

        await cb.record_failure()
        await cb.record_failure()
        self.assertEqual(cb.state, CircuitBreakerState.OPEN)

        # Await recovery timeout
        await asyncio.sleep(0.12)
        self.assertEqual(cb.state, CircuitBreakerState.HALF_OPEN)

        # Two successful trials heal the circuit back to CLOSED
        await cb.record_success()
        self.assertEqual(cb.state, CircuitBreakerState.HALF_OPEN)
        await cb.record_success()
        self.assertEqual(cb.state, CircuitBreakerState.CLOSED)

    def test_dlq_persistence_and_replay(self):
        """Verify that failed calls persist into SQLite DLQ and can be inspected and marked replayed."""
        tmp_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(tmp_fd)

        try:
            handler = DLQHandler(db_path=db_path)
            event = CollectionsCallEvent(
                event_id="test-dlq-1",
                campaign_id="CAMP-TEST",
                borrower_bucket=BorrowerBucket.DPD_0_30,
                product_type=ProductType.BNPL,
                outcome=CallOutcome.FAILED_DOWNSTREAM,
                call_duration_s=0,
                retry_count=3,
            )

            # Store in DLQ
            record = handler.record_dead_letter(
                event=event,
                failure_reason="downstream_timeout",
                retries_attempted=3,
            )

            self.assertEqual(record.event_id, "test-dlq-1")
            self.assertEqual(record.status.value, "PENDING")

            # Inspect DLQ
            records = handler.list_records(status="PENDING")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].campaign_id, "CAMP-TEST")

            stats = handler.get_stats()
            self.assertEqual(stats.total_records, 1)
            self.assertEqual(stats.pending_records, 1)

            # Replay DLQ item
            handler.mark_replayed("test-dlq-1", success=True, outcome="connected")
            updated = handler.get_record("test-dlq-1")
            self.assertEqual(updated.status.value, "REPLAYED")
            self.assertEqual(updated.replay_outcome, "connected")

        finally:
            del handler
            gc.collect()
            if os.path.exists(db_path):
                try:
                    os.remove(db_path)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
