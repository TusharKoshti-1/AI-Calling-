-- ============================================================
-- CallSara — UAE Real Estate AI Calling Bot
-- Supabase PostgreSQL Schema
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New Query)
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── CALLS TABLE ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calls (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sid             TEXT UNIQUE NOT NULL,          -- Twilio Call SID
    phone           TEXT NOT NULL,                 -- destination number
    from_number     TEXT,                          -- caller number
    status          TEXT DEFAULT 'ringing',        -- ringing|answered|completed|no-answer|busy|failed
    hot_lead        BOOLEAN DEFAULT FALSE,
    duration_sec    INTEGER DEFAULT 0,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    recording_url   TEXT,                          -- Supabase Storage public URL
    recording_path  TEXT,                          -- Supabase Storage path
    transcript      TEXT,                          -- full conversation text
    agent_name      TEXT DEFAULT 'Sara',
    agency_name     TEXT DEFAULT 'Prestige Properties Dubai',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast dashboard queries
CREATE INDEX IF NOT EXISTS idx_calls_status      ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_hot_lead    ON calls(hot_lead);
CREATE INDEX IF NOT EXISTS idx_calls_started_at  ON calls(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_phone       ON calls(phone);

-- ── TRANSCRIPT MESSAGES TABLE ─────────────────────────────────
-- Stores each message individually for real-time UI updates
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

-- ── AUTO-UPDATE updated_at ────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_updated_at ON calls;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── DASHBOARD STATS VIEW ──────────────────────────────────────
CREATE OR REPLACE VIEW call_stats AS
SELECT
    COUNT(*)                                          AS total_calls,
    COUNT(*) FILTER (WHERE hot_lead = TRUE)           AS hot_leads,
    COUNT(*) FILTER (WHERE status = 'answered' OR status = 'completed') AS answered,
    COUNT(*) FILTER (WHERE status IN ('no-answer','busy','failed'))     AS no_answer,
    COUNT(*) FILTER (WHERE status = 'ringing')        AS ringing,
    ROUND(AVG(duration_sec) FILTER (WHERE duration_sec > 0)) AS avg_duration_sec,
    COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours') AS calls_today,
    COUNT(*) FILTER (WHERE hot_lead AND started_at >= NOW() - INTERVAL '24 hours') AS hot_leads_today
FROM calls;

-- ── ROW LEVEL SECURITY (optional, enable if using auth) ───────
-- ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

-- ── REALTIME (enable for live transcript updates) ─────────────
-- Run in Supabase Dashboard → Database → Replication → Enable for: messages, calls
-- Or uncomment:
-- ALTER PUBLICATION supabase_realtime ADD TABLE calls;
-- ALTER PUBLICATION supabase_realtime ADD TABLE messages;
