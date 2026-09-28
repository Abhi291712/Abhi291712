# Learning guide: how voice-ai-backend works

This guide explains the project in plain language, then follows two real requests through every file they touch. Keep the code open next to it; the file and function names below match the source exactly.

---

## 1. The big picture: a restaurant

Think of the service as a restaurant.

| Restaurant | In this project | Where |
|---|---|---|
| **Order form** | **Schema**: the exact shape an order must have. A form with a missing or nonsense field is handed back before it reaches the kitchen. | `app/schemas/` |
| **Waiter** | **Router**: takes the order from the customer, hands it to the kitchen, and brings back the plate. The waiter never cooks. | `app/routers/` |
| **Chef** | **Service**: knows the recipes (business rules): "we don't serve a dish twice for the same ticket", "an ended call can't restart". The chef never walks into the dining room. | `app/services/` |
| **Storeroom** | **Repository**: the only place ingredients are fetched from or put away. The chef asks the storeroom keeper; they don't rummage through the shelves themselves. | `app/repositories/` |
| **Shelves and labels** | **Models**: how things are laid out in the storeroom (database tables and columns). | `app/models/` |
| **Door staff** | **Middleware**: greets every guest (gives them a ticket number = request ID) and turns people away if they come in too often (rate limiting). | `app/middleware/` |
| **Membership card check** | **Security**: checks the `X-API-Key` card, or the wax seal (HMAC signature) on a delivery from a supplier (webhooks). | `app/core/security.py` |
| **Complaint desk** | **Error handlers**: whatever goes wrong, the customer gets a complaint slip in the same standard format. | `app/core/errors.py` |
| **Head office rules** | **Config**: opening hours, secrets and limits, all written down in one place. | `app/core/config.py` |

Why split things up like this? Each part can change without breaking the others. You can move the storeroom (SQLite to PostgreSQL) without retraining the chef, and you can test the chef's recipes without a dining room.

### How the parts are connected: `Depends`

FastAPI's `Depends(...)` is how the waiter gets a chef, and the chef gets a storeroom keeper, without anyone creating them by hand. Look at `app/dependencies.py`:

```python
def get_call_service(db=Depends(get_db), calls=Depends(get_call_repository), ...) -> CallService:
    return CallService(db, calls, events, appointments)
```

For each request FastAPI runs `get_db` once (opening one database session), builds the repositories with that session, then builds the service. Everything in one request shares the same session, and therefore the same transaction.

---

## 2. Trace: `POST /calls`

Request:

```http
POST /calls
X-API-Key: dev-key-1
Idempotency-Key: abc-123
Content-Type: application/json

{"agent_id": "agent_front_desk", "from_number": "+14155550123", "to_number": "+14155550199"}
```

### Step 0: startup (happens once, before any request)

- **`app/main.py`**: `app = create_app()` runs when uvicorn imports the module.
  - Loads `Settings` from **`app/core/config.py`** (reads `.env`).
  - Calls `configure_logging()` from **`app/core/logging.py`**.
  - Creates the database engine and session factory with **`app/core/database.py`** and stores them on `app.state`.
  - Registers the exception handlers from **`app/core/errors.py`**.
  - Adds the three middlewares and includes the routers.
  - On startup, the `lifespan` function calls `init_db()`, which creates the tables defined in **`app/models/`** (in production, `alembic upgrade head` in **`migrations/`** does this instead). It also starts the retry sweeper (section 5).

### Step 1: the door (middleware)

Middleware runs in the order *last added, first run*:

