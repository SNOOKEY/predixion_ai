# AWS Fargate Cost Estimation & Sizing Trade-Off Analysis

**Target Region:** `ap-south-1` (Asia Pacific - Mumbai)  
**Workload Profile:** 24-hour cycle repeating daily  
- **Baseline (20 hours/day):** Flat 10 calls/min  
- **Campaign Windows (4 hours/day total: 08:00–10:00 & 14:00–16:00):** Spike to 50 calls/min  
- **Average duration per call:** ~60 seconds (concurrency = $\sim 10$ baseline, $\sim 50$ peak)

---

## 1. Unit Pricing Benchmarks (`ap-south-1` On-Demand)
- **Fargate vCPU:** \$0.0456 / vCPU-hour
- **Fargate Memory:** \$0.0050 / GB-hour
- **Application Load Balancer (ALB):** \$0.0252 / hour + \$0.008 / LCU-hour
- **NAT Gateway:** \$0.0450 / hour + \$0.0450 / GB data processed

---

## 2. Configuration Comparison

### Config A: 1 vCPU / 2 GB RAM (Scale 1 $\to$ 5 Tasks)
- **Unit Task Cost:** $(1 \times 0.0456) + (2 \times 0.0050) = \mathbf{\$0.0556 \text{ / task-hour}}$
- **Baseline Compute (20 hrs/day @ 1 task):**  
  $20 \times 1 \times \$0.0556 = \mathbf{\$1.112 \text{ / day}}$
- **Campaign Spike Compute (4 hrs/day @ average 3.2 tasks during ramp & hold):**  
  $4 \times 3.2 \times \$0.0556 = \mathbf{\$0.712 \text{ / day}}$
- **Total Compute Cost:** $(\$1.112 + \$0.712) \times 30.5 \text{ days} = \mathbf{\$55.63 \text{ / month}}$

### Config B: 0.5 vCPU / 1 GB RAM (Scale 1 $\to$ 10 Tasks)
- **Unit Task Cost:** $(0.5 \times 0.0456) + (1 \times 0.0050) = \mathbf{\$0.0278 \text{ / task-hour}}$
- **Baseline Compute (20 hrs/day @ 1 task minimum floor):**  
  $20 \times 1 \times \$0.0278 = \mathbf{\$0.556 \text{ / day}}$
- **Campaign Spike Compute (4 hrs/day @ average 6.4 tasks):**  
  $4 \times 6.4 \times \$0.0278 = \mathbf{\$0.712 \text{ / day}}$
- **Total Compute Cost:** $(\$0.556 + \$0.712) \times 30.5 \text{ days} = \mathbf{\$38.67 \text{ / month}}$

---

## 3. Fixed vs. Variable Overhead Infrastructure

| Infrastructure Component | Monthly Cost | Cost Characteristic |
| :--- | :--- | :--- |
| **Application Load Balancer** | \$18.45 (base) + \$4.00 (LCUs) $\approx$ **\$22.45** | Fixed connection routing overhead |
| **NAT Gateway (Single AZ)** | \$32.85 (base) + \$15.00 (data processing) $\approx$ **\$47.85** | Fixed egress hourly + throughput data charge |
| **Amazon CloudWatch & Logs** | **\$4.50** | Metrics, custom namespaces & log retention |
| **Total Overhead** | **$\approx \$74.80 \text{ / month}$** | **Dominates total expenditure ($\sim 60\text{--}65\%$)** |

**Total Monthly Spend Estimate:**
- **Config A:** $\$55.63 \text{ (Compute)} + \$74.80 \text{ (Overhead)} \approx \mathbf{\$130.43 \text{ / month}}$
- **Config B:** $\$38.67 \text{ (Compute)} + \$74.80 \text{ (Overhead)} \approx \mathbf{\$113.47 \text{ / month}}$

---

## 4. Architectural Trade-Off Analysis & Recommendation

### Trade-Off Breakdown
1. **Compute vs. Fixed Overhead Paradox:** Compute represents only $33\%\text{--}42\%$ of the total bill. Over-optimizing task vCPU yields modest gross savings ($\sim \$16.96/\text{month}$) because NAT Gateway and ALB represent the immutable fixed floor for private subnet deployments.
2. **Cold Start & Scaling Granularity:** Config B allows smaller, stepwise scale-out increments. However, provisioning 5 additional tasks concurrently takes 45–90 seconds on Fargate. Under rapid 15-minute campaign ramps ($10 \to 50$ calls/min), Config A requires spinning up only 4 tasks, reaching stable capacity with fewer ECS scheduler operations.
3. **Python Runtime Headroom & GIL Contention:** An async Python voice inference service with JSON serialization, SQLite/connection pooling, and circuit breaker synchronization operates substantially better with 1 dedicated vCPU. A 0.5 vCPU allocation experiences high CPU throttles under I/O bursts, which inflates $p99$ response latency and risks premature circuit breaker trips.

### Final Recommendation: Config A (1 vCPU / 2 GB, 1 $\to$ 5 Tasks)
**Config A is recommended.** In compliance-critical collections voice operations, latency predictability and task resilience far outweigh a minor $\$17/\text{month}$ compute delta. The $2\text{ GB}$ memory envelope provides ample headroom for buffering in-flight calls and preventing OOM kills during downstream latency spikes, while the 1 vCPU eliminates CPU throttling on concurrent async task loops.
