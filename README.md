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

## ⚡ 60-Second Evaluator Quickstart

You can evaluate this project either by **testing our live AWS deployment** or by **running it locally in one command**.

### Option A: Test Live AWS Deployment (Round 2 — Active in `ap-south-1`)

Our production infrastructure is live on AWS Mumbai. You can query it right now:

```bash
# 1. Health Probe (ALB -> Multi-AZ ECS Fargate)
curl http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/health

# 2. Check AWS SQS Dead-Letter Queue
curl http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/dlq/stats

# 3. Send 30-call traffic batch through live ALB
python traffic_generator/load_tester.py --url http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com --quick --count 30

# 4. Replay any failed calls stored in AWS SQS DLQ
curl -X POST http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/dlq/replay
```

> **Live AWS Console Dashboard**: [CollectionsAgent-Observability (CloudWatch)](https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#dashboards:name=CollectionsAgent-Observability)

---

### Option B: Run Locally with One Command (Round 1)

Evaluators do **not** need to configure AWS credentials or install scanners locally. Everything runs in Docker:

```bash
# 1. Clone & spin up both services (Inference on :8000, Mock Downstream on :8001)
docker compose up --build -d

# 2. Run quick verification test (30 calls under 20% simulated downstream failure)
python traffic_generator/load_tester.py --quick

# 3. Inspect stored calls & dead letters in terminal
python traffic_generator/load_tester.py --inspect
```

---

## 🎯 Challenge Requirements & Compliance Matrix

| Track / Section | Rubric Requirement | Implementation Location | Status |
| :--- | :--- | :--- | :---: |
| **Round 1** | Resilient Inference Service (FastAPI) | [`inference_service/main.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/main.py) | ✅ **100%** |
| **Round 1** | Downstream Mock with 20% 503s & lognormal latency | [`mock_dependency/main.py`](file:///C:/Users/anazs/projects/predixion_ai/mock_dependency/main.py) | ✅ **100%** |
| **Round 1** | Backoff + Jitter & 3-State Circuit Breaker | [`inference_service/circuit_breaker.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/circuit_breaker.py) | ✅ **100%** |
| **Round 1** | Zero Dropped Calls Dead-Letter Queue | [`inference_service/dlq_handler.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/dlq_handler.py) | ✅ **100%** |
| **Round 1** | Appendix C Traffic Replayer & Metrics | [`traffic_generator/load_tester.py`](file:///C:/Users/anazs/projects/predixion_ai/traffic_generator/load_tester.py) | ✅ **100%** |
| **Round 2** | AWS ECS Fargate Deployment via Terraform | [`terraform/ecs_fargate.tf`](file:///C:/Users/anazs/projects/predixion_ai/terraform/ecs_fargate.tf) | ✅ **100%** |
| **Round 2** | Centralized AWS SQS DLQ (`collections-dlq`) | [`terraform/sqs.tf`](file:///C:/Users/anazs/projects/predixion_ai/terraform/sqs.tf) | ✅ **100%** |
| **Round 2** | CloudWatch Observability Dashboard | [`terraform/cloudwatch.tf`](file:///C:/Users/anazs/projects/predixion_ai/terraform/cloudwatch.tf) | ✅ **100%** |
| **Round 2** | Appendix F Non-Wildcard IAM Least Privilege | [`terraform/iam.tf`](file:///C:/Users/anazs/projects/predixion_ai/terraform/iam.tf) | ✅ **100%** |
| **Seniority** | Multi-AZ HA (`ap-south-1a` & `ap-south-1b`) + Playbook | [`docs/seniority_architecture.md`](file:///C:/Users/anazs/projects/predixion_ai/docs/seniority_architecture.md#L11-L53) | ✅ **100%** |
| **Seniority** | Zero-Downtime Deployment & Automated Rollback | [`terraform/ecs_fargate.tf`](file:///C:/Users/anazs/projects/predixion_ai/terraform/ecs_fargate.tf#L95-L100) | ✅ **100%** |
| **Seniority** | RBI Data Localization & Sub-15ms Cadence in Mumbai | [`docs/seniority_architecture.md`](file:///C:/Users/anazs/projects/predixion_ai/docs/seniority_architecture.md#L74-L90) | ✅ **100%** |

---

## Architecture Overview

```
                      +-------------------------------------------------------+
                      |               Inference Service (:8000)               |
                      |                                                       |
  Call Events         |   +-----------------------------------------------+   |
 [Appendix A] ------> |   |         ResilientPipelineClient               |   |
 (Traffic Generator)  |   |  - Exponential Backoff + Randomized Jitter    |   |
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
                      |  - Zero dropped calls (100% compliance capture)       |
                      |  - Fully inspectable (GET /dlq, GET /dlq/stats)       |
                      |  - Replayable (POST /dlq/replay)                      |
                      +-------------------------------------------------------+
```

---

## Detailed Component Breakdown

### 1. Resilience Layer & Circuit Breaker
- **Full Jitter Exponential Backoff**:
  $$\text{delay} = \min(\text{max\_delay}, \text{base\_delay} \times 2^{\text{attempt}}) + \text{uniform}(0, \text{jitter})$$
  Ensures that when the downstream mock returns 503s or suffers from latency spikes, retries do not synchronize into destructive retry storms.
- **Circuit Breaker State Machine**:
  - `CLOSED`: Normal operation. Failures increment an error counter.
  - `OPEN`: Once consecutive failures exceed the threshold (default: 5), calls fast-fail immediately to DLQ in < 1ms without calling downstream.
  - `HALF_OPEN`: After a cooldown period (default: 30s), trial requests test if downstream recovered. If successful, resets to `CLOSED`; if failed, flips back to `OPEN`.

### 2. Zero-Loss Dead-Letter Queue (DLQ)
- **Zero Dropped Calls**: In debt collections, dropping a borrower call mid-interaction or discarding an unlogged failure violates compliance rules.
- **Dual Storage Engine**:
  - **Local Mode**: Uses SQLite database at [`data/dead_letters.db`](file:///C:/Users/anazs/projects/predixion_ai/data/dead_letters.db).
  - **Cloud Mode**: When `SQS_DLQ_URL` is set, the service routes dead letters directly to **AWS SQS** (`collections-dlq`).
- **Replay Capability**: Both SQLite and AWS SQS support one-click replay via `POST /dlq/replay`, re-injecting failed calls back into the pipeline once downstream models recover.

### 3. Model-Agnostic Interface (`BasePipelineClient`)
The resilience layer wraps an abstract interface:
```python
class BasePipelineClient(ABC):
    @abstractmethod
    async def process_call(self, payload: dict) -> dict: ...
```
To swap the HTTP mock for a real open-weight stack (`faster-whisper` STT $\to$ `vLLM` Llama-3 LLM $\to$ `Piper` TTS), simply implement `BasePipelineClient`. The circuit breaker, retries, and DLQ operate identically with zero modifications.

---

## AWS Cloud Infrastructure (Round 2)

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

### Seniority Architecture Details:
1. **Multi-AZ High Availability**:
   - Deployed across `ap-south-1a` and `ap-south-1b` in private subnets.
   - Outbound egress restricted via NAT Gateway. No task has a public IP.
2. **Rolling Blue/Green Deployment with Automated Rollback**:
   - `deployment_maximum_percent = 200`: Launches new Green tasks before terminating old Blue tasks.
   - `deployment_minimum_healthy_percent = 100`: Preserves 100% capacity at all times.
   - `deployment_circuit_breaker { enable = true, rollback = true }`: Automatically halts deployment and rolls back to the previous stable revision if the new version fails ALB `/health` checks.
3. **Autoscaling Policies**:
   - Target Tracking: Maintains 30 requests/target/minute.
   - Step Scaling: Scales up to 5 tasks when in-flight voice calls spike above 25.
4. **RBI Data Localization (`ap-south-1`)**:
   - Mandated by Reserve Bank of India (RBI) Digital Lending directives. All audio data, loan agreement references, and dead letters remain strictly within Mumbai data centers.

---

## Step-by-Step Local Execution

### Prerequisites
- Python 3.10+ (or Docker / Docker Compose)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run automated test suite (12 unit tests)
python -m unittest discover tests

# 3. Start Mock Downstream (Terminal 1)
python -m uvicorn mock_dependency.main:app --host 0.0.0.0 --port 8001

# 4. Start Inference Service (Terminal 2)
python -m uvicorn inference_service.main:app --host 0.0.0.0 --port 8000

# 5. Generate call dataset & run load tester (Terminal 3)
python traffic_generator/generate_dataset.py
python traffic_generator/load_tester.py --quick
```

---

## API Documentation

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/calls/process` | Ingests a call event; executes resilient pipeline or degrades to DLQ. |
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

## Automated CI/CD Pipeline (GitHub Actions)

Every push or pull request to `main` executes our 4-stage pipeline:
1. **Code Quality & Tests**: `ruff` linting + unit tests with coverage.
2. **Security & Vulnerability Scanning**: `pip-audit` (SCA), `Bandit` (AST SAST), `TruffleHog` (Secret leaks).
3. **SonarQube Quality Gate**: Static code analysis & maintainability score.
4. **Container Build & Trivy Scan**: Docker multi-tag build + Aqua Trivy image vulnerability scan + push to AWS ECR.

---

## Infrastructure Teardown

To avoid unnecessary cloud costs after evaluation:

```bash
cd terraform
terraform destroy -auto-approve
```

---

## License

This project is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for details.
