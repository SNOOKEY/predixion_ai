# ECS Application Auto Scaling Target
resource "aws_appautoscaling_target" "ecs" {
  max_capacity       = var.max_tasks
  min_capacity       = var.min_tasks
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# 1. Target Tracking Scaling Policy: Concurrent In-Flight Calls / ALB Request Count Per Target
# Ensures the service maintains baseline at 10 calls/min and scales up to 50 calls/min during spikes
resource "aws_appautoscaling_policy" "target_tracking_requests" {
  name               = "predixion-request-count-autoscaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"
    }

    target_value       = 30.0 # Scale out when a single task exceeds 30 req/min
    scale_in_cooldown  = 180  # Smooth scale down after campaign window ends
    scale_out_cooldown = 60   # Rapid scale out during campaign burst
  }
}

# 2. Custom Metric Scaling Alarm (CloudWatch Custom Metric: InFlightCalls / QueueDepth)
resource "aws_cloudwatch_metric_alarm" "high_inflight_calls" {
  alarm_name          = "predixion-high-inflight-calls"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "InFlightCalls"
  namespace           = "CollectionsChallenge"
  period              = 60
  statistic           = "Average"
  threshold           = 25
  alarm_description   = "Scale out when concurrent in-flight voice calls spike above threshold"
  alarm_actions       = [aws_appautoscaling_policy.step_scale_out.arn]
}

resource "aws_appautoscaling_policy" "step_scale_out" {
  name               = "predixion-inflight-step-scale-out"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Average"

    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 20
      scaling_adjustment          = 2
    }

    step_adjustment {
      metric_interval_lower_bound = 20
      scaling_adjustment          = 4
    }
  }
}
