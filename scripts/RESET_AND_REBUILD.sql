-- ════════════════════════════════════════════════════════════════
-- RESET_AND_REBUILD.sql
--
-- ⚠️  DESTRUCTIVE  ⚠️
--
-- This script DROPS every application table (including users and
-- sessions) and recreates the schema from scratch. All call history,
-- messages, customer memory, settings, and user accounts are deleted.
-- You will need to sign up again after running this.
--
-- Run it once in the Supabase SQL editor (or `psql`) and you're done.
-- After it finishes: deploy the new code, go to /signup, create your
-- account, and customise your settings from the Settings page.
--
-- Design notes
-- ────────────
-- • NO hardcoded business names anywhere. Column defaults are NULL or
--   generic. Every tenant sets their own agent_name / agency_name /
--   system_prompt / voice in their per-user settings.
-- • The "settings" table is per-user — there is no global seed row.
--   When a user hasn't set a key yet, the app falls back to the
--   hard-coded env defaults in app/core/config.py (empty by design).
-- • Recording storage (Supabase Storage bucket) is NOT touched by this
--   script. If you also want to wipe old WAVs, do that from the
--   Supabase Storage UI separately.
-- ════════════════════════════════════════════════════════════════


-- ─── 1. DROP EVERYTHING ─────────────────────────────────────────
-- Order matters: drop children before parents, or use CASCADE.

DROP TABLE IF EXISTS public.customer_memory CASCADE;
DROP TABLE IF EXISTS public.messages        CASCADE;
DROP TABLE IF EXISTS public.calls           CASCADE;
DROP TABLE IF EXISTS public.settings        CASCADE;
DROP TABLE IF EXISTS public.sessions        CASCADE;
DROP TABLE IF EXISTS public.users           CASCADE;

-- Old stats view / function left over from previous migrations.
DROP VIEW     IF EXISTS public.call_stats     CASCADE;
DROP FUNCTION IF EXISTS public.call_stats_for(uuid) CASCADE;

-- updated_at trigger function — safe to recreate cleanly.
DROP FUNCTION IF EXISTS public.update_updated_at() CASCADE;


-- ─── 2. EXTENSIONS ──────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ─── 3. updated_at trigger helper ───────────────────────────────
CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ─── 4. USERS ───────────────────────────────────────────────────
CREATE TABLE public.users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

CREATE INDEX idx_users_email ON public.users (LOWER(email));

CREATE TRIGGER set_users_updated_at
BEFORE UPDATE ON public.users
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


-- ─── 5. SESSIONS ────────────────────────────────────────────────
-- Session records (not JWTs) so we can revoke server-side on signout.
CREATE TABLE public.sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    user_agent      TEXT,
    ip              TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX idx_sessions_user       ON public.sessions (user_id);
CREATE INDEX idx_sessions_token_hash ON public.sessions (token_hash);
CREATE INDEX idx_sessions_expires_at ON public.sessions (expires_at);


