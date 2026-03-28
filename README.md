# CallSara — AI Real Estate Calling SaaS

Production-grade AI calling system built for UAE real estate agencies.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.11) |
| AI — LLM | Groq llama-3.3-70b-versatile |
| AI — TTS | Cartesia sonic-turbo |
| Telephony | Twilio (swap to Telnyx via env var) |
| Database | Supabase PostgreSQL (asyncpg) |
| Storage | Supabase Storage (recordings) |
| Deploy | Docker → Render.com |

## Project Structure

```
callsara_saas/
├── main.py                          # FastAPI entry point + page routes
├── requirements.txt
├── Dockerfile
│
├── app/
│   ├── core/
│   │   ├── config.py                # All settings (pydantic-settings)
│   │   ├── logging.py               # Structured logging
│   │   └── state.py                 # In-memory call session store
│   │
│   ├── api/v1/
│   │   ├── router.py                # Combines all endpoint routers
│   │   └── endpoints/
│   │       ├── calls.py             # POST/GET /api/v1/calls
│   │       ├── settings.py          # GET/POST /api/v1/settings
│   │       ├── status.py            # GET /api/v1/status
│   │       ├── audio.py             # GET /audio/intro, /audio/reply
│   │       └── webhooks.py          # POST /webhooks/twilio/*
│   │
│   └── services/
│       ├── settings_service.py      # Runtime settings cache
│       ├── ai/
│       │   ├── llm.py               # Groq API
│       │   ├── tts.py               # Cartesia API
│       │   └── reply_parser.py      # Parse [END_CALL]/[HOT_LEAD] tags
│       ├── telephony/
│       │   └── twilio_client.py     # Twilio call init + TwiML builders
│       └── storage/
│           └── supabase.py          # Upload recordings to Supabase
│
├── db/
│   ├── database.py                  # asyncpg connection pool
│   ├── schema.sql                   # Run once in Supabase SQL editor
│   └── repositories/
│       ├── calls.py                 # All call/message DB queries
│       └── settings.py              # All settings DB queries
│
├── frontend/
│   ├── pages/
│   │   ├── dashboard.html           # / — stats + quick dial + recent calls
│   │   ├── calls.html               # /calls — all calls with filters
│   │   ├── hot_leads.html           # /hot-leads — hot leads only
│   │   ├── dialer.html              # /dialer — single + bulk dial
│   │   └── settings.html            # /settings — agent config + prompt editor
│   ├── assets/
│   │   ├── css/main.css             # Global styles
│   │   └── js/
│   │       ├── api.js               # All API calls centralised
│   │       ├── utils.js             # Shared helpers (toast, format, etc.)
│   │       └── table.js             # Diff-based table renderer (no flicker)
│   └── components/layout.html       # Sidebar/topbar reference
│
└── scripts/
    └── setup_db.py                  # One-time DB schema setup
```

## Setup

### 1. Run SQL schema in Supabase
Dashboard → SQL Editor → New Query → paste `db/schema.sql` → Run

### 2. Configure environment
```bash
cp .env.example .env
# Required: BASE_URL, TWILIO_AUTH_TOKEN, SUPABASE_DB_HOST, SUPABASE_DB_PASS
```

### 3. Run locally
```bash
pip install -r requirements.txt
python main.py
```
Open http://localhost:8000

### 4. Deploy to Render
1. Push to GitHub
2. Render → New Web Service → Docker
3. Add env vars from `.env.example`

## API Reference

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/calls` | Initiate outbound call |
| GET | `/api/v1/calls` | List calls (paginated, filterable) |
| GET | `/api/v1/calls/stats` | Dashboard statistics |
| GET | `/api/v1/calls/{sid}/messages` | Call transcript |
| GET | `/api/v1/settings` | Get all settings |
| POST | `/api/v1/settings` | Update settings |
| POST | `/api/v1/settings/reset-prompt` | Reset AI prompt to default |
| GET | `/api/v1/status` | App health + config |
| GET | `/audio/intro` | Intro TTS audio (WAV) |
| GET | `/audio/reply?sid=X` | Reply TTS audio (WAV) |
| POST | `/webhooks/twilio/greeting` | Twilio answer webhook |
| POST | `/webhooks/twilio/process-speech` | Speech → AI → TTS |
| POST | `/webhooks/twilio/recording-status` | Upload recording to Supabase |
| POST | `/webhooks/twilio/call-status` | Finalise call in DB |

Full Swagger docs: `/api/docs`

## Pages

| URL | Page |
|---|---|
| `/` | Dashboard — stats + recent calls |
| `/calls` | All calls — full table with filters |
| `/hot-leads` | Hot leads only |
| `/dialer` | Single call + bulk dial |
| `/settings` | Agent identity + AI prompt editor |

## Key Behaviours

- **No intro repeat** — `?started=1` param on redirect prevents intro replaying on silence
- **No table flicker** — diff-based row updates, only changed rows re-render
- **Safe DB calls** — all DB calls wrapped, call never drops on DB error
- **Recording upload** — separate `/recording-status` webhook handles upload after call ends
- **Runtime settings** — agent name, prompt, intro loaded from DB, cached in memory, updated live
