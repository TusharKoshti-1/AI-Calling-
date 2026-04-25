-- ════════════════════════════════════════════════════════════════
-- RESET_AND_REBUILD.sql
--
-- ⚠️  DESTRUCTIVE  ⚠️
--
-- Drops every application table (including users and sessions) and
-- recreates the schema from scratch. All call history, messages,
-- settings, and user accounts are deleted.
-- You will need to sign up again after running this.
--
-- The AI's "memory" within a single call is held entirely in process
-- memory and lives in `state.history` — when the call ends, that
-- memory is discarded. There is no DB table for cross-call memory.
--
-- Run this once in the Supabase SQL editor (or `psql`) and you're done.
-- After it finishes:
--   1. Deploy the new code.
--   2. Visit /signup → create your account.
--   3. Settings → fill in agent_name, agency_name, voice, system
--      prompt, OpenAI API key, transfer_number.
--   4. Place a test call.
-- ════════════════════════════════════════════════════════════════


-- ─── 1. DROP EVERYTHING ─────────────────────────────────────────
-- Order matters: drop children before parents, or use CASCADE.

DROP TABLE IF EXISTS public.customer_memory CASCADE;  -- if it existed previously
DROP TABLE IF EXISTS public.messages        CASCADE;
DROP TABLE IF EXISTS public.calls           CASCADE;
DROP TABLE IF EXISTS public.settings        CASCADE;
DROP TABLE IF EXISTS public.sessions        CASCADE;
DROP TABLE IF EXISTS public.users           CASCADE;

DROP VIEW     IF EXISTS public.call_stats     CASCADE;
DROP FUNCTION IF EXISTS public.call_stats_for(uuid) CASCADE;
DROP FUNCTION IF EXISTS public.update_updated_at()  CASCADE;


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
CREATE TABLE public.settings (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    label       TEXT,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX settings_user_key_uniq
    ON public.settings (user_id, key);

CREATE INDEX idx_settings_user ON public.settings (user_id);

CREATE TRIGGER set_settings_updated_at
BEFORE UPDATE ON public.settings
FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();


-- ─── 9. STATS FUNCTION (per-user) ───────────────────────────────
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


-- ─── DONE ───────────────────────────────────────────────────────
-- Sanity check (uncomment to run):
-- SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
--   → expected: calls, messages, sessions, settings, users
