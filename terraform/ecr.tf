resource "aws_ecr_repository" "app" {
  name                 = "predixion-inference-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "predixion-inference-service"
  }
}

output "ecr_repository_url" {
  description = "URL of the created ECR repository"
  value       = aws_ecr_repository.app.repository_url
}
