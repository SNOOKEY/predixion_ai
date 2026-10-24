# ALB Security Group
resource "aws_security_group" "alb" {
  name        = "predixion-alb-sg"
  description = "Controls ingress traffic to the Application Load Balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP Inbound"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description     = "Outbound to ECS tasks only"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    security_groups = [] # Linked via self or task sg rule
    cidr_blocks     = [var.vpc_cidr]
  }

  tags = {
    Name = "predixion-alb-sg"
  }
}

# ECS Tasks Security Group
resource "aws_security_group" "ecs_tasks" {
  name        = "predixion-ecs-tasks-sg"
  description = "Allows inbound traffic only from the ALB to ECS tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Inbound from ALB"
    from_port       = var.app_port
    to_port         = var.app_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Outbound to internet via NAT (for image pull, telemetry, SQS)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "predixion-ecs-tasks-sg"
  }
}
