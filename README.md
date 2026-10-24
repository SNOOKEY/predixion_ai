# Predixion AI — Resilient Voice Collections Agent
### AWS Forward Deployment Engineer Track — Technical Submission

[![CI Pipeline](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Terraform: ap-south-1](https://img.shields.io/badge/IaC-Terraform-7B42BC.svg?logo=terraform&logoColor=white)](terraform/)

---

## Overview & Business Context

In high-volume BFSI debt collections (Credit Card, BNPL, Personal Loans), call drops are costly and hazardous: they damage borrower trust, erase in-flight negotiation context, and create severe regulatory compliance violations under RBI Fair Practices directives.

When a downstream voice pipeline (STT $\to$ LLM $\to$ TTS) experiences transient degradation—specifically the **20% HTTP 503 error rate** and **lognormal latency spikes (p50 ~300ms, p90 ~700ms, p99 ~1.2s)** specified in the assignment PDF—a standard service crashes or drops customer calls.

This repository is organized strictly into two linear phases:
- **Task 1 (Local Track)**: The core resilient inference service, Docker container build, cost analysis writeup, and the interface for swapping in open-weight models.
- **Task 2 (AWS Cloud Track)**: Full production deployment on AWS ECS Fargate behind an Application Load Balancer in `ap-south-1` (Mumbai), operational runbook, and Seniority stretch goals.

---

## 📋 Assignment Deliverables Checklist

| Assignment Task | Required Deliverable | Repository Location | Notes |
| :--- | :--- | :--- | :--- |
| **Task 1** | **Git Repo: Code + README** | [`inference_service/`](inference_service/) & [`README.md`](README.md) | Full resilience service & 12 passing unit tests |
| **Task 1** | **Half-Page Cost Writeup** | [`cost_estimate.md`](cost_estimate.md) & [Section 1.4](#14-half-page-cost-estimation-writeup) | Detailed Config A vs Config B comparison in `ap-south-1` |
| **Task 1** | **One Paragraph: Swapping Mock for Real Model** | [Section 1.5](#15-how-to-swap-the-mock-for-a-real-open-weight-model-one-paragraph) | Model-agnostic `BasePipelineClient` interface |
| **Task 2** | **Full Repo: IaC + App + README** | [`terraform/`](terraform/) & [`README.md`](README.md) | Multi-AZ ECS Fargate IaC in `ap-south-1` behind ALB |
| **Task 2** | **Operational Runbook** | [`docs/runbook.md`](docs/runbook.md) & [Section 2.3](#23-operational-runbook-appendix-i) | Appendix I Incident triage, diagnostics, and recovery |
| **Task 2** | **Seniority Architecture Blueprint** | [`docs/seniority_architecture.md`](docs/seniority_architecture.md) & [Section 2.4](#24-seniority-track-stretch-objectives) | Multi-AZ simulation, Blue/Green rollback, & RBI compliance |
| **Task 2** | **Video Walkthrough** | *(To be uploaded separately)* | Live load demo, SQS DLQ verification, and clean teardown |

---

# TASK 1: Resilient Inference Service (Local Track)

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
                      |          Dead-Letter Queue (SQLite: data/dead_letters.db)
                      |  - Zero dropped calls (100% compliance capture)       |
                      |  - Fully inspectable (GET /dlq, GET /dlq/stats)       |
                      |  - Replayable (POST /dlq/replay)                      |
                      +-------------------------------------------------------+
```

### 1.1 How to Run Task 1

Evaluators can run Task 1 either using local Python or using the Docker container build.

#### Option A: Local Python Execution
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run automated unit test suite (12 tests, 100% pass)
python -m unittest discover tests

# 3. Start Mock Downstream Pipeline on port 8001 (Terminal 1)
python -m uvicorn mock_dependency.main:app --host 0.0.0.0 --port 8001

# 4. Start Resilient Inference Service on port 8000 (Terminal 2)
python -m uvicorn inference_service.main:app --host 0.0.0.0 --port 8000

# 5. Generate synthetic call dataset (Terminal 3)
python traffic_generator/generate_dataset.py

# 6. Run Traffic Replayer & Resilience Benchmark:
# Command 1: Quick verification batch with 30 sample calls
python traffic_generator/load_tester.py --quick --count 30

# Command 2: Full 24-hour schedule simulation (Appendix C compressed profile: baseline 10 cpm -> surge 50 cpm)
python traffic_generator/load_tester.py --hours 24 --compression 360

# Command 3: Inspect all stored calls, latency metrics, and dead letters
python traffic_generator/load_tester.py --inspect
```

#### Option B: Docker Container Build & Run (Single Command)
Build and spin up both container services in the background:
```bash
# 1. Build and start inference service and mock dependency
docker compose up --build -d

# 2. Run quick verification batch with 30 sample calls
python traffic_generator/load_tester.py --quick --count 30

# 3. Run full 24-hour schedule simulation (compressed profile)
python traffic_generator/load_tester.py --hours 24 --compression 360

# 4. Inspect stored SQLite dead letters
python traffic_generator/load_tester.py --inspect
```

---

### 1.2 Inspection Endpoints & Status Probes (Task 1)

While the service is running locally on port 8000 (via either Python or Docker Compose), you can monitor, inspect, and trigger the service directly through your web browser or `curl`:

| What You Want to See | Browser / Curl URL | Description |
| :--- | :--- | :--- |
| **Service Health & Breaker State** | [`http://localhost:8000/health`](http://localhost:8000/health) | Live status probe and current circuit breaker state (`CLOSED`, `OPEN`, `HALF_OPEN`). |
| **Dead-Letter Queue Statistics** | [`http://localhost:8000/dlq/stats`](http://localhost:8000/dlq/stats) | Summary count of total, pending, and replayed dead letters with failure reason breakdown. |
| **Inspect Stored Dead Letters** | [`http://localhost:8000/dlq`](http://localhost:8000/dlq) | Full records of all dropped/degraded calls with payloads, retry counts, and error reasons. |
| **Historical Call Audit Log** | [`http://localhost:8000/calls`](http://localhost:8000/calls) | Browse every processed call stored in SQLite across all simulation runs. |
| **Call Volume & Latency Summary** | [`http://localhost:8000/calls/summary`](http://localhost:8000/calls/summary) | Aggregated metrics: total calls, success rate, and average latency. |
| **Circuit Breaker Diagnostics** | [`http://localhost:8000/circuit-breaker/status`](http://localhost:8000/circuit-breaker/status) | Detailed circuit breaker telemetry (trip counts, consecutive failure tallies). |
| **Real-time Telemetry Metrics** | [`http://localhost:8000/metrics`](http://localhost:8000/metrics) | Combined active session and cumulative database performance counters. |
| **Replay Pending Dead Letters** | `curl -X POST http://localhost:8000/dlq/replay` | Manually triggers re-injection of all pending dead letters back into the pipeline. |

---

### 1.3 Resilience Implementation Details

1. **Exponential Backoff with Full Randomized Jitter**:
   $$\text{sleep} = \min(\text{max\_delay}, \text{base\_delay} \times 2^{\text{attempt}}) + \text{uniform}(0, \text{jitter})$$
   - Prevents synchronized retry storms (thundering herds) when downstream models pause or restart.
   - Configured with `base_delay = 0.5s`, `max_delay = 4.0s`, `max_retries = 3`.
2. **Three-State Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`)**:
   - Implemented in [`inference_service/circuit_breaker.py`](inference_service/circuit_breaker.py).
   - If 5 consecutive failures occur, the breaker flips to `OPEN` and fast-fails incoming calls in < 1ms directly to DLQ without burdening downstream sockets.
   - After a 30s cooldown, it enters `HALF_OPEN` to test recovery with trial requests.
3. **Zero Dropped Calls (Dead-Letter Queue)**:
   - Captures 100% of failed calls into [`data/dead_letters.db`](data/dead_letters.db) with `event_id`, `borrower_bucket`, `failure_reason`, `retry_count`, and complete original payload.
   - Inspectable via REST API (`GET /dlq`, `GET /dlq/stats`) and replayable via `POST /dlq/replay`.
4. **Traffic Replay Engine (Appendix C Curve)**:
   - Replays 10 cpm baseline traffic and 50 cpm campaign surge windows using 500 realistic borrower records across standard DPD risk buckets.

---

### 1.4 Half-Page Cost Estimation Writeup

> Complete detailed analysis file: [`cost_estimate.md`](cost_estimate.md)

**Workload Profile:** 24-hour cycle in `ap-south-1` (Mumbai). 20 hours/day baseline at 10 calls/min, 4 hours/day campaign surge at 50 calls/min (~60s average call duration).

| Component | Config A (1 vCPU / 2 GB RAM) | Config B (0.5 vCPU / 1 GB RAM) |
| :--- | :--- | :--- |
| **Scale Range** | 1 to 5 Tasks | 1 to 10 Tasks |
| **Task Unit Rate** | \$0.0556 / task-hour | \$0.0278 / task-hour |
| **Monthly Compute** | **\$55.63 / month** | **\$38.67 / month** |
| **Fixed Overhead (ALB + NAT + Logs)** | **\$74.80 / month** | **\$74.80 / month** |
| **Total Estimated Spend** | **\$130.43 / month** | **\$113.47 / month** |

**Architectural Recommendation: Config A (1 vCPU / 2 GB)**
- **Overhead Dominance Paradox**: Fixed infrastructure (NAT Gateway and ALB) accounts for 60–65% of the bill. Over-optimizing compute to Config B yields a modest saving of only ~$17/month.
- **Python GIL & Async Headroom**: An async Python service running JSON parsing, SQLite I/O, and circuit breaker synchronization throttles on 0.5 vCPU, causing $p99$ latency spikes and premature breaker trips.
- **Scaling Responsiveness**: Config A scales to peak capacity by launching only 4 additional tasks (reaching stability in < 45s), whereas Config B must coordinate launching 9 tasks during rapid 15-minute campaign bursts.

---

### 1.5 How to Swap the Mock for a Real Open-Weight Model (One Paragraph)

> **Prompt Requirement**: *One paragraph on how you’d swap the mock for a real open-weight model.*  
> To swap the mocked dependency for a real open-weight model stack without touching any resilience logic, we implement the abstract `BasePipelineClient` interface defined in [`inference_service/pipeline_client.py`](inference_service/pipeline_client.py): incoming borrower voice streams to `faster-whisper` (`large-v3-turbo` with CTranslate2 for sub-200ms GPU transcription), the transcribed text is dispatched asynchronously to a local `vLLM` high-throughput inference engine serving an AWQ-quantized `Llama-3.1-8B-Instruct` or `Mistral-7B-Instruct` model to generate context-aware collections negotiation responses, and the output text is synthesized to natural speech via `Piper TTS` in under 50ms. Because `ResilientPipelineClient` acts as a pure decorator over `BasePipelineClient`, the exponential jitter retries, three-state circuit breaker, and Dead-Letter Queue capture continue to protect the system identically with zero code modifications when pointing to real GPU hardware.

```python
class OpenWeightPipelineClient(BasePipelineClient):
    def __init__(self, stt_client, vllm_client, tts_client):
        self.stt = stt_client      # faster-whisper (large-v3-turbo)
        self.vllm = vllm_client    # vLLM (Llama-3.1-8B-Instruct AWQ)
        self.tts = tts_client      # Piper TTS

    async def process_call(self, payload: dict) -> dict:
        transcript = await self.stt.transcribe(payload["audio_ref"])
        response_text = await self.vllm.generate(
            prompt=f"Collections Agent for {payload['event']['product_type']}: {transcript}"
        )
        audio_out = await self.tts.synthesize(response_text)
        return {"transcript": transcript, "response": response_text, "audio": audio_out}
```

---

# TASK 2: Cloud Infrastructure & AWS Production Deployment

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

In Task 2, the resilient inference service is deployed onto AWS infrastructure in `ap-south-1` (Mumbai) using Terraform.

### 2.1 AWS Cloud Architecture Highlights
1. **Multi-AZ VPC**: 2 public subnets (ALB & NAT Gateway) and 2 private subnets across `ap-south-1a` and `ap-south-1b`. Tasks run in private subnets with no public IPs.
2. **ECS Fargate Service**: Deploys both containers via a sidecar pattern (FastAPI resilience layer on :8000 and downstream mock on :8001).
3. **Application Load Balancer (ALB)**: Public ingress on port 80 with active `/health` checks.
4. **Centralized AWS SQS Dead-Letter Queue**: Distributed queue (`collections-dlq`) with 14-day compliance retention. When `SQS_DLQ_URL` is configured, dead letters automatically land in SQS instead of local SQLite.
5. **Autoscaling Target Tracking**:
   - Target tracking scales out when requests exceed 30 req/min per task.
   - Step scaling adds capacity when concurrent in-flight voice calls exceed 25.
6. **Least-Privilege IAM (Appendix F)**: Scoped strictly to ECR pulls, CloudWatch logs, and SQS actions (`SendMessage`, `ReceiveMessage`, `DeleteMessage`). No wildcard permissions.

---

### 2.2 How to Deploy & Live Demo on AWS

All cloud endpoints, queue URLs, and cluster identifiers are resolved dynamically from Terraform outputs without hardcoded addresses.

#### Step 1: Deploy Cloud Infrastructure
```bash
cd terraform
terraform init
terraform apply -auto-approve
cd ..
```

---

#### Step 2: Dynamically Capture Environment Variables
```bash
export ALB_URL="http://$(terraform -chdir=terraform output -raw alb_dns_name)"
export SQS_URL="$(terraform -chdir=terraform output -raw sqs_dlq_url)"
export CLUSTER="$(terraform -chdir=terraform output -raw ecs_cluster_name)"
```

---

#### Step 3: Service Health & Circuit Breaker Probes
```bash
# 1. ALB target health check probe
curl -s "$ALB_URL/health"

# 2. Inspect circuit breaker status (CLOSED, 0 trips)
curl -s "$ALB_URL/circuit-breaker/status"

# 3. Check baseline AWS SQS DLQ statistics
curl -s "$ALB_URL/dlq/stats"
```

---

#### Step 4: Observability Dashboard & Live Logs
```bash
# Print CloudWatch Observability Dashboard URL (open in browser)
terraform -chdir=terraform output -raw cloudwatch_dashboard_url

# Tail live ECS Fargate container logs in real-time
aws logs tail /ecs/collections-inference --region ap-south-1 --since 10m
```

---

#### Step 5: Live Traffic Load Test & DLQ Replay
```bash
# 1. Send 30 synthetic calls through the ALB into the Fargate cluster
python traffic_generator/load_tester.py --url $ALB_URL --quick --count 30

# 2. Inspect call telemetry and latency percentiles (p50/p95/p99)
curl -s "$ALB_URL/calls/summary"

# 3. Verify message count captured in AWS SQS Dead-Letter Queue
aws sqs get-queue-attributes \
  --queue-url "$SQS_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --region ap-south-1

# 4. Safely replay dead-letter messages from AWS SQS back through pipeline
curl -X POST "$ALB_URL/dlq/replay"
```

---

#### Step 6: Multi-AZ Zone Failure Simulation & Verification
Demonstrate high availability across `ap-south-1a` and `ap-south-1b`:

```bash
# 1. List active tasks across availability zones before outage
aws ecs list-tasks --cluster "$CLUSTER" --region ap-south-1

# 2. Simulate sudden AZ outage by stopping one running task
TASK_ID=$(aws ecs list-tasks --cluster "$CLUSTER" --region ap-south-1 --query "taskArns[0]" --output text)
aws ecs stop-task --cluster "$CLUSTER" --task "$TASK_ID" --reason "Simulating AZ outage" --region ap-south-1

# 3. Verify ZERO downtime: ALB immediately routes 100% of traffic to the survivor task
curl -s "$ALB_URL/health"

# 4. Check ECS auto-recovery events and verify replacement task is launched
aws ecs describe-services \
  --cluster "$CLUSTER" \
  --services collections-inference-service \
  --region ap-south-1 \
  --query "services[0].events[:3]" \
  --output table
```

---

### 2.3 Operational Runbook (Appendix I)

> Complete incident runbook file: [`docs/runbook.md`](docs/runbook.md)

Fulfills the Appendix I requirement for P1/P2 production incident response:
1. **Trigger Condition**: Activated when $p99 \text{ latency} > 3\times \text{baseline}~(\sim 3600\text{ms})$ or $5\text{XX Error Rate} > 5\%$ during campaign windows.
2. **First 5 Minutes Triage**: Step-by-step checklist inspecting CloudWatch Dashboard, ECS desired vs running task counts, ALB request rate, and circuit breaker status (`OPEN` vs `CLOSED`).
3. **Diagnostic Workflows**:
   - **Scenario A**: Downstream model outage (checking logs with `aws logs filter-log-events` and verifying SQS DLQ depth).
   - **Scenario B**: Autoscaling lag or task launch failure (checking Fargate vCPU quotas and adjusting scaling cooldowns).
4. **Escalation Matrix**: Clear time-based escalation tiers (0–10 min: On-Call FDE, 10–20 min: Lead Infra Engineer, > 20 min: Head of Engineering & Client Ops).
5. **Post-Incident Recovery**: Safe replay of all SQS dead letters via `POST /dlq/replay` and verification that zero calls were dropped.

---

### 2.4 Seniority Track Stretch Objectives

> Complete architectural writeup: [`docs/seniority_architecture.md`](docs/seniority_architecture.md)

#### 1. Multi-AZ High Availability & Failure Simulation Playbook
- **Baseline Deployment**: Configured with `min_tasks = 2`, running actively across `ap-south-1a` and `ap-south-1b`.
- **Dynamic Failure Simulation**:
  ```bash
  # Dynamically obtain cluster name from terraform output
  CLUSTER=$(terraform -chdir=terraform output -raw ecs_cluster_name)
  TASK_ID=$(aws ecs list-tasks --cluster "$CLUSTER" --region ap-south-1 --query "taskArns[0]" --output text)

  # Simulate sudden data center/AZ death by stopping that task
  aws ecs stop-task --cluster "$CLUSTER" --task "$TASK_ID" --reason "Simulating ap-south-1a outage" --region ap-south-1
  ```
  **Result**: ALB health checks detect the failure within 15 seconds and route 100% of calls to the surviving AZ (`ap-south-1b`). ECS automatically launches a replacement task in `ap-south-1a`. Zero dropped calls.

#### 2. Zero-Downtime Deployment & Automated Rollback
Implemented natively via ECS Deployment Circuit Breakers ([`terraform/ecs_fargate.tf`](terraform/ecs_fargate.tf)):
- `deployment_maximum_percent = 200`: Provisions a full set of new "Green" tasks before stopping any "Blue" tasks.
- `deployment_minimum_healthy_percent = 100`: Maintains 100% healthy capacity at all times.
- `deployment_circuit_breaker { enable = true, rollback = true }`: If the new revision fails container health checks or crashes on startup, ECS halts deployment and **automatically rolls back** to the last known-good task revision with zero downtime.

#### 3. `ap-south-1` (Mumbai) Regional & Regulatory Considerations
- **RBI Data Localization**: RBI Guidelines on Digital Lending mandate that borrower financial records, call audio, and recovery logs reside exclusively within India. Deploying in `ap-south-1` enforces strict territorial compliance.
- **Conversational Cadence (< 15ms Telephony Latency)**: Voice agents require natural turn-taking cadence. Delays exceeding 600ms introduce awkward pauses. Deploying in Mumbai provides sub-15ms round-trip transit to major Indian telecom aggregators (Exotel, Airtel, Tata Tele), leaving ample latency headroom for model inference.

---

### 2.5 Clean Infrastructure Teardown

To cleanly destroy all cloud resources and avoid ongoing AWS charges after evaluation:

```bash
cd terraform
terraform destroy -auto-approve
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

## Automated CI/CD Pipeline (GitHub Actions)

Every push or pull request to `main` executes our 4-stage pipeline in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):
1. **Stage 1 (Code Quality & Tests)**: `ruff` linting + unit test suite with coverage.
2. **Stage 2 (Security Scanning)**: Dependency vulnerability auditing (`pip-audit`), AST static analysis (`Bandit`), and secret leak detection (`TruffleHog`).
3. **Stage 3 (SonarQube Quality Gate)**: Static code analysis for maintainability and reliability.
4. **Stage 4 (Container Build & Trivy Scan)**: Docker build + Aqua Trivy image vulnerability scan + AWS ECR push.

---

## License

This project is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for complete terms.
