"""Tests for LLM call analysis. A fake Anthropic client stands in for the real API, so the tests
check how we build the request and handle every kind of response without network calls or cost."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.call_analysis import ANALYSIS_SCHEMA, FALLBACK_BETA, CallAnalyzer

GOOD_ANALYSIS = {
    "summary": "Jane booked a teeth cleaning for Tuesday at 10 AM.",
    "sentiment": "positive",
    "intent": "book_appointment",
    "customer_name": "Jane",
    "appointment_booked": True,
    "follow_up_needed": False,
    "follow_up_reason": None,
}


class FakeMessages:
    """Mimics `client.beta.messages`: records requests and returns a canned response."""

    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(stop_reason="end_turn", text: str | None = None, error=None):
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=content), error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


# ---------- The analyzer on its own ----------


def test_analyzer_builds_structured_output_request_with_fallback():
    client, messages = fake_client(text=json.dumps(GOOD_ANALYSIS))

    result = CallAnalyzer(client, "claude-opus-5").analyze("Agent: Hello\nUser: Hi, I'm Jane")

    assert result is not None and result.intent == "book_appointment"
    request = messages.requests[0]
    assert request["model"] == "claude-opus-5"
    assert request["fallbacks"] == "default" and request["betas"] == [FALLBACK_BETA]
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": ANALYSIS_SCHEMA}
    # The transcript is fenced so the model treats it as data, not instructions.
    assert request["messages"][0]["content"].startswith("<transcript>")


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_analyzer_returns_none_when_model_stops_early(stop_reason):
    client, _ = fake_client(stop_reason=stop_reason, text=json.dumps(GOOD_ANALYSIS))
    assert CallAnalyzer(client, "m").analyze("transcript") is None


def test_analyzer_returns_none_for_output_not_matching_schema():
    client, _ = fake_client(text=json.dumps({"summary": "x", "sentiment": "ecstatic"}))
    assert CallAnalyzer(client, "m").analyze("transcript") is None


def test_analyzer_disabled_by_default(settings):
    assert CallAnalyzer.from_settings(settings) is None


# ---------- Through the application ----------


@pytest.fixture
def llm_app(settings):
    app = create_app(settings)
    client, messages = fake_client(text=json.dumps(GOOD_ANALYSIS))
    app.state.call_analyzer = CallAnalyzer(client, "claude-opus-5")
    app.state.fake_messages = messages
    return app


def test_call_is_analysed_after_it_ends(llm_app, auth_headers):
    with TestClient(llm_app) as client:
        from tests.conftest import sign

        body = json.dumps(
            {
                "event_id": "evt-end",
                "event_type": "call_ended",
                "call": {"call_id": "ext-llm", "transcript": "Agent: Hi\nUser: I'm Jane..."},
            }
        ).encode()
        client.post("/webhooks/voice", content=body, headers=sign(body))

        call = client.get("/calls", headers=auth_headers).json()["items"][0]
        summary = client.get(f"/calls/{call['id']}/summary", headers=auth_headers).json()

    assert summary["analysis"]["intent"] == "book_appointment"
    assert summary["summary"] == GOOD_ANALYSIS["summary"]  # Filled in because none was sent.
    assert summary["sentiment"] == "positive"
    assert len(llm_app.state.fake_messages.requests) == 1


def test_llm_failure_does_not_break_webhook_processing(settings, auth_headers):
    app = create_app(settings)
    client_obj, _ = fake_client(error=RuntimeError("API down"))
    app.state.call_analyzer = CallAnalyzer(client_obj, "claude-opus-5")

    with TestClient(app) as client:
        from tests.conftest import sign

        body = json.dumps(
            {
                "event_id": "e",
                "event_type": "call_ended",
                "call": {"call_id": "c", "transcript": "t"},
            }
        ).encode()
        response = client.post("/webhooks/voice", content=body, headers=sign(body))
        call = client.get("/calls", headers=auth_headers).json()["items"][0]

    assert response.status_code == 200
    assert call["status"] == "ended"  # The call was still processed.


def test_analyze_endpoint_requires_transcript(llm_app, auth_headers, call_payload):
    with TestClient(llm_app) as client:
        call = client.post("/calls", json=call_payload, headers=auth_headers).json()
        response = client.post(f"/calls/{call['id']}/analyze", headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_TRANSCRIPT"


def test_analyze_endpoint_when_llm_disabled_returns_503(client, auth_headers, create_call):
    call = create_call()
    response = client.post(f"/calls/{call['id']}/analyze", headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_DISABLED"


def test_analyze_endpoint_missing_call_returns_404(llm_app, auth_headers):
    with TestClient(llm_app) as client:
        response = client.post("/calls/nope/analyze", headers=auth_headers)
    assert response.status_code == 404
