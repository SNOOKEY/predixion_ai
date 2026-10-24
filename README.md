# Predixion AI — Resilient Voice Collections Agent
### AWS Forward Deployment Engineer Challenge — Technical Submission

[![CI Pipeline](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Terraform: ap-south-1](https://img.shields.io/badge/IaC-Terraform-7B42BC.svg?logo=terraform&logoColor=white)](terraform/)

---

## Overview

Hi,

Here is my complete submission for the **Predixion AI Voice Collections Agent Challenge**.

### What This Solves
In debt collections (Credit Cards, BNPL, Personal Loans), dropped calls are fatal. Every dropped call erases borrower negotiation context, damages borrower trust, and violates regulatory compliance rules (RBI Fair Practices Code). 

When downstream voice pipelines (STT $\to$ LLM $\to$ TTS) experience transient degradations—specifically the **20% HTTP 503 error rate** and **lognormal latency spikes (p50 ~300ms, p90 ~700ms, p99 ~1.2s)** specified in the assignment PDF—an ordinary service crashes or drops customer calls.

This submission solves this end-to-end across **Task 1** and **Task 2**:
- **Task 1**: A resilient application layer with exponential backoff, circuit breaking, zero dropped calls via a Dead-Letter Queue (DLQ), and a clean model boundary to plug in real open-weight LLMs.
- **Task 2**: A production-grade AWS cloud deployment on ECS Fargate behind an ALB in `ap-south-1` (Mumbai), configured for Multi-AZ High Availability, automated rollback, and centralized AWS SQS DLQ storage.

---

## ⚡ Quick Evaluation: Test in Under 60 Seconds

You can evaluate the system right now either against the live AWS deployment or locally via Docker.

### 1. Test the Live AWS Cloud Deployment (Task 2)
The service is active on AWS in `ap-south-1` (Mumbai). You can test it immediately:

```bash
# Check service health (ALB -> Multi-AZ ECS Fargate)
curl http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/health

# Inspect the live AWS SQS Dead-Letter Queue
curl http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/dlq/stats

# Fire a 30-call batch through the live ALB (with 20% simulated downstream 503s)
python traffic_generator/load_tester.py --url http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com --quick --count 30

# Replay any dead letters from AWS SQS
curl -X POST http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/dlq/replay
```
*Live CloudWatch Dashboard*: [CollectionsAgent-Observability](https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#dashboards:name=CollectionsAgent-Observability)

---

### 2. Run Locally in One Command (Task 1)
No cloud account or local security scanner installations required:

```bash
# 1. Spin up Inference Service (:8000) and Mock Dependency (:8001)
docker compose up --build -d

# 2. Run verification traffic batch (30 calls)
python traffic_generator/load_tester.py --quick

# 3. Inspect stored calls & dead letters in terminal
python traffic_generator/load_tester.py --inspect
```

---

## Task 1: Resilient Inference Service & Zero-Loss Dead-Letter Queue

As per the assignment requirements, Task 1 is built around four specific conditions:

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
                      |  - Zero dropped calls (100% compliance capture)       |
                      |  - Fully inspectable (GET /dlq, GET /dlq/stats)       |
                      |  - Replayable (POST /dlq/replay)                      |
                      +-------------------------------------------------------+
```

### Condition 1: Downstream 20% 503 Errors & Latency Spikes
When the downstream mock returns 503 errors or experiences latency up to 1.2s, the client absorbs the faults using **Exponential Backoff with Full Randomized Jitter**:
$$\text{delay} = \min(\text{max\_delay}, \text{base\_delay} \times 2^{\text{attempt}}) + \text{uniform}(0, \text{jitter})$$
- Prevents retrying clients from hammering the service at identical intervals (thundering herds).
- Default configuration: `base_delay = 0.5s`, `max_delay = 4.0s`, `max_retries = 3`.

### Condition 2: Cascading Failure & Outage Protection
To prevent downstream outages from consuming all server threads and network sockets, I implemented a **Three-State Circuit Breaker** ([`inference_service/circuit_breaker.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/circuit_breaker.py)):
- **`CLOSED`**: Healthy operation. Requests flow downstream normally.
- **`OPEN`**: When 5 consecutive failures occur, the breaker trips. Incoming calls are short-circuited immediately to the Dead-Letter Queue in < 1ms without calling downstream.
- **`HALF_OPEN`**: After a 30-second cooldown, trial requests probe downstream health. If successful, the breaker resets to `CLOSED`; if they fail, it immediately returns to `OPEN`.

### Condition 3: Zero Dropped Calls (Dead-Letter Queue)
Dropping a call in collections violates regulatory audit rules. The service guarantees **zero unlogged drops**:
- Every failed or degraded call is persisted with `event_id`, `borrower_bucket`, `product_type`, `retry_count`, `failure_reason`, timestamps, and the complete uncorrupted payload.
- **Dual-Storage Engine**:
  - Local development uses SQLite at [`data/dead_letters.db`](file:///C:/Users/anazs/projects/predixion_ai/data/dead_letters.db).
  - Cloud production automatically switches to **AWS SQS** when `SQS_DLQ_URL` is configured.
- Exposes `POST /dlq/replay` to re-execute pending dead letters through the pipeline once downstream recovers.

### Condition 4: Traffic Replay Engine (Appendix C Curve)
Replays realistic BFSI campaign traffic curves:
- Baseline traffic: **10 calls/min**.
- Campaign spike: **50 calls/min**.
- Pre-populated with 500 realistic synthetic events across `0-30 DPD`, `31-60 DPD`, `61-90 DPD`, and `90+ DPD` buckets.

---

## How to Switch to a Real Open-Weight LLM

The assignment asks how the mock dependency can be swapped for a real open-weight model stack. 

I decoupled the resilience layer using an abstract interface [`BasePipelineClient`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/pipeline_client.py):

```python
class BasePipelineClient(ABC):
    @abstractmethod
    async def process_call(self, payload: dict) -> dict:
        """Process a call event through STT -> LLM -> TTS pipeline."""
        pass
```

### Switching to a Real GPU Model Stack:
To swap the HTTP mock for a real open-weight pipeline (e.g. running on an AWS `g5.xlarge` instance with an NVIDIA A10G GPU), simply create a concrete implementation:

```python
class OpenWeightPipelineClient(BasePipelineClient):
    def __init__(self, stt_client, vllm_client, tts_client):
        self.stt = stt_client      # faster-whisper (large-v3-turbo, ~200ms transcription)
        self.vllm = vllm_client    # vLLM serving Llama-3.1-8B-Instruct (AWQ 4-bit)
        self.tts = tts_client      # Piper TTS (~50ms low-latency speech synthesis)

    async def process_call(self, payload: dict) -> dict:
        # 1. Transcribe incoming caller voice audio
        transcript = await self.stt.transcribe(payload["audio_ref"])
        
        # 2. Generate conversational collections response with context
        response_text = await self.vllm.generate(
            prompt=f"Collections Agent for {payload['event']['product_type']}: {transcript}"
        )
        
        # 3. Synthesize voice audio response
        audio_out = await self.tts.synthesize(response_text)
        return {"transcript": transcript, "response": response_text, "audio": audio_out}
```

Because `ResilientPipelineClient` wraps `BasePipelineClient`, **you switch to real open-weight models without changing a single line of resilience code, circuit breaker logic, or DLQ capture**.

---

## Task 2: Cloud Infrastructure & AWS ECS Deployment

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

The production infrastructure is defined in Terraform under [`terraform/`](file:///C:/Users/anazs/projects/predixion_ai/terraform/):

1. **Multi-AZ VPC**: 2 public subnets (ALB & NAT Gateway) and 2 private subnets across `ap-south-1a` and `ap-south-1b`. Tasks run in private subnets with no public IPs.
2. **ECS Fargate Service**: Deploys the application with a sidecar pattern (FastAPI resilience layer on :8000 and downstream mock on :8001).
3. **Application Load Balancer (ALB)**: Public entry point on port 80 with health checks on `/health`.
4. **AWS SQS Dead-Letter Queue**: Distributed queue (`collections-dlq`) with 14-day retention for regulatory compliance.
5. **Autoscaling**:
   - Target tracking scales capacity when requests exceed 30 req/min per task.
   - Step scaling adds capacity when concurrent in-flight voice calls exceed 25.
6. **Least-Privilege IAM (Appendix F)**: Scoped strictly to ECR pulls, CloudWatch logs, and SQS actions (`SendMessage`, `ReceiveMessage`, `DeleteMessage`). No wildcard permissions.

---

## Seniority Track Stretch Objectives

### 1. Multi-AZ High Availability & Failure Simulation Playbook
- **Active Across 2 AZs**: Configured with `min_tasks = 2`, actively running across `ap-south-1a` and `ap-south-1b`.
- **Failure Simulation Playbook** ([`docs/seniority_architecture.md`](file:///C:/Users/anazs/projects/predixion_ai/docs/seniority_architecture.md#L38-L53)):
  ```bash
  # 1. Fetch task running in ap-south-1a
  TASK_ID=$(aws ecs list-tasks --cluster predixion-cluster --region ap-south-1 --query "taskArns[0]" --output text)
  # 2. Simulate sudden data center/AZ outage by terminating that task
  aws ecs stop-task --cluster predixion-cluster --task $TASK_ID --reason "Simulating ap-south-1a outage" --region ap-south-1
  ```
  **Result**: The ALB immediately detects the stopped task and routes 100% of traffic to `ap-south-1b`. ECS automatically launches a replacement task in `ap-south-1a`. Zero dropped calls.

### 2. Zero-Downtime Deployment & Automated Rollback
Implemented natively via ECS Deployment Circuit Breaker:
- `deployment_maximum_percent = 200`: Spins up new Green tasks before terminating old Blue tasks.
- `deployment_minimum_healthy_percent = 100`: Ensures 100% healthy capacity remains active at all times.
- `deployment_circuit_breaker { enable = true, rollback = true }`: If a new deployment fails container health checks or crashes on boot, ECS halts rollout and **automatically rolls back** to the last known healthy revision without dropping live calls.

### 3. `ap-south-1` (Mumbai) Regional & Regulatory Considerations
- **RBI Data Localization**: RBI Guidelines on Digital Lending mandate that borrower financial records, call audio, and recovery logs reside exclusively within Indian borders. Deploying in `ap-south-1` enforces strict territorial data residency.
- **Conversational Cadence (< 15ms Telephony Latency)**: Voice agents require natural turn-taking cadence. Delays exceeding 600ms introduce awkward pauses. Deploying in Mumbai provides sub-15ms round-trip transit to major Indian telecom gateways (Exotel, Airtel, Tata Tele), leaving ample latency headroom for model inference.

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

The repository includes a 4-stage GitHub Actions workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):
1. **Stage 1 (Code Quality & Tests)**: `ruff` linting + unit test suite with coverage.
2. **Stage 2 (Security Scanning)**: Dependency vulnerability auditing (`pip-audit`), AST static analysis (`Bandit`), and secret leak detection (`TruffleHog`).
3. **Stage 3 (SonarQube Quality Gate)**: Cloud quality gate analysis for code maintainability.
4. **Stage 4 (Container Build & Trivy Scan)**: Docker build + Aqua Trivy image CVE scanning + AWS ECR push.

---

## Infrastructure Teardown

To cleanly destroy all cloud resources and avoid ongoing AWS charges after evaluation:

```bash
cd terraform
terraform destroy -auto-approve
```

---

## License

This project is licensed under the **Apache License, Version 2.0**. See the [LICENSE](LICENSE) file for complete terms.
