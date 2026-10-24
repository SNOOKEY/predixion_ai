variable "aws_region" {
  type        = string
  description = "AWS region for deployment (ap-south-1 required per challenge spec)"
  default     = "ap-south-1"
}

variable "environment" {
  type        = string
  description = "Environment identifier"
  default     = "production"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for VPC"
  default     = "10.0.0.0/16"
}

variable "app_image" {
  type        = string
  description = "Container image for the inference service"
  default     = "predixion-inference-service:1.0"
}

variable "app_port" {
  type        = number
  description = "Port exposed by the inference service container"
  default     = 8000
}

variable "fargate_cpu" {
  type        = number
  description = "Fargate vCPU allocation (1024 = 1 vCPU per Config A, 512 = 0.5 vCPU per Config B)"
  default     = 1024
}

variable "fargate_memory" {
  type        = number
  description = "Fargate memory allocation in MB (2048 = 2 GB per Config A, 1024 = 1 GB per Config B)"
  default     = 2048
}

variable "min_tasks" {
  type        = number
  description = "Minimum running task count (baseline = 1 task)"
  default     = 1
}

variable "max_tasks" {
  type        = number
  description = "Maximum running task count during campaign spikes (5 for Config A, 10 for Config B)"
  default     = 5
}

variable "enable_gpu_worker" {
  type        = bool
  description = "Whether to provision the g5.xlarge GPU host for open-weight model deployment (Round 2 live test)"
  default     = false
}
