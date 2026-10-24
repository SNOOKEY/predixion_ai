resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "CollectionsAgent-Observability"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "ALB Request Rate (Calls/min)"
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix, { "label": "Total Requests" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Downstream Error Rate (HTTP 5XX vs Injected 20%)"
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { "color": "#d62728", "label": "5XX Errors" }],
            ["AWS/ApplicationELB", "HTTPCode_Target_2XX_Count", "LoadBalancer", aws_lb.main.arn_suffix, { "color": "#2ca02c", "label": "2XX Successes" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Latency Percentiles (TargetResponseTime: p50, p90, p99)"
          period = 60
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, { "stat": "p50", "color": "#1f77b4", "label": "p50 Latency (Target: ~300ms)" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, { "stat": "p90", "color": "#ff7f0e", "label": "p90 Latency (Target: ~700ms)" }],
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, { "stat": "p99", "color": "#d62728", "label": "p99 Latency (Target: ~1200ms)" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Autoscaling & Task Count (Desired vs Running Tasks)"
          stat   = "Average"
          period = 60
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ServiceName", aws_ecs_service.app.name, "ClusterName", aws_ecs_cluster.main.name, { "color": "#2ca02c", "label": "Running Tasks" }],
            ["ECS/ContainerInsights", "DesiredTaskCount", "ServiceName", aws_ecs_service.app.name, "ClusterName", aws_ecs_cluster.main.name, { "color": "#1f77b4", "label": "Desired Tasks" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "Custom Metric: In-Flight Calls"
          stat   = "Average"
          period = 60
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["CollectionsChallenge", "InFlightCalls", { "color": "#9467bd", "label": "Active Concurrent Calls" }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "Dead-Letter Queue (DLQ) Depth (Zero Silent Dropped Calls)"
          stat   = "Sum"
          period = 60
          view   = "timeSeries"
          region = var.aws_region
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.dlq.name, { "color": "#8c564b", "label": "Messages in DLQ" }]
          ]
        }
      }
    ]
  })
}
