# Runbook: Collections Voice-Agent Inference Service

> **Severity**: P1 / P2 Incident Response  
> **Service**: `collections-inference-service` (ECS Fargate, `ap-south-1`)  
> **Compliance Context**: BFSI collections calls must be logged and retried; silent drops constitute a regulatory compliance failure.

---

## 1. Trigger
**Alert Condition**: `p99 latency > 3x baseline (~3600ms)` or `5XX Error Rate > 5%` during an active campaign window (08:00–10:00 or 14:00–16:00 IST).

---

## 2. First 5 Minutes Triage Checklist

1. **Check CloudWatch Dashboard (`CollectionsAgent-Observability`)**:
   - Inspect **Autoscaling & Task Count**: Is the running task count scaling up from 1 to 5 to match the traffic ramp, or is it lagging behind desired capacity?
   - Inspect **ALB Request Rate**: Verify incoming call volume matches expected campaign curve (10 $\to$ 50 calls/min).

2. **Check Downstream Dependency Health**:
   - Inspect **HTTP 5XX vs Injected 20%** widget.
   - If downstream error rate $> 20\%$, downstream model service (STT $\to$ LLM $\to$ TTS) is experiencing a brownout or hard failure.

3. **Check Circuit-Breaker State**:
   - Verify whether the circuit breaker in `inference_service` is in **`OPEN`** state.
   - **`OPEN` (Good)**: Circuit breaker tripped as designed, fast-failing calls directly into the Dead-Letter Queue (DLQ) without tying up worker threads or network sockets.
   - **`CLOSED` & Retrying (Bad)**: Indicates circuit breaker threshold is too lenient or misconfigured, exacerbating latency and risking thread starvation.

---

## 3. Diagnostic & Remediation Workflows

### Scenario A: Downstream Dependency Outage (STT / LLM / TTS Failure)
- **Immediate Action**:
  1. Confirm circuit breaker state via `/health` endpoint or ECS container logs:
     ```bash
     aws logs filter-log-events \
       --log-group-name /ecs/collections-inference \
       --filter-pattern "CIRCUIT_BREAKER_OPEN" \
       --region ap-south-1
     ```
  2. Verify Dead-Letter Queue (DLQ) depth is increasing:
     ```bash
     aws sqs get-queue-attributes \
       --queue-url <SQS_DLQ_URL> \
       --attribute-names ApproximateNumberOfMessages \
       --region ap-south-1
     ```
  3. Ensure no calls are silently dropped. All failed call records must exist with valid event payloads, timestamps, and error codes for compliance replay.
  4. If running on GPU instance (`g5.xlarge`), check GPU memory and model service logs:
     ```bash
     nvidia-smi
     docker logs model-worker
     ```

### Scenario B: Autoscaling Lag or Task Failure
- **Immediate Action**:
  1. Check task Desired Count vs. Running Count in ECS:
     ```bash
     aws ecs describe-services \
       --cluster predixion-cluster \
       --services collections-inference-service \
       --region ap-south-1
     ```
  2. If tasks fail to launch:
     - Check **Service Quotas**: Has Fargate vCPU limit been reached?
     - Check **VPC Subnets**: Check available IPs in private subnets (`10.0.10.0/24`, `10.0.11.0/24`).
     - Check **Task Definition / ECR**: Are container pulls failing with `ImagePullBackOff`?
  3. If scaling is too sluggish for the 15-minute ramp:
     - Reduce scale-out cooldown in `terraform/autoscaling.tf` from 60s to 30s.
     - Temporarily adjust manual desired count:
       ```bash
       aws ecs update-service \
         --cluster predixion-cluster \
         --services collections-inference-service \
         --desired-count 5 \
         --region ap-south-1
       ```

---

## 4. Escalation Matrix

| Time Unresolved | Role / Team | Action |
| :--- | :--- | :--- |
| **0 – 10 minutes** | Primary On-Call FDE | Initial triage via CloudWatch, check circuit breaker & DLQ. |
| **10 – 20 minutes**| Lead Infrastructure Engineer | Evaluate ECS task scaling, NAT gateway egress, and GPU health. |
| **> 20 minutes**   | Engineering Head & Portfolio Lead | Inform BFSI client operations; switch campaign dialer to fallback dial rate. |

---

## 5. Post-Incident & Recovery Procedures

1. **Replay DLQ**:
   - Once downstream pipeline recovers and circuit breaker resets to `CLOSED`, initiate DLQ replay:
     ```bash
     # Local SQLite DLQ Replay:
     curl -X POST http://<SERVICE_URL>/dlq/replay

     # Or trigger batch replay consumer for SQS DLQ
     python -m inference_service.dlq.replay_sqs --batch-size 50
     ```
2. **Compliance Verification**:
   - Validate that 100% of failed call events in the campaign window have either been successfully re-processed or marked with terminal regulatory outcomes (`dispute`, `no_answer`, `ptp_taken`).
3. **Telemetry & Calibration**:
   - Compare actual p50/p90/p99 latency against baseline expectations.
   - Adjust circuit breaker failure rate threshold (default: 50% over rolling window) or backoff parameters if necessary.
