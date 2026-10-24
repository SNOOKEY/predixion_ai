import json
import os
import random
import uuid
from datetime import UTC, datetime, timedelta

# Campaign time window configuration
CAMPAIGN_DATE = datetime(2026, 9, 1, tzinfo=UTC)
CAMPAIGN_ID = "CAMP-2026-09-A"

BORROWER_BUCKETS = ["0-30 DPD", "31-60 DPD", "61-90 DPD", "90+ DPD"]
BUCKET_WEIGHTS = [0.60, 0.20, 0.15, 0.05]

PRODUCT_TYPES = ["credit_card", "bnpl", "personal_loan"]

# Outcomes distribution (roughly 60/20/15/5 favoring connected/no_answer, with ~20% failed_downstream)
# Non-failed pool: connected (50%), no_answer (30%), ptp_taken (15%), dispute (5%)
NORMAL_OUTCOMES = ["connected", "no_answer", "ptp_taken", "dispute"]
NORMAL_WEIGHTS = [0.50, 0.30, 0.15, 0.05]


def generate_call_event(event_index: int, total_events: int = 500) -> dict:
    """Generates a single synthetic call event adhering strictly to Appendix A schema."""
    event_id = str(uuid.uuid4())
    bucket = random.choices(BORROWER_BUCKETS, weights=BUCKET_WEIGHTS, k=1)[0]
    product = random.choice(PRODUCT_TYPES)

    # Random timestamp across campaign day (08:00 - 18:00 UTC)
    random_minute = random.randint(8 * 60, 18 * 60)
    random_second = random.randint(0, 59)
    timestamp = CAMPAIGN_DATE + timedelta(minutes=random_minute, seconds=random_second)

    # 20% downstream failure injection per Appendix A/B
    is_failed_downstream = random.random() < 0.20

    if is_failed_downstream:
        outcome = "failed_downstream"
        call_duration_s = 0
        retry_count = random.choice([1, 2, 3])
    else:
        outcome = random.choices(NORMAL_OUTCOMES, weights=NORMAL_WEIGHTS, k=1)[0]
        retry_count = random.choice([0, 0, 0, 1])  # Mostly 0, occasionally 1
        if outcome == "no_answer":
            call_duration_s = 0
        elif outcome == "ptp_taken":
            call_duration_s = random.randint(60, 180)
        elif outcome == "dispute":
            call_duration_s = random.randint(90, 240)
        else:  # connected
            call_duration_s = random.randint(30, 120)

    return {
        "event_id": event_id,
        "campaign_id": CAMPAIGN_ID,
        "borrower_bucket": bucket,
        "product_type": product,
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "call_duration_s": call_duration_s,
        "outcome": outcome,
        "retry_count": retry_count,
    }


def generate_dataset(
    num_records: int = 500, output_path: str = "data/sample_call_events.json"
):
    events = [generate_call_event(i, num_records) for i in range(num_records)]
    # Sort events chronologically by timestamp
    events.sort(key=lambda x: x["timestamp"])

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2)

    # Print summary statistics
    outcome_counts = {}
    bucket_counts = {}
    for ev in events:
        outcome_counts[ev["outcome"]] = outcome_counts.get(ev["outcome"], 0) + 1
        bucket_counts[ev["borrower_bucket"]] = (
            bucket_counts.get(ev["borrower_bucket"], 0) + 1
        )

    print(f"Generated {len(events)} synthetic call events in '{output_path}'.")
    print(f"Outcome distribution: {outcome_counts}")
    print(f"Borrower bucket distribution: {bucket_counts}")


if __name__ == "__main__":
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    target_file = os.path.join(out_dir, "sample_call_events.json")
    generate_dataset(500, target_file)
