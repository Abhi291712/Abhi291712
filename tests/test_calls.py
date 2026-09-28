"""Tests for the Calls API: creation, idempotency, auth, validation, pagination, transitions."""

from fastapi.testclient import TestClient

from tests.conftest import OTHER_API_KEY

# ---------- POST /calls ----------


def test_create_call_returns_201(client, auth_headers, call_payload):
    response = client.post("/calls", json=call_payload, headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "registered"
    assert body["agent_id"] == "agent_front_desk"
    assert body["id"]
    assert "Idempotent-Replayed" not in response.headers


def test_create_call_requires_api_key(client, call_payload):
    response = client.post("/calls", json=call_payload)

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "UNAUTHORIZED", "message": "Missing or invalid API key."}
    }


def test_create_call_rejects_invalid_api_key(client, call_payload):
    response = client.post("/calls", json=call_payload, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_create_call_validation_error_returns_422(client, auth_headers):
    response = client.post(
        "/calls",
        json={"agent_id": "a", "from_number": "not-a-phone", "to_number": "+14155550199"},
        headers=auth_headers,
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert any(d["field"].endswith("from_number") for d in error["details"])


def test_create_call_rejects_unknown_fields(client, auth_headers, call_payload):
    response = client.post("/calls", json={**call_payload, "stauts": "x"}, headers=auth_headers)
    assert response.status_code == 422


def test_idempotency_key_prevents_duplicate_create(client, auth_headers, call_payload, db_session):
    headers = {**auth_headers, "Idempotency-Key": "create-123"}

    first = client.post("/calls", json=call_payload, headers=headers)
    second = client.post("/calls", json=call_payload, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    listing = client.get("/calls", headers=auth_headers).json()
    assert len(listing["items"]) == 1


def test_idempotency_key_reused_with_different_body_returns_422(client, auth_headers, call_payload):
    headers = {**auth_headers, "Idempotency-Key": "create-456"}
    client.post("/calls", json=call_payload, headers=headers)

    response = client.post("/calls", json={**call_payload, "agent_id": "other"}, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISMATCH"


def test_duplicate_external_call_id_returns_409(client, auth_headers, create_call, call_payload):
    create_call(external_call_id="ext-1")

    response = client.post(
        "/calls", json={**call_payload, "external_call_id": "ext-1"}, headers=auth_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CALL_ALREADY_EXISTS"


# ---------- GET /calls/{id} ----------


def test_get_call_returns_call(client, auth_headers, create_call):
    created = create_call()

    response = client.get(f"/calls/{created['id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_call_returns_404(client, auth_headers):
    response = client.get("/calls/does-not-exist", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_get_call_requires_api_key(client, create_call):
    created = create_call()
    assert client.get(f"/calls/{created['id']}").status_code == 401


# ---------- GET /calls (filters + cursor pagination) ----------


def _collect_all_pages(client: TestClient, headers: dict, **params) -> list[list[str]]:
    """Walk every page and return the IDs seen on each page."""
    pages, cursor = [], None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        body = client.get("/calls", params=query, headers=headers).json()
        pages.append([item["id"] for item in body["items"]])
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


def test_list_calls_paginates_with_cursor(client, auth_headers, create_call):
    created_ids = [create_call()["id"] for _ in range(5)]

    pages = _collect_all_pages(client, auth_headers, limit=2)

    assert [len(page) for page in pages] == [2, 2, 1]
    seen = [call_id for page in pages for call_id in page]
    assert len(seen) == len(set(seen)) == 5  # No duplicates...
    assert set(seen) == set(created_ids)  # ...and nothing skipped.


def test_list_calls_last_page_has_no_cursor(client, auth_headers, create_call):
    create_call()
    body = client.get("/calls", params={"limit": 10}, headers=auth_headers).json()
    assert body["next_cursor"] is None


def test_list_calls_filters_by_status_and_agent(client, auth_headers, create_call):
    ongoing = create_call(agent_id="agent_a")
    create_call(agent_id="agent_a")
    create_call(agent_id="agent_b")
    client.patch(f"/calls/{ongoing['id']}", json={"status": "ongoing"}, headers=auth_headers)

    by_agent = client.get("/calls", params={"agent_id": "agent_a"}, headers=auth_headers).json()
    by_status = client.get("/calls", params={"status": "ongoing"}, headers=auth_headers).json()

    assert len(by_agent["items"]) == 2
    assert [c["id"] for c in by_status["items"]] == [ongoing["id"]]


def test_list_calls_invalid_cursor_returns_422(client, auth_headers):
    response = client.get("/calls", params={"cursor": "garbage!!"}, headers=auth_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


def test_list_calls_limit_out_of_range_returns_422(client, auth_headers):
    assert client.get("/calls", params={"limit": 0}, headers=auth_headers).status_code == 422
    assert client.get("/calls", params={"limit": 101}, headers=auth_headers).status_code == 422


def test_list_calls_invalid_status_returns_422(client, auth_headers):
    response = client.get("/calls", params={"status": "flying"}, headers=auth_headers)
    assert response.status_code == 422


def test_second_api_key_is_accepted(client):
    assert client.get("/calls", headers={"X-API-Key": OTHER_API_KEY}).status_code == 200


# ---------- PATCH /calls/{id} ----------


def test_valid_status_transitions(client, auth_headers, create_call):
    call_id = create_call()["id"]

    ongoing = client.patch(f"/calls/{call_id}", json={"status": "ongoing"}, headers=auth_headers)
    ended = client.patch(f"/calls/{call_id}", json={"status": "ended"}, headers=auth_headers)

    assert ongoing.status_code == 200 and ongoing.json()["status"] == "ongoing"
    assert ongoing.json()["started_at"] is not None
    assert ended.status_code == 200 and ended.json()["status"] == "ended"
    assert ended.json()["ended_at"] is not None


def test_ongoing_can_move_to_error(client, auth_headers, create_call):
    call_id = create_call()["id"]
    client.patch(f"/calls/{call_id}", json={"status": "ongoing"}, headers=auth_headers)

    response = client.patch(f"/calls/{call_id}", json={"status": "error"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "error"


def test_skipping_a_status_returns_409(client, auth_headers, create_call):
    call_id = create_call()["id"]

    response = client.patch(f"/calls/{call_id}", json={"status": "ended"}, headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


def test_moving_backwards_returns_409(client, auth_headers, create_call):
    call_id = create_call()["id"]
    client.patch(f"/calls/{call_id}", json={"status": "ongoing"}, headers=auth_headers)
    client.patch(f"/calls/{call_id}", json={"status": "ended"}, headers=auth_headers)

    response = client.patch(f"/calls/{call_id}", json={"status": "ongoing"}, headers=auth_headers)

    assert response.status_code == 409


def test_repeating_current_status_is_a_noop(client, auth_headers, create_call):
    call_id = create_call()["id"]
    response = client.patch(
        f"/calls/{call_id}", json={"status": "registered"}, headers=auth_headers
    )
    assert response.status_code == 200


def test_patch_missing_call_returns_404(client, auth_headers):
    response = client.patch("/calls/nope", json={"status": "ongoing"}, headers=auth_headers)
    assert response.status_code == 404


def test_patch_invalid_status_value_returns_422(client, auth_headers, create_call):
    call_id = create_call()["id"]
    response = client.patch(f"/calls/{call_id}", json={"status": "paused"}, headers=auth_headers)
    assert response.status_code == 422


def test_patch_requires_api_key(client, create_call):
    call_id = create_call()["id"]
    assert client.patch(f"/calls/{call_id}", json={"status": "ongoing"}).status_code == 401


# ---------- GET /calls/{id}/summary ----------


def test_summary_for_call_without_events(client, auth_headers, create_call):
    call_id = create_call()["id"]

    response = client.get(f"/calls/{call_id}/summary", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["events"] == [] and body["appointments"] == []
    assert body["duration_seconds"] is None


def test_summary_missing_call_returns_404(client, auth_headers):
    assert client.get("/calls/nope/summary", headers=auth_headers).status_code == 404


def test_summary_requires_api_key(client, create_call):
    call_id = create_call()["id"]
    assert client.get(f"/calls/{call_id}/summary").status_code == 401
