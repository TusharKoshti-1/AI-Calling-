# CallSara — Multi-Tenant AI Calling Platform

FastAPI + Supabase Postgres + Supabase Storage + Twilio + Groq/OpenAI + Cartesia TTS.

**Multi-tenant SaaS:** every account gets its own isolated workspace —
their own calls, hot leads, system prompt, agent name, voice, and LLM
credentials. Data never crosses tenant boundaries.

---

## 1 · Quick start (local)

```bash
cp .env.example .env             # then fill in real secrets
pip install -r requirements-dev.txt

# Run migrations against your Supabase project (SQL editor):
#   1. app/db/migrations/001_initial_schema.sql   (skip if already applied)
#   2. app/db/migrations/002_multi_tenant.sql

python -m app.main               # http://localhost:8000
```

Or with Docker:

```bash
cp .env.example .env
docker compose up --build
```

1. Open [http://localhost:8000](http://localhost:8000) → you'll be redirected to `/signin`.
2. Click **Create one** → sign up.
3. **The first user to sign up becomes an admin automatically.**
4. You're now in a fresh, isolated workspace. Everything you do —
   calls, settings, hot leads, system prompt — is scoped to you.

---

## 2 · Authentication model

- **Cookie-based sessions, no shared API key.** Signing in sets an
  HttpOnly cookie that the browser carries on every `/api/*` request.
- **Server-side session records.** Signout revokes the session
  immediately. Changing your password revokes every session for your
  account.
- **Tokens are signed** (HMAC prefix) and **hashed in the DB** (SHA-256).
  A DB leak cannot produce usable session tokens.
- **First user = admin.** Subsequent signups are regular users. Set
  `ALLOW_PUBLIC_SIGNUP=false` to close signups after the first one.

No more admin API key prompts. No more localStorage tokens. Standard web
auth.

---

## 3 · Project layout

```
app/
├── main.py                      App factory + per-page routing
├── core/
│   ├── config.py                Typed env settings (pydantic-settings)
│   ├── logging.py
│   ├── exceptions.py            AppError → HTTP response
│   ├── passwords.py             bcrypt hashing
│   ├── session_tokens.py        signed opaque session tokens
│   └── security.py              get_current_user, get_optional_user,
│                                verify_twilio_signature
├── db/
│   ├── session.py               asyncpg pool (Supavisor-safe)
│   ├── repositories/
│   │   ├── users.py             (new)
│   │   ├── sessions.py          (new)
│   │   ├── calls.py             user-scoped
│   │   ├── messages.py          user-scoped
│   │   └── settings.py          per-user key/value
│   └── migrations/
│       ├── 001_initial_schema.sql
│       └── 002_multi_tenant.sql
├── schemas/                     Pydantic request/response models
├── services/
│   ├── llm/                     Pluggable, stateless providers
│   ├── tts/                     Cartesia adapter
│   ├── telephony/               Twilio REST + TwiML builders
│   ├── storage/                 Supabase Storage
│   ├── text_cleaner.py          LLM output sanitiser
│   ├── prompts.py               Default system prompt
│   ├── settings_service.py      Per-user cache, keyed by user_id
│   └── call_orchestrator.py     Per-call state (knows user_id)
├── api/v1/
│   ├── router.py
│   └── endpoints/
│       ├── auth.py              signup / signin / signout / me
│       ├── calls.py             user-scoped
│       ├── settings.py          user-scoped
│       ├── voice.py             user-scoped
│       ├── status.py            user-scoped
│       ├── health.py            public liveness probe
│       └── twilio_webhooks.py   HMAC-verified
└── static/
    ├── css/
    │   ├── app.css              Main styles (unchanged from legacy)
    │   └── auth.css             Signin/signup styling
    ├── js/
    │   ├── api.js               Cookie-based fetch wrapper
    │   ├── layout.js            Sidebar + topbar renderer
    │   └── pages/
    │       ├── calls-table.js   Shared diff-based table
    │       ├── modal.js         Call-detail overlay
    │       ├── dialer.js        Single + bulk dial
    │       ├── stats.js         Stats + hot-leads widget
    │       ├── dashboard.js     /dashboard controller
    │       ├── calls.js         /calls controller
    │       ├── hot.js           /hot controller
    │       ├── settings.js      /settings controller
    │       └── voice.js         /voice controller
    └── pages/
        ├── signin.html
        ├── signup.html
        ├── dashboard.html
        ├── calls.html
        ├── hot.html
        ├── dialer.html
        ├── settings.html
        └── voice.html
```

Every page is its own HTML file loaded via a dedicated server route.
The shared sidebar/topbar is injected by `layout.js` at load time so the
markup still lives in exactly one place.

---

## 4 · Per-request data flow (multi-tenant)

```
  POST /api/call                     POST /webhooks/twilio/process-speech
      ▼                                          ▼
  get_current_user  ────► user_id       Twilio signature verify
      ▼                                          ▼
  orchestrator.register_outbound       lookup user_id by SID
      ▼                                          ▼
  insert into calls (user_id=…)        settings_service.for_user(user_id)
                                                 ▼
                                       llm_registry.get(user's provider)
                                                 ▼
                                       provider.complete(… api_key=user's key)
                                                 ▼
                                       tts.synthesize(… voice=user's voice)
```

Everything downstream of the webhook uses the owning user's settings,
credentials, and voice. Tenants cannot observe each other's calls or
recordings.

---

## 5 · Environment variables

See `.env.example` for the full list. The SaaS-specific ones:

| Var | Required? | Notes |
|-----|-----------|-------|
| `SESSION_SECRET` | **Yes in prod** | Long random string. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `SESSION_TTL_HOURS` | No | Default 720 (30 days). |
| `SESSION_COOKIE_SECURE` | Recommended | `true` when behind HTTPS. |
| `ALLOW_PUBLIC_SIGNUP` | No | `false` after first admin created to lock down signups. |
| `VERIFY_TWILIO_SIGNATURE` | Recommended | `true` in production. |

---

## 6 · Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

The pure-Python tests (text cleaner, phone normalisation, HMAC signing,
password hashing, session tokens) run without a live DB. Integration
tests that need the DB are tagged and skipped by default.

---

## 7 · Scaling beyond one worker

- **API layer** scales horizontally already — it's stateless except
  for the in-process `_CallStateStore` inside `CallOrchestrator`.
- **To run `--workers N > 1`** swap `_CallStateStore` for a Redis-backed
  implementation. The orchestrator's public API does not change.
- **Session cache**: `SettingsService` caches per-user settings in
  process memory — safe to run multiple workers because each worker
  keeps its own cache. Writes invalidate the cache on the worker that
  served the write; other workers pick up fresh values within seconds
  via the short cache lifetime. For strict consistency you can
  broadcast invalidations over Postgres `LISTEN/NOTIFY` or Redis pub/sub.

---

## 8 · License

MIT. See `LICENSE`.
