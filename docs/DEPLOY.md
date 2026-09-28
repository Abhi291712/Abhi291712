# Deploying voice-ai-backend and connecting a real phone agent

This guide takes you from the repository to a phone number you can call, where a Retell AI agent answers, checks availability and books appointments through your backend.

Total time: about 30 minutes. Everything below has a free tier.

> Dashboards change over time, so button names in Render, Retell or Google may differ slightly from this guide. The concepts stay the same.

---

## 1. Deploy the backend on Render

The repository contains a [Render Blueprint](../render.yaml) that creates three things at once: the API (from the Dockerfile), a PostgreSQL database and a Redis-compatible Key Value store.

1. Push the repository to GitHub (already done if you are reading this there).
2. Sign in at <https://render.com> with GitHub.
3. Click **New > Blueprint**, select this repository and confirm.
4. Wait for the first deploy. The container runs `alembic upgrade head` automatically, then starts the server.
5. Open `https://<your-service>.onrender.com/health`. You should see:
   ```json
   {"status": "ok", "database": "ok", "version": "0.1.0"}
   ```
6. In the service's **Environment** tab, copy the generated `API_KEYS` value. You need it to call the API and to try `/docs`.

Free Render web services sleep after inactivity, and the first request after a nap takes a while. For a live phone demo, open `/health` a minute before calling, or use a paid instance.

### Alternative: any Docker host

```bash
docker build -t voice-ai-backend .
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  -e API_KEYS=... -e WEBHOOK_SECRET=... -e RETELL_API_KEY=... \
  voice-ai-backend
```

---

## 2. Create a Retell agent

1. Sign up at <https://www.retellai.com> and open the dashboard.
2. Copy your **API key** (Settings / API Keys). In Render, set it as `RETELL_API_KEY` and redeploy. Retell signs every request with this key, and the backend refuses Retell traffic without it.
3. Create a new agent (single prompt / LLM agent) and give it a voice.
4. Write the agent prompt. A starting point:

   ```text
   You are the friendly receptionist of Bright Smile Dental. You help callers book
   30-minute appointments between 9 AM and 5 PM. Always call check_availability before
   offering times, and only confirm a booking after book_appointment succeeds. Ask for the
   caller's full name and phone number before booking. Keep answers short; this is a phone
   call. If a function returns "ok": false, read its "message" to the caller.
   ```

   The agent must know today's date to turn "next Tuesday" into `2030-01-15`. Retell can insert the current date and time into the prompt with a dynamic variable; see Retell's docs on dynamic variables and add something like `Today is {{current_time}}.`

5. Set the business time zone in Render (`BUSINESS_TIMEZONE`, e.g. `America/New_York`) so "10 AM" means 10 AM where the business is.

---

## 3. Point Retell at the backend

### Webhook

In the agent's (or account's) webhook settings, set the URL to:

```
https://<your-service>.onrender.com/webhooks/retell
```

Retell then sends `call_started`, `call_ended` and `call_analyzed`. Each call appears in `GET /calls`, and its transcript, summary and bookings appear in `GET /calls/{id}/summary`.

### Custom functions

Add two custom functions to the agent.

**check_availability**: URL `https://<your-service>.onrender.com/retell/functions/check-availability`

```json
{
  "type": "object",
  "properties": {
    "date": {"type": "string", "description": "Day to check, format YYYY-MM-DD"}
  },
  "required": ["date"]
}
```

Description for the agent: *"Find free appointment times on a given day."*

**book_appointment**: URL `https://<your-service>.onrender.com/retell/functions/book-appointment`

```json
{
  "type": "object",
  "properties": {
    "customer_name": {"type": "string", "description": "Caller's full name"},
    "customer_phone": {"type": "string", "description": "Caller's phone number in E.164 format, e.g. +14155550123"},
    "start_time": {"type": "string", "description": "Appointment start, format YYYY-MM-DDTHH:MM:SS in the business's local time"}
  },
  "required": ["customer_name", "customer_phone", "start_time"]
}
```

Description for the agent: *"Book an appointment at a time returned by check_availability."*

The backend accepts both the full function payload (`{"call": ..., "args": ...}`) and the "args only" mode. With the full payload, bookings are automatically linked to the call.

### Phone number

Buy or import a phone number in Retell and attach it to the agent. Alternatively, use the dashboard's web call button to test in the browser.

---

## 4. Optional: Claude call analysis

1. Create an API key at <https://console.anthropic.com>.
2. In Render set `ANTHROPIC_API_KEY=<key>` and `LLM_ANALYSIS_ENABLED=true`.
3. After each call ends, `GET /calls/{id}/summary` includes an `analysis` object with intent, sentiment and follow-up flags. `POST /calls/{id}/analyze` re-runs it on demand.

Check quality with `python -m evals.run_analysis_eval --yes` (costs a few cents).

---

## 5. Optional: Google Calendar

1. In Google Cloud, create a project, enable the **Google Calendar API**, and create a **service account** with a JSON key.
2. In Google Calendar, share the business calendar with the service account's e-mail address and give it **Make changes to events** permission.
3. Upload the JSON key to your host as a secret file (on Render: **Secret Files**, e.g. `/etc/secrets/google.json`).
4. Set `GOOGLE_CALENDAR_ID` (shown in the calendar's settings, often an e-mail address) and `GOOGLE_SERVICE_ACCOUNT_FILE=/etc/secrets/google.json`.

Busy calendar time is now never offered, and each booking appears in the calendar a moment after it is made.

---

## 6. Verify the deployment

```bash
# Every scenario must pass, and p95 latency must be within budget.
python -m evals.run_booking_eval --base-url https://<your-service>.onrender.com --api-key <API_KEYS value>
```

This books test appointments in 2031, so run it against a test deployment, or delete those rows afterwards.

Watch it live:

- `GET /metrics` for request rates, latency histograms, `tool_latency_budget_exceeded_total`, webhook outcomes and LLM analysis outcomes.
- Render's **Logs** tab: JSON lines, each carrying a `request_id`.

## Security checklist

- Use long random values for `API_KEYS` and `WEBHOOK_SECRET` (the Blueprint generates them).
- Never commit `.env` or the Google JSON key; use the platform's secret storage.
- `/metrics` is public by default. Restrict it at the network level, or set `METRICS_ENABLED=false` if you don't scrape it.
