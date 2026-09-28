"""End-to-end eval of the voice agent tools: correctness of every answer, and latency.

Each scenario in ``booking_scenarios.json`` replays the tool calls a voice agent would make
during a conversation and checks the status code, the structured fields and, most importantly,
the sentence the agent will speak. It also measures latency, because a correct answer that
arrives after two seconds of silence is still a bad call.

Usage::

    python -m evals.run_booking_eval                        # in-process, fresh database
    python -m evals.run_booking_eval --base-url https://my-app.onrender.com --api-key KEY

The exit code is non-zero if any check fails or the p95 latency exceeds the budget, so the
eval can gate CI or a deployment.
"""

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

SCENARIOS = Path(__file__).with_name("booking_scenarios.json")


def fill(value: Any, day: str) -> Any:
    """Replace the {date} placeholder recursively (each scenario gets its own day)."""
    if isinstance(value, str):
        return value.replace("{date}", day)
    if isinstance(value, dict):
        return {k: fill(v, day) for k, v in value.items()}
    return value


def check(response: httpx.Response, expect: dict) -> list[str]:
    """Return a list of human-readable problems (empty = the step passed)."""
    problems = []
    if response.status_code != expect.get("status", 200):
        problems.append(f"status {response.status_code}, expected {expect['status']}")
    body = response.json()
    message = body.get("message", "")
    if not message:
        problems.append("no speakable 'message' in the response")
    for text in expect.get("message_contains", []):
        if text not in message:
            problems.append(f"message missing {text!r}: {message!r}")
    for text in expect.get("message_excludes", []):
        if text in message:
            problems.append(f"message should not contain {text!r}: {message!r}")
    for key, value in expect.get("json", {}).items():
        if body.get(key) != value:
            problems.append(f"{key}={body.get(key)!r}, expected {value!r}")
    return problems


def in_process_client(api_key: str):
    """A TestClient against a fresh temporary database (no server needed)."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    db = Path(tempfile.mkdtemp()) / "eval.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db}",
        api_keys=api_key,
        rate_limit_capacity=10_000,
        event_retry_interval_seconds=0,
        log_level="WARNING",
    )
    return TestClient(create_app(settings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", help="Evaluate a running deployment instead.")
    parser.add_argument("--api-key", default="eval-key")
    parser.add_argument("--start-date", default="2031-03-03", help="First scenario's day.")
    parser.add_argument("--budget-ms", type=float, default=800, help="p95 latency budget.")
    args = parser.parse_args()

    client = (
        httpx.Client(base_url=args.base_url, timeout=10)
        if args.base_url
        else in_process_client(args.api_key)
    )
    headers = {"X-API-Key": args.api_key}
    scenarios = json.loads(SCENARIOS.read_text())
    first_day = date.fromisoformat(args.start_date)

    latencies: list[float] = []
    failures = 0
    with client:
        for index, scenario in enumerate(scenarios):
            day = (first_day + timedelta(days=index)).isoformat()
            problems: list[str] = []
            for step in scenario["steps"]:
                started = time.perf_counter()
                response = client.post(
                    f"/tools/{step['tool']}", json=fill(step["args"], day), headers=headers
                )
                latencies.append((time.perf_counter() - started) * 1000)
                problems += [f"{step['tool']}: {p}" for p in check(response, step["expect"])]
            status = "PASS" if not problems else "FAIL"
            failures += bool(problems)
            print(f"[{status}] {scenario['name']}")
            for problem in problems:
                print(f"        - {problem}")

    p50 = statistics.median(latencies)
    p95 = statistics.quantiles(latencies, n=20)[-1] if len(latencies) >= 2 else latencies[0]
    passed = len(scenarios) - failures
    print(f"\n{passed}/{len(scenarios)} scenarios passed")
    print(f"latency: p50 {p50:.1f} ms, p95 {p95:.1f} ms (budget {args.budget_ms:.0f} ms)")

    if p95 > args.budget_ms:
        print("FAIL: p95 latency is over budget")
    return 0 if failures == 0 and p95 <= args.budget_ms else 1


if __name__ == "__main__":
    sys.exit(main())
