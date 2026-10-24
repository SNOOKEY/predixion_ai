# Predixion AI — Resilient Voice Collections Agent
### Candidate Submission Brief — AWS Forward Deployment Engineer Track (Round 1 & Round 2)

[![CI Pipeline](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: Bandit](https://img.shields.io/badge/security-bandit-black.svg)](https://github.com/PyCQA/bandit)
[![Vulnerability Scan: Trivy](https://img.shields.io/badge/vulnerability%20scan-trivy-1904DA.svg)](https://github.com/aquasecurity/trivy)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)
[![Terraform: ap-south-1](https://img.shields.io/badge/IaC-Terraform-7B42BC.svg?logo=terraform&logoColor=white)](terraform/)

---

## Candidate Overview: Engineering Summary

Hi team,

Here is my complete, production-ready submission for the **Predixion AI Voice Collections Agent Challenge**. 

In high-volume BFSI debt collections (Credit Cards, BNPL, Personal Loans), call drops are costly and hazardous: they damage borrower trust, erase in-flight negotiation context, and create severe regulatory compliance violations under RBI Fair Practices directives. 

When a downstream voice pipeline (STT $\to$ LLM $\to$ TTS) experiences transient degradation—such as the 20% HTTP 503 error rate and lognormal latency spikes (p50 ~300ms, p90 ~700ms, p99 ~1.2s) specified in the brief—a naive inference service fails callers and drops data. 

I designed this solution as a unified, two-round system:
1. **Task 1 (Core Resilience & Boundary)**: An application layer that absorbs downstream failures through exponential backoff with randomized jitter, trips a three-state circuit breaker to prevent thundering herds, and captures 100% of unrecoverable calls into a persistent Dead-Letter Queue (DLQ).
2. **Task 2 (Cloud Production & Seniority Track)**: Deploying this service onto **AWS ECS Fargate Multi-AZ** in `ap-south-1` (Mumbai) with native rolling Blue/Green automated rollback, centralized AWS SQS DLQ persistence, least-privilege IAM, and an end-to-end automated GitHub Actions CI/CD pipeline.

Below is the complete walkthrough of how each requirement was met, how the model boundary was abstracted, how the cloud infrastructure was architected, and how you can run and evaluate it immediately.

---

## ⚡ Quick Evaluation: Test It in Under 60 Seconds

You can evaluate this submission in one of two ways:

### Option A: Test My Live AWS Deployment (`ap-south-1`)
The production infrastructure is currently live on AWS Mumbai. You can run these commands directly against the active Application Load Balancer:

```bash
# 1. Health & circuit breaker state
curl http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/health

# 2. Inspect the live AWS SQS Dead-Letter Queue
curl http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/dlq/stats

# 3. Fire a 30-call traffic batch through the live ALB
python traffic_generator/load_tester.py --url http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com --quick --count 30

# 4. Replay dead letters directly from AWS SQS
curl -X POST http://predixion-alb-2128714305.ap-south-1.elb.amazonaws.com/dlq/replay
```
*Live CloudWatch Observability Dashboard*: [CollectionsAgent-Observability (ap-south-1)](https://ap-south-1.console.aws.amazon.com/cloudwatch/home?region=ap-south-1#dashboards:name=CollectionsAgent-Observability)

---

### Option B: Run Locally in One Command (Docker Compose)
Evaluators do **not** need cloud credentials, AWS setup, or local security scanners. Everything spins up locally:

```bash
# 1. Start Inference Service (:8000) and Mock Dependency (:8001)
docker compose up --build -d

# 2. Run quick verification test (30 calls with 20% injected failure)
python traffic_generator/load_tester.py --quick

# 3. View stored call records and DLQ entries in your terminal
python traffic_generator/load_tester.py --inspect
```

---

## Task 1: How the Resilience Layer & Zero-Drop DLQ Were Built

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

### 1. Exponential Backoff with Full Randomized Jitter
To prevent retrying clients from hammering recovering downstream services at synchronized intervals (thundering herds), I implemented the AWS Full Jitter algorithm:
$$\text{sleep} = \min(\text{max\_delay}, \text{base\_delay} \times 2^{\text{attempt}}) + \text{uniform}(0, \text{jitter})$$
- Default settings: `base_delay = 0.5s`, `max_delay = 4.0s`, `max_retries = 3`.
- Retries trigger exclusively on retryable status codes (`503 Service Unavailable`, `504 Gateway Timeout`, or socket timeouts), while 4xx client errors fast-fail immediately.

### 2. Three-State Circuit Breaker State Machine
Implemented in [`inference_service/circuit_breaker.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/circuit_breaker.py):
- **`CLOSED`**: All requests flow downstream. Consecutive 503/timeout failures increment an internal counter.
- **`OPEN`**: When consecutive failures hit the threshold (5), the breaker trips. Incoming calls are short-circuited in < 1ms directly to the Dead-Letter Queue without wasting downstream sockets.
- **`HALF_OPEN`**: After a 30-second cooldown, the breaker cautiously admits trial requests. If successful, it automatically heals back to `CLOSED`; if they fail, it immediately trips back to `OPEN`.

### 3. Zero-Loss Dead-Letter Queue (DLQ)
In collections, an unrecorded dropped call means a lost promise-to-pay (PTP) or an unnotified borrower.
- **Dual-Storage Engine**:
  - **Local Development**: Stored in a resilient SQLite database at [`data/dead_letters.db`](file:///C:/Users/anazs/projects/predixion_ai/data/dead_letters.db).
  - **Cloud Production**: Switches seamlessly to **AWS SQS** when `SQS_DLQ_URL` is set, storing events with a 14-day compliance retention period.
- **Audit Logging**: Every record captures `event_id`, `borrower_bucket`, `product_type`, `failure_reason`, `retry_count`, `created_at`, and the complete uncorrupted payload.
- **Automated Replay**: Exposes `POST /dlq/replay` to poll, reprocess, and delete dead letters once the downstream recovers.

---

## How to Switch from the Mock to a Real Open-Weight LLM Stack

As required by the challenge prompt, the resilience layer is **strictly decoupled** from the downstream execution logic via the abstract `BasePipelineClient` interface in [`inference_service/pipeline_client.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/pipeline_client.py):

```python
class BasePipelineClient(ABC):
    @abstractmethod
    async def process_call(self, payload: dict) -> dict:
        """Process a call event through STT -> LLM -> TTS pipeline."""
        pass
```

### Switching to a Real Open-Weight GPU Stack:
To plug in a real production model stack (e.g., deployed on an AWS `g5.xlarge` instance with 24GB A10G GPU), simply create a concrete implementation of `BasePipelineClient`:

```python
class OpenWeightPipelineClient(BasePipelineClient):
    def __init__(self, stt_client, vllm_client, tts_client):
        self.stt = stt_client      # faster-whisper (large-v3-turbo, ~200ms)
        self.vllm = vllm_client    # vLLM serving Llama-3.1-8B-Instruct (AWQ quantized)
        self.tts = tts_client      # Piper TTS (~50ms deterministic voice synthesis)

    async def process_call(self, payload: dict) -> dict:
        # 1. Transcribe borrower voice audio
        transcript = await self.stt.transcribe(payload["audio_ref"])
        
        # 2. Stream collections prompt through local vLLM server
        response_text = await self.vllm.generate(
            prompt=f"Collections Agent for {payload['event']['product_type']}: {transcript}"
        )
        
        # 3. Synthesize natural response speech
        audio_out = await self.tts.synthesize(response_text)
        return {"transcript": transcript, "response": response_text, "audio": audio_out}
```

Because `ResilientPipelineClient` wraps `BasePipelineClient`, **zero lines of code** in the retry engine, circuit breaker, metrics collector, or dead-letter queue need to change when swapping from the mock to real open-weight models.

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

The cloud architecture was provisioned via Terraform in [`terraform/`](file:///C:/Users/anazs/projects/predixion_ai/terraform/):

1. **Multi-AZ VPC**: 2 public subnets (ALB & NAT Gateway) and 2 private subnets across `ap-south-1a` and `ap-south-1b`. Tasks have no public IPs.
2. **ECS Fargate Service**: Runs a sidecar container pattern:
   - Container 1: `inference-service` (FastAPI resilience layer on port 8000)
   - Container 2: `mock-dependency` (Downstream mock pipeline on port 8001)
3. **Application Load Balancer (ALB)**: Routes public incoming traffic on port 80 with active health checking on `/health`.
4. **AWS SQS Dead-Letter Queue**: Queue `collections-dlq` receives unrecoverable calls. 14-day retention satisfies compliance dispute windows.
5. **Autoscaling Target Tracking**:
   - Maintains a target of 30 requests/target/minute.
   - Custom Step Scaling policy triggers additional capacity when in-flight voice calls spike above 25.
6. **Least-Privilege IAM (Appendix F)**:
   - Task Role permissions are strictly scoped to `collections-dlq` (`sqs:SendMessage`, `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`) and CloudWatch scoped metric writes. Zero wildcards (`*`) used for ECS, IAM, or S3.

---

## Seniority Track Stretch Objectives

### 1. Multi-AZ High Availability & Failure Simulation Playbook
- **Baseline Deployment**: Configured with `min_tasks = 2`, running actively across both `ap-south-1a` and `ap-south-1b`.
- **Failure Simulation Playbook** ([`docs/seniority_architecture.md`](file:///C:/Users/anazs/projects/predixion_ai/docs/seniority_architecture.md#L38-L53)):
  ```bash
  # 1. Fetch task running in ap-south-1a
  TASK_ID=$(aws ecs list-tasks --cluster predixion-cluster --region ap-south-1 --query "taskArns[0]" --output text)
  # 2. Simulate sudden data center/AZ outage by terminating that task
  aws ecs stop-task --cluster predixion-cluster --task $TASK_ID --reason "Simulating ap-south-1a outage" --region ap-south-1
  ```
  **Result**: ALB health checks instantly shift 100% of traffic to the task in `ap-south-1b`. ECS automatically launches a replacement task in `ap-south-1a`. Zero dropped calls.

### 2. Zero-Downtime Deployment & Automated Rollback
Implemented natively via ECS Deployment Circuit Breaker:
- `deployment_maximum_percent = 200`: Spins up a complete set of new "Green" containers alongside active "Blue" containers before stopping old ones.
- `deployment_minimum_healthy_percent = 100`: Guarantees 100% healthy capacity throughout deployments.
- `deployment_circuit_breaker { enable = true, rollback = true }`: If the new revision fails container startup or ALB `/health` checks, ECS halts rollout and **automatically rolls back** to the previous stable task revision.

### 3. `ap-south-1` (Mumbai) Regional & Regulatory Considerations
- **RBI Data Localization**: RBI Guidelines on Digital Lending mandate that borrower financial records, call audio, and recovery logs reside exclusively within India. Deploying in `ap-south-1` enforces strict territorial compliance.
- **Conversational Cadence (< 15ms Telephony Latency)**: Voice collections requires seamless turn-taking cadence. Delays over 600ms cause unnatural awkward pauses. Operating in Mumbai provides sub-15ms round-trip transit to Indian telecom gateways (Exotel, Airtel, Tata Tele), leaving ample headroom for the inference pipeline.

---

## Local Developer Quickstart

```bash
# 1. Clone repository & install dependencies
git clone https://github.com/SNOOKEY/predixion_ai.git
cd predixion_ai
pip install -r requirements.txt

# 2. Run unit & integration test suite (12 tests)
python -m unittest discover tests

# 3. Start both services in separate terminals
python -m uvicorn mock_dependency.main:app --host 0.0.0.0 --port 8001
python -m uvicorn inference_service.main:app --host 0.0.0.0 --port 8000

# 4. Run traffic benchmark
python traffic_generator/load_tester.py --quick
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

The repository includes a 4-stage GitHub Actions workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):
1. **Stage 1**: `ruff` code formatting & Pytest unit test coverage.
2. **Stage 2**: Security vulnerability auditing (`pip-audit`, `Bandit` SAST, `TruffleHog` secrets).
3. **Stage 3**: SonarQube Quality Gate analysis.
4. **Stage 4**: Docker multi-tag build + Aqua Trivy container vulnerability scan + AWS ECR push.

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
