# Recording a 2-minute demo

A short screen recording of a real call is the most convincing way to show this project. This script shows the phone call first, then proves what happened behind the scenes.

## Before recording

- Deploy and connect Retell (see [DEPLOY.md](DEPLOY.md)), with `LLM_ANALYSIS_ENABLED=true`.
- Open `/health` to wake the service up (free hosting sleeps).
- Prepare browser tabs: the Retell dashboard, `https://<service>/docs` (authorised with your API key), and optionally Google Calendar.
- Use a screen recorder that captures system audio (e.g. OBS, or QuickTime plus a loopback device).

## Script

**0:00-0:15: What it is.** Show the README architecture diagram.
> "This is a backend that lets an AI voice agent run a dental front desk. The agent is Retell; the business logic, booking, and call analysis run here."

**0:15-1:00: The call.** Call the number (or use Retell's web call) and have a natural conversation:
> "Hi, I'd like to book a cleaning next Tuesday."
> *(agent offers times from `check_availability`)*
> "10 AM, please. My name is Jane Doe, number +1 415 555 0123."
> *(agent confirms after `book_appointment` succeeds)*

Then try the conflict path: call again and ask for the same slot. The agent should offer alternatives. That sentence came from the `409` handling.

**1:00-1:40: Behind the scenes.** In `/docs`:
1. `GET /calls`: the call is there, status `ended`.
2. `GET /calls/{id}/summary`: show the transcript, the event timeline (`call_started`, `call_ended`, `call_analyzed`), the linked appointment and Claude's `analysis` (intent `book_appointment`, sentiment, follow-up flag).
3. Optional: the event in Google Calendar.

**1:40-2:00: Engineering highlights.** Show a terminal:
```bash
pytest -q                            # 140+ tests
python -m evals.run_booking_eval     # scenarios + p95 latency
```
> "Webhooks are signed, deduplicated and retried. Tool latency is measured against a budget, and an eval in CI checks every sentence the agent says."

## Tips

- Keep the audio clean: a headset avoids the agent hearing itself.
- If something goes wrong on camera, `GET /calls/{id}/summary` and the logs (search the `request_id`) show exactly what happened, which is a good demo too.
