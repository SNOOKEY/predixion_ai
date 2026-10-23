import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
import uuid
from datetime import UTC, datetime

# Ensure root workspace directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from inference_service.dlq_handler import print_cli_inspection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("load_tester")

BORROWER_BUCKETS = ["0-30 DPD", "31-60 DPD", "61-90 DPD", "90+ DPD"]
PRODUCT_TYPES = ["credit_card", "bnpl", "personal_loan"]


def get_target_rate_for_simulated_hour(hour_of_day: float) -> float:
    """
    Appendix C schedule:
    - 00:00 - 08:00: 10 calls/min (baseline)
    - 08:00 - 10:00: ramp 10 -> 50 over 15m (0.25h), hold at 50
    - 10:00 - 14:00: 10 calls/min (baseline)
    - 14:00 - 16:00: ramp 10 -> 50 over 15m (0.25h), hold at 50
    - 16:00 - 24:00: 10 calls/min (baseline)
    """
    h = hour_of_day % 24.0

    if 8.0 <= h < 10.0:
        elapsed = h - 8.0
        if elapsed < 0.25:
            fraction = elapsed / 0.25
            return 10.0 + fraction * (50.0 - 10.0)
        return 50.0
    elif 14.0 <= h < 16.0:
        elapsed = h - 14.0
        if elapsed < 0.25:
            fraction = elapsed / 0.25
            return 10.0 + fraction * (50.0 - 10.0)
        return 50.0
    else:
        return 10.0


def create_synthetic_event(base_event: dict | None = None) -> dict:
    """Creates a call event with a guaranteed unique UUID so each event is independently audited."""
    if base_event:
        event_copy = dict(base_event)
        event_copy["event_id"] = str(uuid.uuid4())
        event_copy["timestamp"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return event_copy

    return {
        "event_id": str(uuid.uuid4()),
        "campaign_id": "CAMP-2026-09-A",
        "borrower_bucket": random.choices(BORROWER_BUCKETS, weights=[0.6, 0.2, 0.15, 0.05], k=1)[0],
        "product_type": random.choice(PRODUCT_TYPES),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "call_duration_s": random.randint(30, 90),
        "outcome": "connected",
        "retry_count": 0,
    }


async def send_single_call(
    client: httpx.AsyncClient,
    target_url: str,
    event_data: dict | None = None,
) -> dict:
    event = create_synthetic_event(event_data)
    payload = {
        "event": event,
        "audio_ref": "synthetic",
    }
    t0 = time.time()
    try:
        resp = await client.post(f"{target_url}/calls/process", json=payload, timeout=15.0)
        latency = (time.time() - t0) * 1000.0
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "event_id": event["event_id"],
                "status": data.get("status"),
                "outcome": data.get("outcome"),
                "retries": data.get("retries_attempted", 0),
                "circuit_breaker": data.get("circuit_breaker_state"),
                "latency_ms": latency,
            }
        else:
            return {
                "success": False,
                "event_id": event["event_id"],
                "status": f"HTTP_{resp.status_code}",
                "latency_ms": latency,
            }
    except (httpx.RequestError, httpx.HTTPError) as exc:
        latency = (time.time() - t0) * 1000.0
        return {
            "success": False,
            "event_id": event["event_id"],
            "status": "network_exception",
            "error": str(exc),
            "latency_ms": latency,
        }


def load_dataset_records(dataset_file: str | None) -> list[dict]:
    """Synchronously load pre-generated dataset records."""
    if dataset_file and os.path.exists(dataset_file):
        with open(dataset_file, encoding="utf-8") as f:
            return json.load(f)
    return []


