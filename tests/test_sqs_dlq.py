import json
import os
import unittest
from unittest.mock import MagicMock, patch

from inference_service.dlq_handler import DLQHandler
from inference_service.schemas import BorrowerBucket, CollectionsCallEvent, ProductType


class TestSQSDLQHandler(unittest.TestCase):
    def setUp(self):
        self.test_queue_url = (
            "https://sqs.ap-south-1.amazonaws.com/123456789012/test-dlq"
        )
        self.test_db_path = "data/test_sqs_dlq.db"

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    @patch("boto3.client")
    def test_record_dead_letter_sqs_delivery(self, mock_boto_client):
        mock_sqs = MagicMock()
        mock_boto_client.return_value = mock_sqs
        mock_sqs.send_message.return_value = {"MessageId": "msg-uuid-999"}

        handler = DLQHandler(
            db_path=self.test_db_path,
            sqs_queue_url=self.test_queue_url,
        )

        event = CollectionsCallEvent(
            event_id="evt-sqs-test-01",
            campaign_id="CAMP-2026-SQS",
            borrower_bucket=BorrowerBucket.DPD_0_30,
            product_type=ProductType.BNPL,
            call_duration_s=45,
        )

        # Trigger DLQ recording
        record = handler.record_dead_letter(
            event=event,
            failure_reason="downstream_503_timeout",
            retries_attempted=3,
        )

        # Verify SQS send_message was called
        mock_sqs.send_message.assert_called_once()
        call_kwargs = mock_sqs.send_message.call_args[1]
        self.assertEqual(call_kwargs["QueueUrl"], self.test_queue_url)

        sent_body = json.loads(call_kwargs["MessageBody"])
        self.assertEqual(sent_body["event_id"], "evt-sqs-test-01")
        self.assertEqual(sent_body["failure_reason"], "downstream_503_timeout")
        self.assertEqual(sent_body["retry_count"], 3)

        # Verify also saved in SQLite for audit compliance
        self.assertEqual(record.event_id, "evt-sqs-test-01")
        self.assertEqual(record.status.value, "PENDING")

    @patch("boto3.client")
    def test_get_stats_with_sqs(self, mock_boto_client):
        mock_sqs = MagicMock()
        mock_boto_client.return_value = mock_sqs
        mock_sqs.get_queue_attributes.return_value = {
            "Attributes": {"ApproximateNumberOfMessages": "14"}
        }

        handler = DLQHandler(
            db_path=self.test_db_path,
            sqs_queue_url=self.test_queue_url,
        )

        stats = handler.get_stats()
        self.assertEqual(stats.pending_records, 14)
        self.assertEqual(stats.sqs_messages_visible, 14)
        self.assertEqual(stats.sqs_queue_url, self.test_queue_url)


if __name__ == "__main__":
    unittest.main()
