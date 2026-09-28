"""Eval for LLM call analysis: how accurately does the model label real-looking transcripts?

``transcripts.jsonl`` holds hand-labelled calls, including tricky ones: a caller trying a prompt
injection, a complaint that needs a human, an urgent case with no booking. For each transcript
the script runs the real analyzer and compares every field with the label, then reports
per-field accuracy, latency and token cost.

This calls the Claude API and costs real money (roughly a few cents for the whole set), so it
never runs in CI and requires ``--yes``::

    ANTHROPIC_API_KEY=... python -m evals.run_analysis_eval --yes
    python -m evals.run_analysis_eval --yes --limit 3        # a quick smoke run

Use it whenever you change the prompt, the schema, the model or the effort level: compare the
accuracy table before and after, and keep the change only if it does not get worse.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

DATA = Path(__file__).with_name("transcripts.jsonl")
FIELDS = ["intent", "sentiment", "appointment_booked", "follow_up_needed", "customer_name"]
# Claude Opus 5 list prices in USD per million tokens (update if you change the model).
PRICE_PER_MTOK = {"input": 5.00, "output": 25.00}


class UsageRecorder:
    """Wraps `client.beta.messages` to record token usage of every request."""

    def __init__(self, messages):
        self._messages = messages
        self.input_tokens = 0
        self.output_tokens = 0

    def create(self, **kwargs):
        response = self._messages.create(**kwargs)
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        return response


def normalise(field: str, value):
    # Names are compared case-insensitively and ignoring surrounding whitespace.
    if field == "customer_name" and isinstance(value, str):
        return value.strip().lower()
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--yes", action="store_true", help="Confirm you accept the API cost.")
    parser.add_argument("--limit", type=int, help="Only evaluate the first N transcripts.")
    parser.add_argument("--model", help="Override LLM_MODEL for this run.")
    args = parser.parse_args()
    if not args.yes:
        print("This eval calls the Claude API and costs money. Re-run with --yes to continue.")
        return 2

    from types import SimpleNamespace

    from app.core.config import Settings
    from app.services.call_analysis import CallAnalyzer

    settings = Settings(llm_analysis_enabled=True)
    analyzer = CallAnalyzer.from_settings(settings)
    if args.model:
        analyzer.model = args.model
    recorder = UsageRecorder(analyzer.client.beta.messages)
    analyzer.client = SimpleNamespace(beta=SimpleNamespace(messages=recorder))

    cases = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    cases = cases[: args.limit] if args.limit else cases

    correct = dict.fromkeys(FIELDS, 0)
    latencies = []
    unusable = 0
    for case in cases:
        started = time.perf_counter()
        result = analyzer.analyze(case["transcript"])
        latencies.append(time.perf_counter() - started)
        if result is None:
            unusable += 1
            print(f"[NONE] {case['id']}: refused or unusable output")
            continue
        wrong = []
        for field in FIELDS:
            got, want = getattr(result, field), case["expected"][field]
            if normalise(field, got) == normalise(field, want):
                correct[field] += 1
            else:
                wrong.append(f"{field}: got {got!r}, expected {want!r}")
        print(f"[{'PASS' if not wrong else 'MISS'}] {case['id']}")
        for line in wrong:
            print(f"        - {line}")

    total = len(cases)
    print(f"\nModel: {analyzer.model}   transcripts: {total}   unusable: {unusable}")
    for field in FIELDS:
        print(f"  {field:<20} {correct[field]}/{total}  ({100 * correct[field] / total:.0f}%)")
    cost = (
        recorder.input_tokens * PRICE_PER_MTOK["input"]
        + recorder.output_tokens * PRICE_PER_MTOK["output"]
    ) / 1_000_000
    print(f"latency: median {statistics.median(latencies):.1f} s, max {max(latencies):.1f} s")
    print(
        f"tokens: {recorder.input_tokens} in / {recorder.output_tokens} out  "
        f"(about ${cost:.3f}, {100 * cost / total:.2f} cents per call)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
