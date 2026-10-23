import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from inference_service.main import app, dlq_handler, resilient_client
from inference_service.pipeline_client import PipelineResult, RetriesExhaustedError


class TestAPIEndpoints(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = os.path.join(self.tmp_dir.name, "test_dlq.db")
        
        # Point DLQHandler to a temporary isolated DB for tests
        self.original_db_path = dlq_handler.db_path
        dlq_handler.db_path = self.test_db_path
        dlq_handler._init_db()

        self.client = TestClient(app)

    def tearDown(self):
        dlq_handler.db_path = self.original_db_path
        self.tmp_dir.cleanup()

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("circuit_breaker_state", data)

    def test_metrics_endpoint(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("active_instance_session", data)
        self.assertIn("circuit_breaker", data)
        self.assertIn("dlq", data)

    def test_call_process_success(self):
        sample_event = {
            "event": {
                "event_id": "test-call-101",
                "campaign_id": "CAMP-2026-09-A",
                "borrower_bucket": "0-30 DPD",
                "product_type": "bnpl",
                "outcome": "connected",
                "call_duration_s": 45,
                "retry_count": 0,
            },
            "audio_ref": "synthetic",
        }

        mock_result = PipelineResult(
            status="ok",
            transcript="Hello, this is a test call.",
            response_audio_ref="synthetic_audio",
            latency_ms=250.0,
            retries_performed=0,
        )

        with patch.object(resilient_client, "process_call", new=AsyncMock(return_value=mock_result)):
            resp = self.client.post("/calls/process", json=sample_event)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["event_id"], "test-call-101")
            self.assertEqual(data["retries_attempted"], 0)

    def test_call_process_degrades_to_dlq_on_downstream_failure(self):
        sample_event = {
            "event": {
                "event_id": "test-call-failed-102",
                "campaign_id": "CAMP-2026-09-A",
                "borrower_bucket": "31-60 DPD",
                "product_type": "credit_card",
                "outcome": "connected",
                "call_duration_s": 0,
                "retry_count": 0,
            },
            "audio_ref": "synthetic",
        }

        exhausted_err = RetriesExhaustedError(
            message="Downstream failed after retries",
            attempts=2,
            last_error=Exception("503 Service Unavailable"),
        )

        with patch.object(resilient_client, "process_call", new=AsyncMock(side_effect=exhausted_err)):
            resp = self.client.post("/calls/process", json=sample_event)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            # Verified: must degrade gracefully and record to DLQ without dropping
            self.assertEqual(data["status"], "degraded_dlq")
            self.assertEqual(data["outcome"], "failed_downstream")
            self.assertEqual(data["retries_attempted"], 2)

            # Confirm event landed in DLQ
            dlq_records = dlq_handler.list_records(status="PENDING")
            self.assertEqual(len(dlq_records), 1)
            self.assertEqual(dlq_records[0].event_id, "test-call-failed-102")

    def test_circuit_breaker_status_and_reset(self):
        resp = self.client.get("/circuit-breaker/status")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("state", resp.json())

        reset_resp = self.client.post("/circuit-breaker/reset")
        self.assertEqual(reset_resp.status_code, 200)
        self.assertEqual(reset_resp.json()["circuit_breaker_state"], "CLOSED")


if __name__ == "__main__":
    unittest.main()
