output "alb_dns_name" {
  description = "Public DNS hostname of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS Cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Name of the ECS Service"
  value       = aws_ecs_service.app.name
}

output "sqs_dlq_url" {
  description = "URL of the SQS Dead-Letter Queue"
  value       = aws_sqs_queue.dlq.url
}

output "sqs_dlq_arn" {
  description = "ARN of the SQS Dead-Letter Queue"
  value       = aws_sqs_queue.dlq.arn
}

output "cloudwatch_dashboard_url" {
  description = "Direct AWS Console URL to the Observability Dashboard"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "alb_test_listener_url" {
  description = "Test listener URL on Port 8080 for Green deployment validation"
  value       = "http://${aws_lb.main.dns_name}:8080"
}

output "codedeploy_app_name" {
  description = "CodeDeploy Application Name"
  value       = aws_codedeploy_app.collections.name
}

output "codedeploy_deployment_group_name" {
  description = "CodeDeploy Blue/Green Deployment Group"
  value       = aws_codedeploy_deployment_group.collections.deployment_group_name
}
