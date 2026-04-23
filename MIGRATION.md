# Migrating from the legacy layout

This is a practical checklist for moving an existing deployment of the flat
`callbot/` project (single `main.py`, `config.py`, `db/`, `services/`, `static/`)
to the new `app/` package layout.

Your **database does not need to change** — the new schema is a direct clone of
the legacy one. Your Twilio number config does not need to change either —
the legacy webhook paths are preserved as aliases.

---

## 0 · ROTATE LEAKED KEYS FIRST

The legacy `config.py` committed live keys to git. Before anything else, rotate
them at the vendors. They are in your git history permanently.

- **Groq** – Groq Cloud console → API Keys → revoke + create new
- **Cartesia** – Dashboard → API Keys → revoke + create new
- **Supabase service role** – Project → Settings → API → *Generate new service role JWT*
- **Twilio auth token** – Console → Account → API keys & tokens → rotate

You do NOT need to rotate the Supabase `SUPABASE_URL` or project ref — those
are not secrets.

---

## 1 · Environment

Create `.env` in the new project root:

```bash
cp .env.example .env
```

Copy values from your legacy `config.py` into `.env` — **using the newly
rotated keys**, not the originals. Add these extra ones that didn't exist before:

| New var | What to set |
|---------|-------------|
| `ADMIN_API_KEY` | Any long random string — the dashboard will prompt for it on first load and cache it in `localStorage`. |
| `VERIFY_TWILIO_SIGNATURE` | `true` in production, `false` during dev-tunnel testing. |
| `APP_ENV` | `production` when deploying. |

Verify `.env` is gitignored before committing anything.

---

## 2 · Database

The legacy `db/schema.sql` and `db/migration_add_llm_settings.sql` are replaced
by the single consolidated file `app/db/migrations/001_initial_schema.sql`.

If you already have the legacy schema in Supabase, you do **not** need to
re-run the migration — the tables are identical. If you're bootstrapping a
fresh project, run the new file once in the Supabase SQL editor.

---

## 3 · Twilio

No action required. The new app exposes both the clean new webhook URLs:

```
POST /webhooks/twilio/greeting
POST /webhooks/twilio/process-speech
POST /webhooks/twilio/call-status
POST /webhooks/twilio/recording-status
GET  /webhooks/twilio/opening-audio
GET  /webhooks/twilio/reply-audio
```

…and the legacy paths as aliases:

```
POST /twiml-greeting     → /webhooks/twilio/greeting
POST /process-speech     → /webhooks/twilio/process-speech
POST /call-status        → /webhooks/twilio/call-status
POST /recording-status   → /webhooks/twilio/recording-status
GET  /opening-audio      → /webhooks/twilio/opening-audio
GET  /reply-audio        → /webhooks/twilio/reply-audio
```

When `/api/call` initiates a new call it registers the **new** webhook URLs
with Twilio — so the legacy aliases only exist to serve calls already in
flight during a deploy. You can remove them later if you want.

---

## 4 · Deployment command change

The legacy Dockerfile ran:

```dockerfile
CMD ["python", "main.py"]
```

The new Dockerfile runs:

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*"]
```

If your hosting provider (Render, Fly, Railway) has a "Start command" override,
update it to match.

---

## 5 · Feature parity cross-check

Everything the legacy app did still works:

| Legacy behaviour | New location |
|------------------|--------------|
| `main.py` `_settings` dict | `SettingsService` cache, backed by `settings` table |
| Per-call `_call_state` dict | `CallOrchestrator._state` (same in-memory semantics) |
| `_opening_audio_cache` | `CallState.opening_audio` |
| Fire-and-forget DB writes | `_spawn()` helper in `call_orchestrator.py` |
| Groq / OpenAI switch | `LLMRegistry.get(provider)` |
| Arabic-voice model override | `CartesiaProvider._model_for` |
| Substring END_PHRASES match | Word-boundary regex (bug fix) |
| Unauthenticated admin API | `require_admin_api_key` dependency |
| Unverified Twilio webhooks | `verify_twilio_signature` dependency |
| 1493-line index.html | Shell + `/static/css/app.css` + 3 JS modules |

---

## 6 · Dashboard login

Because admin endpoints now require `X-API-Key`, the dashboard will:

1. Try every `/api/*` call without a key.
2. If the server returns 401, prompt for the key once.
3. Store the entered key in `localStorage` under `callsara_admin_key`.
4. Send it as `X-API-Key` on every subsequent request.

To revoke access on a browser, clear `localStorage` or rotate
`ADMIN_API_KEY` on the server (all clients will be prompted again).

---

## 7 · Rollback plan

If anything goes wrong, the database and Twilio config are unchanged, so
redeploying the legacy commit restores service. The only thing you can't roll
back is the rotated keys — but those needed to rotate anyway.
