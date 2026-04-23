# CallSara — AI Outbound Calling Platform

FastAPI + Supabase Postgres + Supabase Storage + Twilio + Groq/OpenAI + Cartesia TTS.

Outbound calls are placed via Twilio, live speech is piped through an LLM,
and replies are synthesised by Cartesia. Every turn is persisted to Postgres;
full-call MP3 recordings are uploaded to Supabase Storage. A dashboard shows
calls in near-real-time and lets you reconfigure the agent, prompt, and voice.

---

## 1 · Quick start (local)

```bash
cp .env.example .env             # then fill in real secrets
pip install -r requirements-dev.txt
# one-time: run the SQL in app/db/migrations/001_initial_schema.sql
#           against your Supabase project via the SQL editor.
python -m app.main                # http://localhost:8000
```

Or with Docker:

```bash
cp .env.example .env
docker compose up --build
```

Hit [http://localhost:8000](http://localhost:8000). If `ADMIN_API_KEY` is set, the dashboard will
prompt once for it and persist it in `localStorage`.

---

## 2 · Project layout

```
app/
├── main.py                      App factory + lifespan
├── core/                        Cross-cutting: config, logging, security, exceptions
├── db/
│   ├── session.py               asyncpg pool (Supavisor-safe)
│   ├── repositories/            One per aggregate (calls, messages, settings)
│   └── migrations/              Ordered .sql files
├── schemas/                     Pydantic request/response models
├── services/
│   ├── llm/                     Pluggable providers (groq, openai) behind a Protocol
│   ├── tts/                     Cartesia; swap-in-ready for ElevenLabs, etc.
│   ├── telephony/               Twilio REST + TwiML builders
│   ├── storage/                 Supabase Storage
│   ├── text_cleaner.py          LLM output sanitiser + tag extraction
│   ├── prompts.py               Default system prompt
│   ├── settings_service.py      DB-backed runtime settings cache
│   └── call_orchestrator.py     The per-call state machine
├── api/v1/
│   ├── router.py                Aggregates every endpoint module
│   └── endpoints/               One file per resource
└── static/                      Single-page dashboard (vanilla JS, no build step)

tests/                           pytest + pytest-asyncio
scripts/
Dockerfile / docker-compose.yml / requirements*.txt / pyproject.toml
.env.example                     Every setting documented; never commit .env
```

Why this shape:

- **`core/`** – owned by platform engineers; rarely changes.
- **`db/repositories/`** – one file per aggregate. Add a new entity by adding a new repo.
- **`services/`** – domain logic. No HTTP, no SQL. Testable in isolation.
- **`api/v1/endpoints/`** – thin HTTP shells that wire schemas → services → repositories. Add a new route by dropping a module here and adding one line to `router.py`.

---

## 3 · Architecture (single request path)

```
Twilio  ──POST /webhooks/twilio/process-speech──▶  twilio_webhooks.py
                                                       │
                                                       ▼
                                            CallOrchestrator.handle_speech
                                                       │
                   ┌───────────────────────────────────┼────────────────────┐
                   ▼                                   ▼                    ▼
           llm_registry.get()              tts_provider.synthesize()   repositories/*
              (Groq or OpenAI)              (Cartesia, async task)     (DB writes are
                                                                        fire-and-forget)
                                                       │
                                                       ▼
                                          TwiML returned to Twilio
```

Latency-critical: the TTS task is **started before** TwiML is returned, so by
the time Twilio fetches the `<Play>` URL the audio is ready. DB writes are
`create_task`’d — they never block the call path.

---

## 4 · Environment variables

All vars are documented with examples in `.env.example`. Highlights:

| Var | Required? | Notes |
|-----|-----------|-------|
| `ADMIN_API_KEY` | **Yes in prod** | Guards every `/api/*` admin route. Dev can omit. |
| `VERIFY_TWILIO_SIGNATURE` | Recommended | HMAC-verify `/webhooks/twilio/*` callbacks. |
| `BASE_URL` | Yes | Used to build Twilio callback URLs. Must be externally reachable. |
| `SUPABASE_DB_HOST` / `SUPABASE_DB_USER` / `SUPABASE_DB_PASSWORD` | Yes | Use the pooler host (port 6543). |
| `TWILIO_*` | Yes | Account SID, auth token, caller ID. |
| `GROQ_API_KEY` or `OPENAI_API_KEY` | At least one | Selected provider must have creds. |
| `CARTESIA_API_KEY` | Yes | TTS. |

The OpenAI key can also be entered in the dashboard (stored in the `settings` table).

---

## 5 · Running tests

```bash
pip install -r requirements-dev.txt
pytest                  # unit tests — no DB or network needed
```

---

## 6 · Adding things

### A new API endpoint
1. Add a module in `app/api/v1/endpoints/`.
2. Expose a `router = APIRouter()` with `dependencies=[Depends(require_admin_api_key)]` if admin-only.
3. Include it in `app/api/v1/router.py`.

### A new LLM provider (e.g. Anthropic)
1. Add `app/services/llm/anthropic.py` exposing a class that implements `LLMProvider`.
2. Register it in `app/services/llm/registry.py`.
3. Add the literal string to `Settings.llm_provider` in `app/core/config.py` and to the `SettingsUpdate` regex pattern.
4. That’s it — the dashboard dropdown + orchestrator pick it up automatically.

### A new TTS provider
Same pattern as LLM — implement `TTSProvider`, swap the module-level `tts_provider`
singleton in `app/services/tts/__init__.py`, or make the selection dynamic.

### A new table
1. Add an SQL migration file `app/db/migrations/00N_your_change.sql`.
2. Add a repository in `app/db/repositories/` with the SQL wrapped in methods.
3. Services call the repo; nothing else touches SQL.

---

## 7 · Deployment

The Dockerfile is production-grade:

- `python:3.11-slim` base
- Non-root user (uid 10001)
- `uvicorn --workers 2 --proxy-headers` as entrypoint

Render / Fly / Railway / any container host will work. Set all env vars per
`.env.example` in the dashboard. Set `APP_ENV=production` to suppress `/docs`
and enforce that `ADMIN_API_KEY` is configured.

Webhook URLs you’ll configure in Twilio (or via the `/api/call` handler which
sets them automatically):

- `POST {BASE_URL}/webhooks/twilio/greeting`
- `POST {BASE_URL}/webhooks/twilio/process-speech`
- `POST {BASE_URL}/webhooks/twilio/call-status`
- `POST {BASE_URL}/webhooks/twilio/recording-status`

Legacy paths (`/twiml-greeting`, `/process-speech`, `/call-status`,
`/recording-status`, `/opening-audio`, `/reply-audio`) are kept as aliases so
existing Twilio numbers keep working during the cutover.

---

## 8 · Scaling beyond one worker

The `CallOrchestrator` currently keeps per-call state (history, pre-generated
TTS audio) in process memory. This is fine for a single worker. To run
`--workers N > 1`, swap `_CallStateStore` in
`app/services/call_orchestrator.py` for a Redis-backed implementation — the
public API of the orchestrator does not change.

---

## 9 · License

MIT. See `LICENSE`.
