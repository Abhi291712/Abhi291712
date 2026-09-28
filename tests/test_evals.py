"""Keep the eval scripts working: run the booking eval for real, the LLM eval with a fake model."""

import json
import sys
from types import SimpleNamespace

from app.services.call_analysis import CallAnalyzer
from evals import run_analysis_eval, run_booking_eval


def test_booking_eval_passes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_booking_eval"])

    assert run_booking_eval.main() == 0
    assert "scenarios passed" in capsys.readouterr().out


def test_booking_eval_detects_wrong_answers():
    response = SimpleNamespace(status_code=409, json=lambda: {"message": "Sorry, taken."})

    problems = run_booking_eval.check(response, {"status": 201, "message_contains": ["all set"]})

    assert len(problems) == 2


def test_analysis_eval_requires_confirmation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_analysis_eval"])
    assert run_analysis_eval.main() == 2  # Refuses to spend money without --yes.


def test_analysis_eval_scores_fields(monkeypatch, capsys):
    perfect = {
        "summary": "s",
        "sentiment": "positive",
        "intent": "book_appointment",
        "customer_name": "jane doe",
        "appointment_booked": True,
        "follow_up_needed": False,
        "follow_up_reason": None,
    }
    response = SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text=json.dumps(perfect))],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )
    fake = SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **k: response))
    )
    monkeypatch.setattr(CallAnalyzer, "from_settings", classmethod(lambda cls, s: cls(fake, "m")))
    monkeypatch.setattr(sys, "argv", ["run_analysis_eval", "--yes", "--limit", "1"])

    assert run_analysis_eval.main() == 0
    out = capsys.readouterr().out
    assert "[PASS] booking-simple" in out
    assert "tokens: 1000 in / 200 out" in out