async def run_traffic_profile(
    target_url: str,
    simulated_hours: float = 24.0,
    compression_factor: float = 60.0,
    events_pool: list[dict] | None = None,
    quick_test: bool = False,
    quick_count: int = 75,
):
    print("=" * 80)
    print("Predixion AI Voice Agent — Appendix C Traffic Replay Engine")
    print(f"Target Service URL   : {target_url}")
    print(f"Simulated Profile    : {simulated_hours:.1f} hours (Compression: {compression_factor}x)")
    if events_pool:
        print(f"Loaded {len(events_pool)} events from dataset pool.")
    print("=" * 80)

    if quick_test:
        count = quick_count
        print(f"\n[QUICK-TEST MODE] Dispatching {count} calls under Appendix B 20% failure...")
        results = []
        async with httpx.AsyncClient() as client:
            for i in range(count):
                event_data = events_pool[i % len(events_pool)] if events_pool else None
                res = await send_single_call(client, target_url, event_data)
                results.append(res)
                print(
                    f"[{i+1:02d}/{count}] Status: {res.get('status', 'unknown'):<12} | "
                    f"Retries: {res.get('retries', 0)} | "
                    f"CB: {res.get('circuit_breaker', 'N/A'):<9} | "
                    f"Latency: {res.get('latency_ms', 0):.0f}ms | "
                    f"ID: {res.get('event_id', '')[:8]}..."
                )
                await asyncio.sleep(0.05)
        _print_summary(results, target_url)
        return

    real_seconds_per_simulated_hour = 3600.0 / compression_factor
    total_real_duration_s = simulated_hours * real_seconds_per_simulated_hour
    start_time = time.time()

    all_results: list[dict] = []
    event_idx = 0

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)) as client:
        while True:
            elapsed_real_s = time.time() - start_time
            if elapsed_real_s >= total_real_duration_s:
                break

            current_simulated_hour = (elapsed_real_s / real_seconds_per_simulated_hour) % 24.0
            calls_per_min = get_target_rate_for_simulated_hour(current_simulated_hour)

            real_calls_per_sec = (calls_per_min / 60.0) * (compression_factor / 60.0)
            interval_s = 1.0 / max(real_calls_per_sec, 0.5)

            event_data = None
            if events_pool:
                event_data = events_pool[event_idx % len(events_pool)]
                event_idx += 1

            res = await send_single_call(client, target_url, event_data)
            all_results.append(res)

            status_str = res.get("status")
            cb_str = res.get("circuit_breaker")
            lat = res.get("latency_ms", 0.0)
            retries = res.get("retries", 0)

            print(
                f"[Sim Hour {current_simulated_hour:04.1f}] Rate: {calls_per_min:2.0f} cpm | "
                f"Status: {status_str:<12} | Retries: {retries} | CB: {cb_str:<9} | {lat:4.0f}ms"
            )

            await asyncio.sleep(interval_s)

    _print_summary(all_results, target_url)


def _print_summary(results: list[dict], target_url: str):
    if not results:
        print("No calls executed.")
        return

    total = len(results)
    successes = sum(1 for r in results if r.get("status") == "success")
    degraded_dlq = sum(1 for r in results if r.get("status") == "degraded_dlq")
    errors = total - successes - degraded_dlq
    latencies = sorted([r["latency_ms"] for r in results if "latency_ms" in r])

    p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0
    p90 = latencies[int(len(latencies) * 0.90)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    print("\n" + "=" * 80)
    print("CURRENT BATCH EXECUTION REPORT")
    print("=" * 80)
    print(f"Batch Calls Processed           : {total}")
    print(f"Batch Calls Succeeded (200 OK)  : {successes} ({successes/total*100:.1f}%)")
    print(f"Batch Calls Degraded to DLQ     : {degraded_dlq} ({degraded_dlq/total*100:.1f}%) [Zero Unlogged Drops]")
    if errors > 0:
        print(f"Network / Unhandled Errors      : {errors}")
    print(f"Latency Percentiles             : p50={p50:.1f}ms | p90={p90:.1f}ms | p99={p99:.1f}ms")
    print("=" * 80)

    # Fetch cumulative historical database stats from the service
    try:
        resp = httpx.get(f"{target_url}/metrics", timeout=5.0)
        if resp.status_code == 200:
            m = resp.json()
            hist = m.get("database_historical_totals", {})
            dlq = m.get("dlq", {})
            print("CUMULATIVE DATABASE TOTALS (SQLite: data/dead_letters.db):")
            print(f"  • Total Historical Calls Logged : {hist.get('total_historical_calls')}")
            print(f"  • Total Historical Successful   : {hist.get('successful_calls')}")
            print(f"  • Total Historical In DLQ       : {dlq.get('total_records')} (Pending: {dlq.get('pending_records')}, Replayed: {dlq.get('replayed_records')})")
            print(f"  • Failure Reasons Breakdown     : {dlq.get('failure_breakdown')}")
            print("=" * 80)
    except (httpx.RequestError, httpx.HTTPError) as exc:
        print(f"Could not fetch metrics: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthetic traffic generator and resilience benchmark")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of inference service")
    parser.add_argument("--hours", type=float, default=24.0, help="Simulated duration in hours")
    parser.add_argument("--compression", type=float, default=360.0, help="Compression factor (default 360: 10s = 1 hr)")
    parser.add_argument("--dataset", default="data/sample_call_events.json", help="Path to pre-generated JSON dataset")
    parser.add_argument("--count", type=int, default=75, help="Number of calls to send in --quick mode (default 75)")
    parser.add_argument("--quick", action="store_true", help="Run quick verification test (default 75 calls)")
    parser.add_argument("--inspect", action="store_true", help="Inspect SQLite database and print records")

    args = parser.parse_args()

    if args.inspect:
        print_cli_inspection()
    else:
        dataset_records = load_dataset_records(args.dataset)
        asyncio.run(
            run_traffic_profile(
                target_url=args.url,
                simulated_hours=args.hours,
                compression_factor=args.compression,
                events_pool=dataset_records,
                quick_test=args.quick,
                quick_count=args.count,
            )
        )