1. **`app/middleware/request_id.py`**, `RequestIdMiddleware`: generates a request ID (or accepts the caller's `X-Request-ID`) and stores it in `request_id_ctx`. From now on every log line includes it. It also adds `X-Request-ID` to the response.
2. **`app/middleware/metrics.py`**, `MetricsMiddleware`: starts a timer. When the response is sent, it counts the request in Prometheus metrics under the route template `/calls`.
3. **`app/middleware/rate_limit.py`**, `RateLimitMiddleware`: the path starts with `/calls`, and the key is valid, so it `await`s `limiter.acquire("dev-key-1")` for a token (in memory, or in Redis when `REDIS_URL` is set). If the bucket is empty, the request stops here with **429** and a `Retry-After` header.

### Step 2: routing and dependencies

FastAPI matches `POST /calls` to `create_call` in **`app/routers/calls.py`**. Before the function body runs, FastAPI resolves everything it needs:

1. **Auth**: the router was created with `dependencies=[Depends(require_api_key)]`. `require_api_key` in **`app/core/security.py`** compares the header against `settings.api_key_set` using `hmac.compare_digest`. Missing or wrong key raises `UnauthorizedError`, which becomes **401**.
2. **Body validation**: the JSON is parsed into `CallCreate` from **`app/schemas/call.py`**. The phone numbers must match the E.164 pattern, `agent_id` must be 1-100 characters, and unknown fields are rejected. Any problem produces **422** through `validation_error_handler`.
3. **Header**: `Idempotency-Key` is read (max 255 chars).
4. **Service**: `get_call_service` in **`app/dependencies.py`** builds a `CallService` with a fresh session from `get_db` and three repositories.

### Step 3: the router calls the service

```python
call, replayed = service.create_call(payload, idempotency_key)
```

The router does nothing else except set the `Idempotent-Replayed` header when needed.

### Step 4: business logic in `CallService.create_call`

In **`app/services/call_service.py`**:

1. `hash_request(data)` computes a SHA-256 fingerprint of the body.
2. If an `Idempotency-Key` was sent, `calls.get_by_idempotency_key()` looks for an earlier call with that key.
   - Found, same hash: return it (`replayed=True`). No new row.
   - Found, different hash: `UnprocessableError(code="IDEMPOTENCY_KEY_MISMATCH")`, which becomes **422**.
3. If `external_call_id` is set and already used: `ConflictError`, which becomes **409**.
4. Builds a `Call` ORM object (**`app/models/call.py`**) with status `registered`.
5. `calls.add(call)` in **`app/repositories/call_repo.py`** adds it to the session and flushes (sends the `INSERT`).
6. `self.session.commit()` makes it permanent.
   - If a unique constraint fails (two identical requests at the same instant), it rolls back and returns the winner's call instead.
7. Logs `"Call created"` (with the request ID, automatically).

### Step 5: the response

- The router returns the `Call` object. FastAPI converts it with `response_model=CallRead` (`from_attributes=True` lets Pydantic read ORM attributes), so internal columns like `request_hash` are never exposed.
- Status code **201** comes from `status_code=status.HTTP_201_CREATED` on the route decorator.
- On the way out, `RequestIdMiddleware` adds `X-Request-ID` and logs `POST /calls -> 201 (x ms)`.
- `get_db` closes the session.

### If something goes wrong

Services raise exceptions such as `NotFoundError` or `ConflictError`. They do not know about status codes. `app_error_handler` in **`app/core/errors.py`** turns them into:

```json
{"error": {"code": "CONFLICT", "message": "..."}}
```

---

## 3. Trace: a webhook (`POST /webhooks/voice`)

The voice platform tells us a call ended:

```http
POST /webhooks/voice
X-Webhook-Timestamp: 1893456300
X-Signature: sha256=5f2c...
Content-Type: application/json

{"event_id": "evt_42", "event_type": "call_ended",
 "call": {"call_id": "call_abc", "ended_at": "2030-01-15T10:05:00Z", "transcript": "..."}}
```

### Step 1: middleware

- `RequestIdMiddleware` assigns a request ID as before.
- `RateLimitMiddleware` lets it through: `/webhooks` is not a rate-limited prefix (the platform authenticates with a signature, not an API key).

### Step 2: signature check (before anything is parsed)

`receive_voice_webhook` in **`app/routers/webhooks.py`** depends on `verified_webhook_body` in **`app/core/security.py`**:

1. Reads the **raw bytes** of the body.
2. `is_valid_signature()` first checks the timestamp: if it is more than 5 minutes away from now, the request is rejected. A captured request can't be replayed later.
3. `compute_signature()` calculates `HMAC-SHA256(secret, "<timestamp>." + raw_body)`, and the result is compared to the `X-Signature` header with `hmac.compare_digest`.
4. Mismatch, stale timestamp or missing header: **401** `INVALID_SIGNATURE`. A tampered body (or a changed timestamp) changes the hash, so it fails too.

Why raw bytes? If we parsed the JSON and re-encoded it, spacing or key order could change and the signature would no longer match.

Retell requests (`POST /webhooks/retell`) follow the same path with a different lock: `verified_retell_body` checks the `x-retell-signature` header, and **`app/integrations/retell.py`** translates Retell's payload into the same `WebhookEvent` before Step 4. From then on the flow is identical.

### Step 3: parse and validate

The router calls `WebhookEvent.model_validate_json(body)` (**`app/schemas/webhook.py`**). An unknown `event_type` or missing `call.call_id` becomes **422**.

### Step 4: store the event once (fast path)

`WebhookService.record_event()` in **`app/services/webhook_service.py`**:

1. `events.get_by_event_id("evt_42")` (**`app/repositories/event_repo.py`**). Already stored? Return `False` (duplicate).
2. Otherwise insert an `Event` row (**`app/models/event.py`**) with the full payload and status `received`, then commit.
3. The `event_id` column is `UNIQUE`, so if two deliveries race, the database rejects the second one and we treat it as a duplicate.

### Step 5: answer immediately

- New event: the router schedules `process_event_in_background(session_factory, "evt_42", analyzer)` with `BackgroundTasks`.
- The response `{"received": true, "duplicate": false}` (or `"duplicate": true`) is sent with **200**. Duplicates also get 200 because the platform just needs to know it can stop retrying.

### Step 6: background processing (after the response is sent)

`process_event_in_background` opens its **own** session (the request's session is already closed) and calls `WebhookService.process_event()`:

1. Loads the stored event; skips it if already processed.
2. `_apply(event)`:
   1. `calls.get_by_external_id("call_abc")`. If the service has never seen this call, it creates it.
   2. Looks up the target status: `call_ended` means `ended` (`EVENT_TARGET_STATUS`).
   3. **Out-of-order protection**: compares `STATUS_RANK` from **`app/models/call.py`**. The status only changes if the new rank is higher. So if `call_started` arrives *after* `call_ended`, the call stays `ended`.
   4. Fills in missing details (timestamps, numbers) without overwriting known values with empty ones, and stores transcript, summary and sentiment when present.
3. Marks the event `processed`, counts the attempt, and commits. If anything fails, the event is marked `failed` with the error text, so nothing is lost silently; the retry sweeper tries again later.
4. **LLM analysis** (only when `LLM_ANALYSIS_ENABLED=true`): for `call_ended`, `maybe_analyze()` sends the transcript to Claude through `CallAnalyzer` in **`app/services/call_analysis.py`** and stores the structured result in `calls.analysis`. If Claude fails, the call is still fine; only the analysis is missing.

### Step 7: reading the merged result

`GET /calls/{id}/summary` goes through `get_call_summary` in `app/routers/calls.py`, then `CallService.get_summary()`, which merges:

- the call row (status, timestamps, transcript, summary, sentiment, LLM `analysis`),
- a computed `duration_seconds`,
- the event timeline from `EventRepository.list_for_call()`,
- appointments booked during the call from `AppointmentRepository.list_for_call()`.

---

## 4. Every status code used, and why

| Code | Name | When it happens here | Why this code |
|---|---|---|---|
| **200** | OK | Successful `GET`, `PATCH`, `POST /tools/check-availability`, `POST /calls/{id}/analyze`, every accepted webhook (including duplicates), and **every** Retell function call (success or not, with `ok: true/false`) | The request succeeded and the body contains the result. Retell functions always use 200 so the agent's LLM receives a sentence to say instead of a failed call. |
| **201** | Created | `POST /calls` (also when replayed with the same `Idempotency-Key`), `POST /tools/book-appointment` | A new resource was created. Replays return the same status so a retrying client needs no special handling. |
| **401** | Unauthorized | Missing or invalid `X-API-Key` on `/calls` or `/tools`; missing, invalid or expired (replayed) webhook signature; Retell requests when `RETELL_API_KEY` is not configured | The caller has not proven who they are. |
| **404** | Not Found | Unknown call ID on `GET`/`PATCH /calls/{id}` or `/summary`; unknown routes | The resource does not exist. |
| **405** | Method Not Allowed | Wrong HTTP method on an existing path, e.g. `DELETE /health` | The path exists but not with that method. |
| **409** | Conflict | Invalid status transition (e.g. `ended -> ongoing`); duplicate `external_call_id`; double booking a slot (or a slot busy in Google Calendar); analysing a call with no transcript | The request is valid but clashes with the current state of the data. |
| **422** | Unprocessable Content | Schema validation errors; invalid pagination cursor; `Idempotency-Key` reused with a different body; booking outside business hours, misaligned, or in the past | The request is well-formed JSON but its content breaks a rule. |
| **429** | Too Many Requests | The API key's token bucket is empty. Includes `Retry-After` | Tells the client to slow down and exactly when to try again. |
| **500** | Internal Server Error | An unexpected bug. Details are logged, never sent to the client | Something failed on our side. |
| **503** | Service Unavailable | `GET /health` when the database is unreachable; `POST /calls/{id}/analyze` when LLM analysis is disabled or the model could not produce an analysis | A dependency is unavailable. For `/health`, it tells load balancers to stop sending traffic to this instance. |

---

## 5. The production pieces, in restaurant terms

| Piece | Restaurant | Where | What to read first |
|---|---|---|---|
| **Retell integration** | The phone operator who takes orders by phone and speaks a different "order form" dialect; a translator rewrites each order onto our own form | `app/integrations/retell.py`, `app/routers/retell.py` | `to_webhook_event()` |
| **LLM call analysis** | A note-taker who reads the call transcript afterwards and fills in a fixed summary card (the JSON schema) | `app/services/call_analysis.py` | `CallAnalyzer.analyze()` |
| **Retry sweeper** | The night shift: every 30 seconds it checks for orders that were written down but never cooked (or burned), and sends them back to the kitchen | `app/services/event_sweeper.py` | `reprocess_pending_events()` |
| **Migrations** | Renovation plans for the storeroom: numbered, reviewed steps instead of moving shelves at random | `migrations/versions/` | `0001_initial_schema.py` |
| **Redis rate limiting** | One shared ticket dispenser for all entrances, instead of one per door | `app/middleware/rate_limit.py` | `TOKEN_BUCKET_LUA` |
| **Metrics and latency budget** | The kitchen display showing how long each dish takes, with a warning light when the phone orders are too slow | `app/middleware/metrics.py`, `app/core/metrics.py` | `MetricsMiddleware._record()` |
| **Google Calendar** | The owner's personal diary: never book when the owner is out, and write every booking in it too | `app/integrations/google_calendar.py` | `overlaps()`, `create_event()` |
| **Evals** | Mystery diners who order the same set of dishes after every change and grade each one | `evals/` | `booking_scenarios.json` |

A good exercise: follow a Retell `book_appointment` function call from `retell_book_appointment()` through `AppointmentService.book()` to `sync_appointment_to_calendar()`, and explain why the calendar sync happens *after* the response.

---

## 6. Where to go next

- Run `pytest -v` and read the test names: they are a list of every behaviour the service guarantees.
- Run `python -m evals.run_booking_eval` and change a sentence in `appointment_service.py`: the eval shows which spoken answers changed.
- Open <http://127.0.0.1:8000/docs>, click **Authorize**, and try the requests from this guide.
- Break something on purpose (e.g. remove the `STATUS_RANK` check in `webhook_service.py`) and watch which test fails.
