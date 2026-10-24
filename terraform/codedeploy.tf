# IAM Role for AWS CodeDeploy
resource "aws_iam_role" "codedeploy" {
  name = "predixion-codedeploy-ecs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "codedeploy.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "codedeploy" {
  role       = aws_iam_role.codedeploy.name
  policy_arn = "arn:aws:iam::aws:policy/AWSCodeDeployRoleForECS"
}

# CodeDeploy Application for ECS
resource "aws_codedeploy_app" "collections" {
  compute_platform = "ECS"
  name             = "predixion-collections-app"
}

# CloudWatch Alarm for Automated Deployment Rollback
resource "aws_cloudwatch_metric_alarm" "deployment_error_rate" {
  alarm_name          = "predixion-deployment-5xx-alarm"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Triggers instant automated rollback if new Green deployment returns unexpected 5XX errors"

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }
}

# CodeDeploy Blue/Green Deployment Group with Automatic Rollback
resource "aws_codedeploy_deployment_group" "collections" {
  app_name              = aws_codedeploy_app.collections.name
  deployment_group_name = "predixion-collections-bluegreen-dg"
  service_role_arn      = aws_iam_role.codedeploy.arn

  deployment_config_name = "CodeDeployDefault.ECSAllAtOnce"

  blue_green_deployment_config {
    deployment_ready_option {
      action_on_timeout = "CONTINUE_DEPLOYMENT"
    }

    terminate_blue_instances_on_deployment_success {
      action                           = "TERMINATE"
      termination_wait_time_in_minutes = 5
    }
  }

  ecs_service {
    cluster_name = aws_ecs_cluster.main.name
    service_name = aws_ecs_service.app.name
  }

  load_balancer_info {
    target_group_pair_info {
      prod_traffic_route {
        listener_arns = [aws_lb_listener.http.arn]
      }

      test_traffic_route {
        listener_arns = [aws_lb_listener.test.arn]
      }

      target_group {
        name = aws_lb_target_group.blue.name
      }

      target_group {
        name = aws_lb_target_group.green.name
      }
    }
  }

  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM"]
  }

  alarm_configuration {
    alarms  = [aws_cloudwatch_metric_alarm.deployment_error_rate.alarm_name]
    enabled = true
  }

  depends_on = [
    aws_lb_listener.http,
    aws_lb_listener.test
  ]
}
