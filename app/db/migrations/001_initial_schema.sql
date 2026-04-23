-- ════════════════════════════════════════════════════════════════
-- 001 — Initial schema
-- Creates: calls, messages, settings tables, triggers, stats view.
-- Idempotent — safe to run multiple times.
-- ════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── CALLS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calls (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
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
    agent_name      TEXT DEFAULT 'Sara',
    agency_name     TEXT DEFAULT 'Prestige Properties Dubai',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_status     ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_hot_lead   ON calls(hot_lead);
CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_phone      ON calls(phone);

-- ── MESSAGES ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id     UUID REFERENCES calls(id) ON DELETE CASCADE,
    call_sid    TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('customer', 'ai')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_call_sid    ON messages(call_sid);
CREATE INDEX IF NOT EXISTS idx_messages_created_at  ON messages(created_at);

-- ── SETTINGS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Seed defaults (first-run only)
INSERT INTO settings (key, value) VALUES
    ('agent_name',     'Sara'),
    ('agency_name',    'Prestige Properties Dubai'),
    ('system_prompt',  'default'),
    ('voice_id',       '95d51f79-c397-46f9-b49a-23763d3eaa2d'),
    ('llm_provider',   'groq'),
    ('groq_model',     'llama-3.3-70b-versatile'),
    ('openai_model',   'gpt-4o-mini'),
    ('openai_api_key', '')
ON CONFLICT (key) DO NOTHING;

-- ── updated_at trigger ───────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_updated_at ON calls;
CREATE TRIGGER set_updated_at BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS set_settings_updated_at ON settings;
CREATE TRIGGER set_settings_updated_at BEFORE UPDATE ON settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── Stats view ───────────────────────────────────────────────
CREATE OR REPLACE VIEW call_stats AS
SELECT
    COUNT(*)                                                                AS total_calls,
    COUNT(*) FILTER (WHERE hot_lead = TRUE)                                AS hot_leads,
    COUNT(*) FILTER (WHERE status IN ('answered','completed'))             AS answered,
    COUNT(*) FILTER (WHERE status IN ('no-answer','busy','failed'))        AS no_answer,
    COUNT(*) FILTER (WHERE status = 'ringing')                             AS ringing,
    COALESCE(ROUND(AVG(duration_sec) FILTER (WHERE duration_sec > 0)), 0)  AS avg_duration_sec,
    COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours')      AS calls_today,
    COUNT(*) FILTER (WHERE hot_lead AND started_at >= NOW() - INTERVAL '24 hours') AS hot_leads_today
FROM calls;
