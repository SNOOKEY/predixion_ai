resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/collections-inference"
  retention_in_days = 7

  tags = {
    Name = "collections-inference-logs"
  }
}

resource "aws_ecs_cluster" "main" {
  name = "predixion-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "predixion-ecs-cluster"
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "collections-inference"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.fargate_cpu)
  memory                   = tostring(var.fargate_memory)
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "inference-service"
      image     = var.app_image
      essential = true
      command   = ["uvicorn", "inference_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
      portMappings = [
        {
          containerPort = var.app_port
          hostPort      = var.app_port
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ENVIRONMENT", value = var.environment },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "MOCK_PIPELINE_URL", value = "http://localhost:8001" },
        { name = "SQS_DLQ_URL", value = aws_sqs_queue.dlq.url },
        { name = "CLOUDWATCH_NAMESPACE", value = "CollectionsChallenge" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "inference"
        }
      }
    },
    {
      name      = "mock-dependency"
      image     = var.app_image
      essential = true
      command   = ["uvicorn", "mock_dependency.main:app", "--host", "0.0.0.0", "--port", "8001"]
      portMappings = [
        {
          containerPort = 8001
          hostPort      = 8001
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "FAILURE_RATE", value = "0.20" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "dependency"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name            = "collections-inference-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.min_tasks
  launch_type     = "FARGATE"

  network_configuration {
    security_groups  = [aws_security_group.ecs_tasks.id]
    subnets          = aws_subnet.private[*].id
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "inference-service"
    container_port   = var.app_port
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]
}
