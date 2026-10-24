# Round 2 Deployment & Live Verification Guide

This guide walks through deploying the Predixion Collections Voice-Agent on AWS in region `ap-south-1` (Mumbai), verifying live resilience under the synthetic traffic profile, and executing a clean teardown.

---

## 1. Prerequisites

1. **AWS CLI** configured with credentials for your personal AWS account:
   ```bash
   aws sts get-caller-identity
   ```
2. **Terraform CLI** (v1.5.0+) installed.
3. **Docker** installed for building and pushing the container image to Amazon ECR.
4. **GPU Quota (Appendix G)**:
   - In AWS Console $\rightarrow$ **Service Quotas** $\rightarrow$ **Amazon Elastic Compute Cloud (EC2)**.
   - Request increase for **"Running On-Demand G and VT instances"** to $\ge 4$ vCPUs in `ap-south-1`.

---

## 2. Container Build & Push to Amazon ECR

1. **Create an ECR repository**:
   ```bash
   aws ecr create-repository \
     --repository-name predixion-inference-service \
     --region ap-south-1
   ```

2. **Authenticate Docker to ECR**:
   ```bash
   ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
   aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com
   ```

3. **Build and push image**:
   ```bash
   docker build -t ${ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com/predixion-inference-service:1.0 .
   docker push ${ACCOUNT_ID}.dkr.ecr.ap-south-1.amazonaws.com/predixion-inference-service:1.0
   ```

---

## 3. Terraform Deployment

1. **Initialize Terraform**:
   ```bash
   cd terraform
   terraform init
   ```

2. **Create `terraform.tfvars`**:
   ```hcl
   aws_region  = "ap-south-1"
   environment = "production"
   app_image   = "<ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/predixion-inference-service:latest"
   fargate_cpu = 1024
   fargate_memory = 2048
   min_tasks   = 1
   max_tasks   = 5
   enable_gpu_worker = false # Set true once GPU quota is approved
   ```

3. **Apply Infrastructure**:
   ```bash
   terraform plan -out=tfplan
   terraform apply tfplan
   ```

4. Note the output `alb_dns_name` and `cloudwatch_dashboard_url`.

---

## 4. Live Traffic & Autoscaling Demonstration

1. Run the compressed traffic schedule against the live ALB endpoint:
   ```bash
   python -m traffic_generator.main \
     --target-url http://<ALB_DNS_NAME>/events/process \
     --compressed
   ```

2. Open the **CloudWatch Dashboard**:
   - Verify request ramp: $10 \to 50$ calls/min.
   - Observe ECS running task count scaling automatically from 1 to 5.
   - Confirm downstream 503 errors trigger exponential backoff and circuit breaking.
   - Inspect SQS DLQ depth ensuring zero calls are dropped.

---

## 5. Teardown & Clean Destruction (Graded)

Leaving billable infrastructure running after a demo is heavily penalized. Ensure complete cleanup:

1. **Destroy Terraform resources**:
   ```bash
   cd terraform
   terraform destroy -auto-approve
   ```

2. **Verify Zero Running Billable Resources**:
   ```bash
   # Confirm no active ECS tasks
   aws ecs list-tasks --cluster predixion-cluster --region ap-south-1

   # Confirm ALB is deleted
   aws elbv2 describe-load-balancers --region ap-south-1

   # Confirm NAT Gateways are deleted
   aws ec2 describe-nat-gateways --filter "Name=state,Values=available,pending" --region ap-south-1
   ```
3. Take a screenshot or capture terminal logs of the clean state as required for your final Round 2 submission.
