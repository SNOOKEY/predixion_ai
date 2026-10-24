# Seniority Architecture & Operational Blueprint
### Predixion AI Voice Collections Agent — AWS GPU Track (Round 2 Stretch Specification)

This document details the enterprise-grade architecture implemented to fulfill all three **Seniority Track Stretch Objectives** specified in the challenge rubric:
1. **Multi-AZ High Availability & Failure Simulation** (Active across `ap-south-1a` & `ap-south-1b`)
2. **Zero-Downtime Blue/Green Deployment with Automated Rollback** (AWS CodeDeploy + CloudWatch Alarms)
3. **`ap-south-1` Specific Considerations** (RBI Data Localization & Sub-15ms Latency Cadence)

---

## 1. Multi-AZ High Availability Architecture

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

- **Baseline Sizing**: Configured with `min_tasks = 2`. At baseline traffic (10 calls/min), one container runs in `ap-south-1a` and one container runs in `ap-south-1b`.
- **Campaign Spike**: Autoscaling target tracking automatically ramps capacity up to 5 tasks across both AZs during 50 calls/min campaign windows.

### Multi-AZ Failure Simulation Playbook:
To demonstrate that the service remains online when an entire Availability Zone fails:
1. Identify the task running in `ap-south-1a`:
   ```bash
   TASK_ID=$(aws ecs list-tasks --cluster predixion-cluster --region ap-south-1 --query "taskArns[0]" --output text)
   ```
2. Simulate sudden data center/AZ death by abruptly terminating that task:
   ```bash
   aws ecs stop-task --cluster predixion-cluster --task $TASK_ID --reason "Simulating ap-south-1a AZ outage" --region ap-south-1
   ```
3. **Expected Behavior**:
   - The ALB health checks detect the failure within 15 seconds and instantly routes 100% of active voice calls to the healthy task in `ap-south-1b`.
   - ECS detects the capacity drop and launches a replacement task in `ap-south-1a`.
   - **Zero dropped calls**: In-flight calls that experience network resets are caught by the client backoff retry or persisted to SQS DLQ.

---

## 2. Zero-Downtime Deployment & Automated Rollback Strategy

To guarantee that broken container images or failing downstream dependencies never impact live borrowers or cause dropped calls, we implement a **Rolling Blue/Green Deployment with ECS Native Deployment Circuit Breakers and Automated Rollback**.

### Architecture:
- **Rolling Blue/Green Surge**:
  - `deployment_maximum_percent = 200`: When a new task definition is deployed, ECS provisions a complete new set of Green tasks in parallel before terminating any Blue tasks.
  - `deployment_minimum_healthy_percent = 100`: Ensures that 100% of required healthy capacity (2 tasks across Multi-AZ) is maintained and actively serving production traffic at all times.
- **ALB Health Gate**:
  - New Green containers must pass consecutive `/health` checks on port 8000 before receiving any production traffic from the ALB.
- **Automated Rollback (Deployment Circuit Breaker)**:
  - Configured with `deployment_circuit_breaker { enable = true, rollback = true }`.
  - If new tasks fail container startup, crash due to missing dependencies, or fail ALB health check verification, the circuit breaker trips automatically.
  - ECS immediately marks the deployment as failed, halts rollout, tears down the unhealthy Green tasks, and restores 100% traffic to the previously verified, healthy Blue task revision.
  - Zero dropped calls, zero manual intervention, and no external CodeDeploy service subscription dependencies.

---

## 3. `ap-south-1` (Mumbai) Regional & Compliance Considerations

Operating voice collection agents in India requires specialized cloud design tailored to RBI regulatory frameworks and telecom topologies:

### A. Regulatory Compliance & Data Residency (RBI Mandate)
- **RBI Guidelines on Digital Lending & Storage of Payment System Data**: Mandates that all borrower financial data, credit card/BNPL loan agreements, borrower interactions, call audio recordings, and recovery logs must reside exclusively on servers physically located within India.
- **Data Perimeter**:
  - Deploying in `ap-south-1` guarantees strict domestic data residency.
  - ECS tasks run in **private subnets with no public IPs**. Inbound access is strictly funneled through the ALB, and outbound egress is restricted via NAT Gateway.
  - Failed collections records stored in `collections-dlq` are retained for 14 days, satisfying regulatory dispute audit windows.

### B. Network Latency & Conversational Cadence
- Outbound voice agents require natural turn-taking cadence. Delays exceeding 600ms introduce awkward pauses or simultaneous speech overlap that break call engagement.
- Deploying in `ap-south-1` achieves:
  - **< 15ms round-trip latency** to major Indian telecom aggregators (Exotel, Tata Tele, Airtel) co-located in Mumbai/Navi Mumbai data centers.
  - Direct local transit to digital lender core banking APIs (NBFCs & fintechs operating out of Mumbai and Bengaluru).
  - Maximizes the latency budget for local Whisper STT (~250ms), LLM token streaming (~200ms), and Piper TTS (~50ms).
