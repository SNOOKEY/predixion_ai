# Predixion AI x TalentX — Open-Weight Collections Agent (Round 1: Local Track)

[![CI Pipeline](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SNOOKEY/predixion_ai/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: Bandit](https://img.shields.io/badge/security-bandit-black.svg)](https://github.com/PyCQA/bandit)
[![Vulnerability Scan: Trivy](https://img.shields.io/badge/vulnerability%20scan-trivy-1904DA.svg)](https://github.com/aquasecurity/trivy)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)

A resilient, compliant voice-agent inference service for BFSI collections portfolios (credit card, BNPL, personal loans). Built to withstand real-world downstream model degradation and transient outages without dropping call events or breaching compliance mandates.

---

## Architecture Overview

```
                      +-------------------------------------------------------+
                      |               Inference Service (:8000)               |
                      |                                                       |
  Call Events         |   +-----------------------------------------------+   |
 [Appendix A] ------> |   |         ResilientPipelineClient               |   |
 (Traffic Gen / Load) |   |  - Exponential Backoff + Randomized Jitter   |   |
                      |   |  - Circuit Breaker (CLOSED / OPEN / HALF-OPEN)|   |
                      |   +-----------------------+-----------------------+   |
                      |                           |                           |
                      |                           | Calls boundary            |
                      |                           v                           |
                      |   +-----------------------------------------------+   |
                      |   |          BasePipelineClient (Boundary)        |   |
                      |   +-----------------------+-----------------------+   |
                      +---------------------------|---------------------------+
                                                  | HTTP POST /pipeline/process
                                                  v
                      +-------------------------------------------------------+
                      |             Mock Downstream Pipeline (:8001)          |
                      |  - 20% Injected HTTP 503 Transients                   |
                      |  - Lognormal Latency (p50~300ms, p90~700ms, p99~1.2s) |
                      +-------------------------------------------------------+
                                                  |
                                                  | On Retry Exhaustion / Trip
                                                  v
                      +-------------------------------------------------------+
                      |         Dead-Letter Queue (data/dead_letters.db)      |
                      |  - Zero dropped calls (Compliance guaranteed)         |
                      |  - Fully inspectable (GET /dlq, GET /dlq/stats)       |
                      |  - Replayable (POST /dlq/replay, /dlq/replay/{id})    |
                      +-------------------------------------------------------+
```

---

## Key Features

1. **Resilience Layer & Circuit Breaking**:
   - Implements **Exponential Backoff with Full Jitter** (`delay = min(max_delay, base_delay * (2^attempt)) + uniform(0, jitter)`) to prevent thundering herds against the downstream pipeline.
   - **Three-State Circuit Breaker (`CLOSED`, `OPEN`, `HALF_OPEN`)**: Fast-fails incoming calls during persistent outages without wasting network socket capacity, and safely probes for recovery with configurable half-open trials.
2. **Compliance-Guaranteed Dead-Letter Queue (DLQ)**:
   - Failed or degraded calls land in a persistent SQLite store (`data/dead_letters.db`).
   - Every dropped call in collections creates a regulatory compliance risk. This DLQ guarantees zero event loss, recording failure reasons, retry counts, timestamps, and full payloads.
   - Inspectable via REST API (`GET /dlq`, `GET /dlq/stats`) and replayable (`POST /dlq/replay`).
3. **Model-Agnostic Boundary**:
   - `BasePipelineClient` abstract interface isolates resilience logic from inference execution. Swapping the mock for real open-weight models requires zero changes to retry or circuit-breaker policies.
4. **Traffic Replay Engine & Appendix A Schema**:
   - Includes synthetic dataset generator (~500 records with realistic DPD buckets and outcomes) and a schedule runner simulating Appendix C traffic curves (10 cpm baseline $\to$ 50 cpm campaign spike).

---

## Quickstart & Execution

### Prerequisites
- Python 3.10+ installed (or Docker / Docker Compose)
- **Zero local evaluator overhead**: Evaluators **do NOT need to install Trivy, SonarQube, Bandit, or scanner binaries** locally. All security sniffing, vulnerability checks, and container image scans run automatically in **GitHub Actions CI** on push/merge.
- No cloud account or external credentials required (Zero-Secret compliance)

### Option 1: Local Python Execution

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run automated resilience tests**:
   ```bash
   python -m unittest tests/test_resilience.py
   ```

3. **Start the Mock Downstream Pipeline** (Terminal 1):
   ```bash
   python -m uvicorn mock_dependency.main:app --host 0.0.0.0 --port 8001
   ```

4. **Start the Inference Service** (Terminal 2):
   ```bash
   python -m uvicorn inference_service.main:app --host 0.0.0.0 --port 8000
   ```

5. **Generate Synthetic Call Dataset** (Terminal 3):
   ```bash
   python traffic_generator/generate_dataset.py
   ```

6. **Run Traffic Load & Resilience Benchmark**:
   ```bash
   # Quick 30-call verification test
   python traffic_generator/load_tester.py --quick

   # Inspect all historical calls and DLQ records stored in SQLite
   python traffic_generator/load_tester.py --inspect

   # Or run Appendix C 24-hour simulation with time compression (e.g. 360x)
   python traffic_generator/load_tester.py --hours 24 --compression 360
   ```

---

### Option 2: Docker Compose (One Command)

```bash
docker compose up --build
```
- `mock_dependency` runs at `http://localhost:8001`
- `inference_service` runs at `http://localhost:8000`
- SQLite volume mapped to `./data`

---

## Viewing Stored Events & Dead Letters

All processed events and dead letters persist permanently in [`data/dead_letters.db`](file:///C:/Users/anazs/projects/predixion_ai/data/dead_letters.db). You can inspect them in three ways:

1. **Terminal CLI Inspector**:
   ```bash
   python traffic_generator/load_tester.py --inspect
   ```
2. **Web Browser / REST API**:
   - `http://localhost:8000/calls` — Browse all historical call audit logs across sessions.
   - `http://localhost:8000/calls/summary` — Aggregate call volume, success counts, and average latency.
   - `http://localhost:8000/dlq` — Full details of all failed calls landed in the Dead-Letter Queue.
   - `http://localhost:8000/dlq/stats` — Summary breakdown of dead letters and failure reasons.
   - `http://localhost:8000/metrics` — Combined active session and cumulative database metrics.
3. **SQLite CLI / DB Browser**:
   ```bash
   sqlite3 data/dead_letters.db "SELECT event_id, status, outcome, latency_ms FROM call_audit_log LIMIT 10;"
   ```

---

## API Documentation & DLQ Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/calls/process` | `POST` | Ingests a call event; executes resilient pipeline or degrades to DLQ. |
| `/calls` | `GET` | View historical audit logs of all past calls stored in SQLite. |
| `/calls/summary` | `GET` | Aggregate historical call volume and success metrics. |
| `/dlq` | `GET` | Inspect stored dead letters (filter by `status`, `campaign_id`, `limit`, `offset`). |
| `/dlq/stats` | `GET` | Get total, pending, and replayed counts with failure reason breakdown. |
| `/dlq/replay` | `POST` | Replays all pending dead letters through the resilient pipeline. |
| `/dlq/replay/{event_id}`| `POST` | Replays a specific dead-letter event by ID. |
| `/circuit-breaker/status`| `GET` | Inspect circuit breaker state, trips count, and consecutive failure tallies. |
| `/circuit-breaker/reset` | `POST` | Administratively reset circuit breaker to `CLOSED`. |
| `/health` | `GET` | Service readiness and health probe. |
| `/metrics` | `GET` | Call volume, success rate, degraded rate, and DLQ counters. |

---

## How to Swap the Mock for a Real Open-Weight Model

To swap the mocked dependency for a production open-weight stack without touching the resilience layer, implement the `BasePipelineClient` interface defined in [`inference_service/pipeline_client.py`](file:///C:/Users/anazs/projects/predixion_ai/inference_service/pipeline_client.py). For instance, an `OpenWeightPipelineClient` connects to an on-instance or microservice stack comprising:
1. **STT**: `faster-whisper` (`large-v3-turbo` with CTranslate2 for sub-250ms transcription on GPU).
2. **LLM**: `Mistral-7B-Instruct-v0.3` or `Llama-3.1-8B-Instruct` served via **vLLM** with int8/AWQ quantization for low-latency streaming completions.
3. **TTS**: `Piper` (lightweight, deterministic ~50ms audio synthesis) or `XTTS-v2`.

Because `ResilientPipelineClient` wraps any subclass of `BasePipelineClient`, the circuit breaking, backoff retries, and SQLite dead-letter capturing operate identically regardless of whether the underlying target is the HTTP mock or real local GPU model inference.

---

## Automated CI/CD Pipeline (GitHub Actions)

This repository includes a multi-stage, gated CI pipeline in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) that executes automatically upon code push or pull request to `main`:

```
[Push / Merge to main]
          │
          ▼
┌───────────────────────────────────────────────┐
│ Stage 1: code-quality-and-test                │
│ • Ruff code formatting & linting checks       │
│ • Pytest unit & resilience tests with coverage│
└───────────────────────┬───────────────────────┘
                        │ Gate 1: PASS
                        ▼
┌───────────────────────────────────────────────┐
│ Stage 2: vulnerability-security-scan          │
│ • pip-audit: Dependency CVE sniffing (SCA)    │
│ • Bandit: AST static security sniffing (SAST) │
│ • TruffleHog: Secret leak sniffing            │
└───────────────────────┬───────────────────────┘
                        │ Gate 2: PASS
                        ▼
┌───────────────────────────────────────────────┐
│ Stage 3: sonarqube-analysis                   │
│ • SonarQube Scanner static code analysis      │
│ • Quality Gate verification                   │
└───────────────────────┬───────────────────────┘
                        │ Gate 3: PASS
                        ▼
┌───────────────────────────────────────────────┐
│ Stage 4: container-image-build                │
│ • Docker Buildx multi-tag container build     │
│ • Aqua Trivy: Image OS/library CVE scanning   │
│ • Ephemeral container health smoke test       │
└───────────────────────────────────────────────┘
```

> **Evaluator Convenience**: Evaluators do **not** need to install Docker security tools (Trivy), SonarQube servers, or SAST scanners locally. Simply run the project with Python or Docker Compose; GitHub Actions handles all enterprise security gating in the cloud.

---

## Cost Reasoning & Sizing Writeup

See the complete half-page AWS Fargate sizing and cost analysis in [cost_estimate.md](file:///C:/Users/anazs/projects/predixion_ai/cost_estimate.md), which breaks down **Config A (1 vCPU / 2GB)** vs **Config B (0.5 vCPU / 1GB)** in `ap-south-1`, evaluates fixed NAT/ALB overheads, and justifies the recommended architecture.
