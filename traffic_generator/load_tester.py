import argparse
import asyncio
from datetime import datetime
import json
import logging
import os
import random
import time
from typing import List
import uuid

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("load_tester")

BORROWER_BUCKETS = ["0-30 DPD", "31-60 DPD", "61-90 DPD", "90+ DPD"]
PRODUCT_TYPES = ["credit_card", "bnpl", "personal_loan"]


def get_target_rate_for_simulated_hour(hour_of_day: float) -> float:
    """
    Appendix C schedule:
    - 00:00 - 08:00: 10 calls/min
    - 08:00 - 10:00: ramp 10 -> 50 over 15m (0.25h), hold at 50
    - 10:00 - 14:00: 10 calls/min
    - 14:00 - 16:00: ramp 10 -> 50 over 15m (0.25h), hold at 50
    - 16:00 - 24:00: 10 calls/min
    """
    # Normalize to 0-24
    h = hour_of_day % 24.0

    if 8.0 <= h < 10.0:
        elapsed = h - 8.0
        if elapsed < 0.25:  # first 15 mins ramp
            fraction = elapsed / 0.25
            return 10.0 + fraction * (50.0 - 10.0)
        return 50.0
    elif 14.0 <= h < 16.0:
        elapsed = h - 14.0
        if elapsed < 0.25:  # first 15 mins ramp
            fraction = elapsed / 0.25
            return 10.0 + fraction * (50.0 - 10.0)
        return 50.0
    else:
        return 10.0


def create_synthetic_event() -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "campaign_id": "CAMP-2026-09-A",
        "borrower_bucket": random.choices(BORROWER_BUCKETS, weights=[0.6, 0.2, 0.15, 0.05], k=1)[0],
        "product_type": random.choice(PRODUCT_TYPES),
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "call_duration_s": random.randint(30, 90),
        "outcome": "connected",
        "retry_count": 0,
    }


async def send_single_call(client: httpx.AsyncClient, target_url: str) -> dict:
    payload = {
        "event": create_synthetic_event(),
        "audio_ref": "synthetic",
    }
    t0 = time.time()
    try:
        resp = await client.post(f"{target_url}/calls/process", json=payload, timeout=10.0)
        latency = (time.time() - t0) * 1000.0
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "status": data.get("status"),
                "circuit_breaker": data.get("circuit_breaker_state"),
                "latency_ms": latency,
            }
        else:
            return {"success": False, "status": f"HTTP_{resp.status_code}", "latency_ms": latency}
    except Exception as exc:
        latency = (time.time() - t0) * 1000.0
        return {"success": False, "status": "network_exception", "error": str(exc), "latency_ms": latency}


async def run_traffic_profile(
    target_url: str,
    simulated_hours: float = 24.0,
    compression_factor: float = 60.0,
    quick_test: bool = False,
):
    """
    Simulates the synthetic load profile against the inference service.
    - compression_factor = 60 means 1 simulated hour takes 1 real minute (60s).
    """
    print("=" * 70)
    print("Predixion AI Voice Agent - Synthetic Load Generator")
    print(f"Target URL: {target_url}")
    print(f"Simulated duration: {simulated_hours:.1f} hours | Compression: {compression_factor}x")
    print("=" * 70)

    if quick_test:
        print("[QUICK-TEST MODE] Running 25 sequential requests to demonstrate ramp & failure handling.")
        results = []
        async with httpx.AsyncClient() as client:
            for i in range(25):
                res = await send_single_call(client, target_url)
                results.append(res)
                print(
                    f"[{i+1}/25] Status: {res.get('status')} | Latency: {res.get('latency_ms', 0):.1f}ms | CB: {res.get('circuit_breaker', 'N/A')}"
                )
                await asyncio.sleep(0.1)
        _print_summary(results)
        return

    real_seconds_per_simulated_hour = 3600.0 / compression_factor
    total_real_duration_s = simulated_hours * real_seconds_per_simulated_hour
    start_time = time.time()

    all_results: List[dict] = []

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)) as client:
        while True:
            elapsed_real_s = time.time() - start_time
            if elapsed_real_s >= total_real_duration_s:
                break

            current_simulated_hour = (elapsed_real_s / real_seconds_per_simulated_hour) % 24.0
            calls_per_min = get_target_rate_for_simulated_hour(current_simulated_hour)

            # Simulated calls/min converted to real calls/sec
            # If 10 calls/min baseline: 10 / 60 = 0.166 calls/simulated_sec
            # With compression factor C, real calls/sec = (calls_per_min / 60.0) * compression_factor
            real_calls_per_sec = (calls_per_min / 60.0) * (compression_factor / 60.0)
            interval_s = 1.0 / max(real_calls_per_sec, 0.5)

            # Dispatch call
            res = await send_single_call(client, target_url)
            all_results.append(res)

            status_str = res.get("status")
            cb_str = res.get("circuit_breaker")
            lat = res.get("latency_ms", 0.0)

            print(
                f"[Sim Hour {current_simulated_hour:04.1f}] Rate: {calls_per_min:.0f} cpm | Last call: {status_str} ({lat:.0f}ms) | CB: {cb_str}"
            )

            await asyncio.sleep(interval_s)

    _print_summary(all_results)


def _print_summary(results: List[dict]):
    if not results:
        print("No calls executed.")
        return

    total = len(results)
    successes = sum(1 for r in results if r.get("status") == "success")
    degraded_dlq = sum(1 for r in results if r.get("status") == "degraded_dlq")
    latencies = sorted([r["latency_ms"] for r in results if "latency_ms" in r])

    p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0
    p90 = latencies[int(len(latencies) * 0.90)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    print("\n" + "=" * 70)
    print("TRAFFIC EXECUTION SUMMARY")
    print("=" * 70)
    print(f"Total Call Events Processed : {total}")
    print(f"Successful Calls (200 OK)   : {successes} ({successes/total*100:.1f}%)")
    print(f"Degraded to DLQ (Zero-Loss) : {degraded_dlq} ({degraded_dlq/total*100:.1f}%)")
    print(f"Latency Percentiles         : p50={p50:.1f}ms | p90={p90:.1f}ms | p99={p99:.1f}ms")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthetic load tester for collections inference service")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of inference service")
    parser.add_argument("--hours", type=float, default=24.0, help="Simulated duration in hours")
    parser.add_argument("--compression", type=float, default=360.0, help="Compression factor (default 360: 10s = 1 hour)")
    parser.add_argument("--quick", action="store_true", help="Run quick 25-request verification test")

    args = parser.parse_args()
    asyncio.run(
        run_traffic_profile(
            target_url=args.url,
            simulated_hours=args.hours,
            compression_factor=args.compression,
            quick_test=args.quick,
        )
    )
