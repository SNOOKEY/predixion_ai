resource "aws_sqs_queue" "dlq" {
  name                      = "collections-dlq"
  message_retention_seconds = 1209600 # 14 days maximum retention for compliance audits
  visibility_timeout_seconds = 60
  receive_wait_time_seconds = 10      # Long polling

  tags = {
    Name        = "collections-dlq"
    Compliance  = "RBI-Collections-Audit"
  }
}
