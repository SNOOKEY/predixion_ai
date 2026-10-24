# Predixion AI x TalentX — Resilient Collections Inference Service
### AWS Forward Deployment Engineer Track — Round 1 & Round 2 Unified Solution

[![CI Pipeline](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: Bandit](https://img.shields.io/badge/security-bandit-black.svg)](https://github.com/PyCQA/bandit)
[![Vulnerability Scan: Trivy](https://img.shields.io/badge/vulnerability%20scan-trivy-1904DA.svg)](https://github.com/aquasecurity/trivy)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)
[![Terraform: ap-south-1](https://img.shields.io/badge/IaC-Terraform-7B42BC.svg?logo=terraform&logoColor=white)](terraform/)

---

## Overview

A resilient, compliant voice-agent inference service designed for BFSI collections portfolios (Credit Card, BNPL, Personal Loans). In high-volume collections environments, dropped calls create regulatory non-compliance risks and degrade borrower engagement.

This solution solves downstream degradation (20% injected 503 errors and lognormal latency spikes) by combining **client-side resilience** (exponential backoff with jitter and a three-state circuit breaker), a **zero-loss Dead-Letter Queue (SQLite + AWS SQS)**, and **enterprise cloud deployment** (AWS ECS Fargate Multi-AZ in `ap-south-1` with automated rollback).

---

## Repository Structure

```text
predixion_ai/
│
├── .github/workflows/          # Automated CI/CD pipeline (Lint, Pytest, SAST, Trivy, ECR)
│
├── inference_service/          # [Task 1 & 2] Core Resilient Inference Service
│   ├── main.py                 # FastAPI app, metrics, and DLQ inspect/replay APIs
│   ├── pipeline_client.py      # Resilient client + abstract BasePipelineClient interface
│   ├── circuit_breaker.py      # CLOSED / OPEN / HALF_OPEN state machine
│   ├── dlq_handler.py          # Unified DLQ handler (AWS SQS + SQLite fallback)
│   ├── schemas.py              # Pydantic data models (Appendix A compliant)
│   └── config.py               # Environment variables (SQS_DLQ_URL, timeouts, retries)
│
├── mock_dependency/            # [Task 1] Downstream Mock Pipeline (:8001)
│   └── main.py                 # Simulates 20% 503 failures & lognormal latency
│
├── traffic_generator/          # [Task 1 & 2] Traffic Replay & Benchmark Engine
│   ├── generate_dataset.py     # Generates synthetic borrower events
│   └── load_tester.py          # Replays Appendix C traffic curves & inspects DLQ
│
├── tests/                      # Unit & integration test suite (12 tests, 100% passing)
│   ├── test_resilience.py      # Circuit breaker, backoff jitter, and recovery tests
│   └── test_sqs_dlq.py         # AWS SQS dead-letter enqueue & replay tests
│
├── terraform/                  # [Task 2] Production IaC for AWS ap-south-1 (Mumbai)
│   ├── vpc.tf                  # Multi-AZ VPC (ap-south-1a & ap-south-1b), private subnets, NAT
│   ├── alb.tf                  # Application Load Balancer, Target Group & Health Checks
│   ├── ecs_fargate.tf          # ECS Cluster & Multi-AZ Fargate Service
│   ├── autoscaling.tf          # Request-count target tracking & in-flight step scaling
│   ├── sqs.tf                  # AWS SQS collections-dlq (14-day compliance audit retention)
│   ├── iam.tf                  # Appendix F non-wildcard least-privilege IAM task roles
│   ├── cloudwatch.tf           # Real-time Observability Dashboard
│   ├── variables.tf            # Configurable deployment inputs
│   └── outputs.tf              # DNS, Cluster, Service, SQS, and Dashboard URLs
│
├── docs/                       # Operational Documentation
│   ├── seniority_architecture.md # Multi-AZ HA, Rolling Blue/Green rollback, RBI data residency
│   ├── runbook.md              # Appendix I Incident Response Runbook
│   └── round2_guide.md         # Deployment, live verification, & teardown guide
│
├── cost_estimate.md            # AWS Fargate cost analysis (Config A vs Config B)
├── docker-compose.yml          # One-command local development harness
├── Dockerfile                  # Multi-stage production container image
├── LICENSE                     # Apache 2.0 Open-Source License
└── README.md
```

---

## Task 1: Core Resilience & Local Verification

```
                      +-------------------------------------------------------+
                      |               Inference Service (:8000)               |
                      |                                                       |
  Call Events         |   +-----------------------------------------------+   |
 [Appendix A] ------> |   |         ResilientPipelineClient               |   |
 (Traffic Generator)  |   |  - Exponential Backoff + Full Jitter          |   |
                      |   |  - Circuit Breaker (CLOSED / OPEN / HALF-OPEN)|   |
                      |   +-----------------------+-----------------------+   |
                      |                           | Calls boundary            |
                      |                           v                           |
                      |   +-----------------------------------------------+   |
                      |   |          BasePipelineClient (Boundary)        |   |
                      |   +-----------------------+-----------------------+   |
                      +---------------------------|---------------------------+
                                                  | POST /pipeline/process
                                                  v
                      +-------------------------------------------------------+
                      |             Mock Downstream Pipeline (:8001)          |
                      |  - 20% Injected HTTP 503 Transients                   |
                      |  - Lognormal Latency (p50~300ms, p90~700ms, p99~1.2s) |
                      +-------------------------------------------------------+
                                                  |
                                                  | On Retry Exhaustion / Breaker Trip
                                                  v
                      +-------------------------------------------------------+
                      |          Dead-Letter Queue (SQLite / AWS SQS)         |
                      |  - Zero dropped calls (100% event capture)            |
                      |  - Inspectable: GET /dlq, GET /dlq/stats              |
                      |  - Replayable: POST /dlq/replay                       |
                      +-------------------------------------------------------+
```

### Key Engineering Features:
1. **Exponential Backoff with Full Randomized Jitter**:
   $$\text{sleep} = \min(\text{max\_delay}, \text{base\_delay} \times 2^{\text{attempt}}) + \text{uniform}(0, \text{jitter})$$
   Prevents thundering herd problems when downstream models recover from transient pauses.
2. **Three-State Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`)**:
   Fast-fails incoming calls during persistent outages without wasting network socket capacity, and safely probes downstream readiness using half-open trial calls.
3. **Dual-Store Dead-Letter Queue**:
   - **Local Development**: Stores failed events in persistent SQLite ([data/dead_letters.db](file:///C:/Users/anazs/projects/predixion_ai/data/dead_letters.db)).
   - **Cloud Production**: Switches transparently to **AWS SQS** when `SQS_DLQ_URL` is set.
4. **Model-Agnostic Interface**:
   `BasePipelineClient` decouples resilience logic from model inference. Swapping the mock for real open-weight models (`faster-whisper`, `vLLM`, `Piper`) requires zero code changes to the resilience layer.

### Local Execution (Docker Compose)
```bash
docker compose up --build
```
- Inference Service: `http://localhost:8000`
- Mock Downstream: `http://localhost:8001`
- Run 30-call verification test:
  ```bash
  python traffic_generator/load_tester.py --quick
  ```

---

## Task 2: Cloud Infrastructure & AWS ECS Deployment

The complete service is deployed to **AWS ECS Fargate** behind an **Application Load Balancer** in the **`ap-south-1` (Mumbai)** region using Terraform.

```
                                  [Internet]
                                      │
                                      ▼
                        [Application Load Balancer]
                        (Public Subnets in 1a & 1b)
                         /                       \
                        /                         \
                       ▼                           ▼
        [Private Subnet: ap-south-1a]    [Private Subnet: ap-south-1b]
       ┌────────────────────────────┐   ┌────────────────────────────┐
       │ ECS Task 1 (Active)        │   │ ECS Task 2 (Active)        │
       │ • inference-service (:8000)│   │ • inference-service (:8000)│
       │ • mock-dependency   (:8001)│   │ • mock-dependency   (:8001)│
       └────────────────────────────┘   └────────────────────────────┘
                       │                               │
                       └───────────────┬───────────────┘
                                       ▼
                       [Centralized AWS SQS DLQ]
                     (collections-dlq: 14-day audit)
```

### Production Infrastructure Highlights:
- **Multi-AZ High Availability**: Baseline capacity of 2 tasks distributed across `ap-south-1a` and `ap-south-1b` in private subnets with NAT egress.
- **Auto-Scaling**:
  - Target Tracking on ALB Request Count (scales out when a task exceeds 30 req/min).
  - Step Scaling on CloudWatch `InFlightCalls` (scales up to 5 tasks during 50 calls/min campaign surges).
- **Zero-Downtime Rolling Blue/Green Rollback**:
  - `deployment_maximum_percent = 200`: Spins up new Green containers alongside active Blue containers.
  - `deployment_minimum_healthy_percent = 100`: Guarantees 100% capacity remains online throughout deployments.
  - `deployment_circuit_breaker { enable = true, rollback = true }`: Automatically trips and rolls back to the previous stable task revision if new containers fail health checks.
- **Least-Privilege Security (Appendix F)**:
  - Task Execution Role: restricted to ECR image pull and CloudWatch logs.
  - Task Role: strictly limited to SQS actions (`SendMessage`, `ReceiveMessage`, `DeleteMessage`) on `collections-dlq` and scoped metric publishing. No wildcard permissions.
- **RBI Data Localization**:
  - Deployed strictly in `ap-south-1` (Mumbai). All borrower data, recovery records, and audit logs remain within Indian borders as required by RBI Digital Lending guidelines.

### Deploying with Terraform:
```bash
cd terraform
terraform init
terraform plan
terraform apply -auto-approve
```

---

## Live Verification & Testing Commands

To run traffic against the deployed AWS infrastructure:

```bash
# 1. Check ALB Service Health
curl http://<ALB_DNS>/health

# 2. Check SQS DLQ Status
curl http://<ALB_DNS>/dlq/stats

# 3. Replay Synthetic Traffic Batch
python traffic_generator/load_tester.py --url http://<ALB_DNS> --quick --count 75

# 4. Replay Failed Dead-Letter Calls from SQS
curl -X POST http://<ALB_DNS>/dlq/replay

# 5. Access Real-Time CloudWatch Dashboard
# URL outputted by terraform output cloudwatch_dashboard_url
```

---

## REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/calls/process` | Ingests a voice call event; executes resilient pipeline or degrades to DLQ. |
| `GET` | `/calls` | Query historical audit logs of all past calls. |
| `GET` | `/calls/summary` | Aggregate call counts, success percentage, and latency percentiles. |
| `GET` | `/dlq` | Inspect stored dead letters (supports pagination & status filters). |
| `GET` | `/dlq/stats` | View DLQ statistics and failure reasons (from SQLite or AWS SQS). |
| `POST` | `/dlq/replay` | Polls and re-executes pending dead-letter calls through the pipeline. |
| `POST` | `/dlq/replay/{event_id}` | Replays a specific dead-letter event by ID. |
| `GET` | `/circuit-breaker/status` | Current circuit breaker state (`CLOSED`, `OPEN`, `HALF_OPEN`) & trip counters. |
| `POST` | `/circuit-breaker/reset` | Administratively resets circuit breaker to `CLOSED`. |
| `GET` | `/health` | Service readiness probe (used by ALB target group health checks). |
| `GET` | `/metrics` | Prometheus/JSON telemetry metrics (requests, latency, DLQ volume). |

---

## Automated CI/CD Pipeline

The repository includes a comprehensive 4-stage GitHub Actions workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

1. **Stage 1 (Code Quality & Tests)**: Ruff linting, formatting checks, and Pytest test suite with code coverage.
2. **Stage 2 (Security Scanning)**: Dependency vulnerability sniffing (`pip-audit`), AST static analysis (`Bandit`), and secret leak detection (`TruffleHog`).
3. **Stage 3 (SonarQube Quality Gate)**: Cloud quality gate analysis for code maintainability and test coverage.
4. **Stage 4 (Container Build & Trivy Scan)**: Docker Buildx container build, Aqua Trivy CVE image scanning, and push to AWS ECR.

---

## Clean Teardown

To tear down all provisioned cloud resources and avoid ongoing AWS charges:

```bash
cd terraform
terraform destroy -auto-approve
```

---

## License

This project is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for details.
