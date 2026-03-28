-- ============================================================
-- CallSara SaaS — PostgreSQL Schema
-- Run in Supabase SQL Editor: Dashboard → SQL Editor → New Query
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── CALLS ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calls (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sid             TEXT UNIQUE NOT NULL,       -- Telephony provider call SID
    phone           TEXT NOT NULL,
    from_number     TEXT,
    status          TEXT DEFAULT 'ringing',     -- ringing|answered|completed|no-answer|busy|failed
    hot_lead        BOOLEAN DEFAULT FALSE,
    duration_sec    INTEGER DEFAULT 0,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    recording_url   TEXT,                       -- Supabase Storage public URL
    recording_path  TEXT,                       -- Storage path (for management)
    transcript      TEXT,                       -- Full conversation text
    agent_name      TEXT DEFAULT 'Sara',
    agency_name     TEXT,
    provider        TEXT DEFAULT 'twilio',      -- twilio | telnyx
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_calls_status      ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_hot_lead    ON calls(hot_lead);
CREATE INDEX IF NOT EXISTS idx_calls_started_at  ON calls(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_phone       ON calls(phone);
CREATE INDEX IF NOT EXISTS idx_calls_provider    ON calls(provider);

-- ── MESSAGES ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id     UUID REFERENCES calls(id) ON DELETE CASCADE,
    call_sid    TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('customer', 'ai')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_call_sid ON messages(call_sid);
CREATE INDEX IF NOT EXISTS idx_messages_call_id  ON messages(call_id);

-- ── SETTINGS ──────────────────────────────────────────────────
-- Per-tenant customisable settings. key is namespaced as "scope.name"
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    label       TEXT,           -- Human-readable label for UI
    description TEXT,           -- Help text shown in settings UI
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── CONTACTS ──────────────────────────────────────────────────
-- Phone number list for bulk campaigns
CREATE TABLE IF NOT EXISTS contacts (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    phone       TEXT NOT NULL,
    name        TEXT,
    notes       TEXT,
    tags        TEXT[],
    do_not_call BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts(phone);

-- ── CAMPAIGNS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    status      TEXT DEFAULT 'draft',   -- draft|running|paused|completed
    total       INTEGER DEFAULT 0,
    called      INTEGER DEFAULT 0,
    hot_leads   INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── TRIGGERS ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_calls_updated    ON calls;
DROP TRIGGER IF EXISTS trg_settings_updated ON settings;
DROP TRIGGER IF EXISTS trg_campaigns_updated ON campaigns;

CREATE TRIGGER trg_calls_updated    BEFORE UPDATE ON calls    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_settings_updated BEFORE UPDATE ON settings FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_campaigns_updated BEFORE UPDATE ON campaigns FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── STATS VIEW ────────────────────────────────────────────────
CREATE OR REPLACE VIEW call_stats AS
SELECT
    COUNT(*)                                                               AS total_calls,
    COUNT(*) FILTER (WHERE hot_lead = TRUE)                               AS hot_leads,
    COUNT(*) FILTER (WHERE status IN ('answered','completed'))            AS answered,
    COUNT(*) FILTER (WHERE status IN ('no-answer','busy','failed'))       AS no_answer,
    COUNT(*) FILTER (WHERE status = 'ringing')                            AS ringing,
    COALESCE(ROUND(AVG(duration_sec) FILTER (WHERE duration_sec > 0)),0) AS avg_duration_sec,
    COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours')    AS calls_today,
    COUNT(*) FILTER (WHERE hot_lead AND started_at >= NOW() - INTERVAL '24 hours') AS hot_leads_today
FROM calls;

-- ── DEFAULT SETTINGS ──────────────────────────────────────────
INSERT INTO settings (key, value, label, description) VALUES
    ('agent.name',        'Sara',             'Agent Name',        'Name the AI uses to introduce itself'),
    ('agent.agency_name', 'Prestige Properties Dubai', 'Agency Name', 'Your company/agency name'),
    ('agent.language',    'en',               'Default Language',  'en | ar | hi — auto-detects if mixed'),
    ('agent.intro_text',  '',                 'Intro Message',     'Leave blank to auto-generate from name/agency'),
    ('agent.system_prompt','default',         'System Prompt',     'Full AI behaviour prompt. Use "default" to reset.'),
    ('call.speech_timeout','3',               'Speech Timeout (s)','Seconds of silence before Twilio stops listening'),
    ('call.record',        'true',            'Record Calls',      'true | false')
ON CONFLICT (key) DO NOTHING;
