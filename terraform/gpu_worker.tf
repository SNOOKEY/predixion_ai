# Security Group for GPU Worker (Port 8001 open only to ECS Tasks)
resource "aws_security_group" "gpu_worker" {
  count       = var.enable_gpu_worker ? 1 : 0
  name        = "predixion-gpu-worker-sg"
  description = "Security group for g5.xlarge open-weight model inference server"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Inbound model inference from ECS tasks"
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    description = "Outbound for container images and package downloads"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "predixion-gpu-worker-sg"
  }
}

# IAM Role for GPU Instance
resource "aws_iam_role" "gpu_instance_role" {
  count = var.enable_gpu_worker ? 1 : 0
  name  = "predixion-gpu-instance-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "gpu_ssm" {
  count      = var.enable_gpu_worker ? 1 : 0
  role       = aws_iam_role.gpu_instance_role[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "gpu" {
  count = var.enable_gpu_worker ? 1 : 0
  name  = "predixion-gpu-instance-profile"
  role  = aws_iam_role.gpu_instance_role[0].name
}

# Open-Weight GPU Instance (g5.xlarge: 1x NVIDIA A10G 24GB VRAM)
# Runs Faster-Whisper + Mistral-7B-Instruct / Llama-3.1-8B + Piper TTS
resource "aws_instance" "gpu_worker" {
  count                = var.enable_gpu_worker ? 1 : 0
  ami                  = "ami-03f4878e83d3f10c8" # Deep Learning OSS Nvidia AMI (ap-south-1)
  instance_type        = "g5.xlarge"
  subnet_id            = aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.gpu_worker[0].id]
  iam_instance_profile = aws_iam_instance_profile.gpu[0].name

  root_block_device {
    volume_size           = 100
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name        = "predixion-openweight-gpu-worker"
    Stage       = "STT-LLM-TTS"
    Environment = var.environment
  }
}