-- ─── 6. CALLS ───────────────────────────────────────────────────
-- NOTE: No hardcoded agent_name / agency_name defaults here.
-- The app populates these from the user's own settings on INSERT.
CREATE TABLE public.calls (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID REFERENCES public.users(id) ON DELETE SET NULL,
    sid             TEXT UNIQUE NOT NULL,
    phone           TEXT NOT NULL,
    from_number     TEXT,
    status          TEXT DEFAULT 'ringing',
    hot_lead        BOOLEAN DEFAULT FALSE,
    duration_sec    INTEGER DEFAULT 0,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    recording_url   TEXT,
    recording_path  TEXT,
    transcript      TEXT,
    agent_name      TEXT,
    agency_name     TEXT,
    provider        TEXT DEFAULT 'twilio',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_calls_user_id    ON public.calls (user_id);
CREATE INDEX idx_calls_status     ON public.calls (status);
CREATE INDEX idx_calls_hot_lead   ON public.calls (hot_lead);
CREATE INDEX idx_calls_started_at ON public.calls (started_at DESC);
CREATE INDEX idx_calls_phone      ON public.calls (phone);

CREATE TRIGGER set_calls_updated_at
BEFORE UPDATE ON public.calls
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


-- ─── 7. MESSAGES ────────────────────────────────────────────────
CREATE TABLE public.messages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID REFERENCES public.users(id) ON DELETE SET NULL,
    call_id     UUID REFERENCES public.calls(id) ON DELETE CASCADE,
    call_sid    TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('customer', 'ai')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_messages_user_id    ON public.messages (user_id);
CREATE INDEX idx_messages_call_sid   ON public.messages (call_sid);
CREATE INDEX idx_messages_created_at ON public.messages (created_at);


-- ─── 8. SETTINGS (per-user) ─────────────────────────────────────
-- Every setting is owned by a user. The UI/API creates rows when the
-- user saves their Settings page. When a key is missing, the app
-- falls back to the default baked into app/core/config.py.
--
-- No global seed rows — multi-tenant means every business brings its
-- own agent_name, agency_name, and system_prompt.
CREATE TABLE public.settings (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    label       TEXT,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- One value per (user, key). Unlike the old schema this has no
-- NULL-sentinel trickery — every row has a real user_id.
CREATE UNIQUE INDEX settings_user_key_uniq
    ON public.settings (user_id, key);

CREATE INDEX idx_settings_user ON public.settings (user_id);

CREATE TRIGGER set_settings_updated_at
BEFORE UPDATE ON public.settings
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


-- ─── 9. CUSTOMER MEMORY ─────────────────────────────────────────
-- One row per (user_id, phone). Loaded into the system prompt at the
-- start of every call so the AI "remembers" past context.
CREATE TABLE public.customer_memory (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id            UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    phone              TEXT NOT NULL,

    -- Generic, domain-agnostic memory fields. The AI's post-call
    -- extraction writes whichever of these it can infer from the
    -- transcript. Different verticals will use them differently
    -- (car service → car_model, real estate → property_type, clinic
    -- → last_appointment_reason) — the fact-extractor prompt is
    -- currently tuned for car service but users can edit their
    -- extractor by changing their own system_prompt setting.
    topic_summary      TEXT,    -- what is this customer interested in / why we're calling
    last_call_summary  TEXT,    -- one-line recap of the previous call
    preferred_callback TEXT,    -- "evenings", "after 6pm", etc.
    notes              TEXT,    -- free-form, anything useful for next call
    last_lead_status   TEXT,    -- 'hot' | 'warm' | 'cold' | NULL

    -- Domain-specific facts we extract when relevant. These are
    -- optional — the fact-extractor omits any field it can't fill.
    car_model          TEXT,    -- used by car-service vertical
    service_status     TEXT,    -- "due" | "overdue" | "done recently" | ...

    call_count         INTEGER NOT NULL DEFAULT 0,
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT customer_memory_user_phone_uniq UNIQUE (user_id, phone)
);

CREATE INDEX idx_customer_memory_user_phone
    ON public.customer_memory (user_id, phone);

CREATE TRIGGER set_customer_memory_updated_at
BEFORE UPDATE ON public.customer_memory
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


-- ─── 10. STATS FUNCTION (per-user) ──────────────────────────────
CREATE OR REPLACE FUNCTION public.call_stats_for(p_user UUID)
RETURNS TABLE (
    total_calls       BIGINT,
    hot_leads         BIGINT,
    answered          BIGINT,
    no_answer         BIGINT,
    ringing           BIGINT,
    avg_duration_sec  NUMERIC,
    calls_today       BIGINT,
    hot_leads_today   BIGINT
)
LANGUAGE SQL STABLE AS $$
    SELECT
        COUNT(*)                                                              AS total_calls,
        COUNT(*) FILTER (WHERE hot_lead = TRUE)                              AS hot_leads,
        COUNT(*) FILTER (WHERE status IN ('answered','completed'))           AS answered,
        COUNT(*) FILTER (WHERE status IN ('no-answer','busy','failed'))      AS no_answer,
        COUNT(*) FILTER (WHERE status = 'ringing')                           AS ringing,
        COALESCE(ROUND(AVG(duration_sec) FILTER (WHERE duration_sec > 0)),0) AS avg_duration_sec,
        COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours')    AS calls_today,
        COUNT(*) FILTER (WHERE hot_lead AND started_at >= NOW() - INTERVAL '24 hours') AS hot_leads_today
    FROM public.calls
    WHERE user_id = p_user;
$$;


-- ─── 11. Sanity check ───────────────────────────────────────────
-- Run these after the script to verify everything is in order.
-- (Commented out — uncomment if you want them to run automatically.)
--
-- SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
--   → expected: calls, customer_memory, messages, sessions, settings, users
--
-- SELECT COUNT(*) FROM public.users;     -- 0
-- SELECT COUNT(*) FROM public.calls;     -- 0
-- SELECT COUNT(*) FROM public.messages;  -- 0
-- SELECT COUNT(*) FROM public.settings;  -- 0


-- ─── DONE ───────────────────────────────────────────────────────
-- After this script completes:
--   1. Redeploy the app (if not already).
--   2. Visit your app URL → /signup → create a fresh account.
--   3. Go to Settings → fill in agent name, agency name, voice, system
--      prompt, OpenAI API key. Nothing is pre-filled with anyone else's
--      business branding.
--   4. Place a test call.
