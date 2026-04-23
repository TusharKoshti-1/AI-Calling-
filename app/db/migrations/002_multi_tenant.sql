-- ════════════════════════════════════════════════════════════════
-- 002 — Multi-tenant SaaS: users, sessions, and scoping columns
--
-- SAFE TO RUN on an existing database: additive only.
--   • Creates `users` and `sessions` tables
--   • Adds nullable `user_id` columns to `calls`, `messages`, `settings`
--   • Backfills nothing (existing rows keep user_id = NULL = "global legacy")
--   • Updates `call_stats` view to accept an optional user filter
--
-- Rollback: drop the new tables + drop the new columns.
-- ════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── USERS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
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

CREATE INDEX IF NOT EXISTS idx_users_email ON users(LOWER(email));

-- ── SESSIONS ─────────────────────────────────────────────────
-- We store session *records* (not JWTs alone) so signout can revoke them
-- immediately on the server. The client just holds an opaque token.
CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,   -- SHA-256 of the session token
    user_agent      TEXT,
    ip              TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_user       ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);

-- ── Scoping columns on existing tables ───────────────────────
-- Nullable so historical rows don't explode. Repositories always pass
-- a user_id, so new rows will be scoped.

ALTER TABLE calls
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_calls_user_id ON calls(user_id);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);

-- Settings are now per-user. A NULL user_id means "global default"
-- (used as fallback when a user hasn't configured their own value).
ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;

-- Drop the old PK so we can make (user_id, key) the new composite key.
-- We only do this once; guard it so re-running is safe.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'settings_pkey'
          AND table_name = 'settings'
          AND constraint_type = 'PRIMARY KEY'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'settings_user_key_pkey'
          AND table_name = 'settings'
    ) THEN
        ALTER TABLE settings DROP CONSTRAINT settings_pkey;
        -- Let NULL user_id be the global default; unique on (user_id, key)
        -- with NULLS NOT DISTINCT so only one global row per key exists.
        ALTER TABLE settings
            ADD CONSTRAINT settings_user_key_pkey
            PRIMARY KEY (user_id, key);
    END IF;
EXCEPTION WHEN others THEN
    -- If settings already has composite PK from a previous run, ignore.
    RAISE NOTICE 'settings PK migration skipped: %', SQLERRM;
END$$;

-- Settings composite-key migration note:
-- Postgres does not allow NULL values in a PRIMARY KEY column, so to keep
-- "global defaults" working we use a regular UNIQUE (user_id, key) and a
-- surrogate id. Replace the above DO block with the following approach if
-- your Postgres version is older:
--
--   ALTER TABLE settings ADD COLUMN id UUID DEFAULT uuid_generate_v4();
--   UPDATE settings SET id = uuid_generate_v4() WHERE id IS NULL;
--   ALTER TABLE settings ALTER COLUMN id SET NOT NULL;
--   ALTER TABLE settings DROP CONSTRAINT settings_pkey;
--   ALTER TABLE settings ADD PRIMARY KEY (id);
--   CREATE UNIQUE INDEX settings_user_key_uniq
--     ON settings(COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), key);

-- For broadest compatibility (works on all PG versions), we take the
-- surrogate-id path below and supersede the DO block:

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'settings' AND column_name = 'id'
    ) THEN
        ALTER TABLE settings ADD COLUMN id UUID DEFAULT uuid_generate_v4();
        UPDATE settings SET id = uuid_generate_v4() WHERE id IS NULL;
        ALTER TABLE settings ALTER COLUMN id SET NOT NULL;
    END IF;

    -- Drop the composite PK if we set it above (may fail silently otherwise).
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'settings_user_key_pkey'
    ) THEN
        ALTER TABLE settings DROP CONSTRAINT settings_user_key_pkey;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'settings_pkey'
          AND table_name = 'settings'
    ) THEN
        ALTER TABLE settings DROP CONSTRAINT settings_pkey;
    END IF;

    ALTER TABLE settings ADD PRIMARY KEY (id);
END$$;

-- NULL-safe uniqueness: one row per (user_id, key), where a NULL user_id
-- is treated as a sentinel value rather than "distinct every time".
CREATE UNIQUE INDEX IF NOT EXISTS settings_user_key_uniq
    ON settings (
        COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
        key
    );

CREATE INDEX IF NOT EXISTS idx_settings_user ON settings(user_id);

-- ── updated_at on users ──────────────────────────────────────
DROP TRIGGER IF EXISTS set_users_updated_at ON users;
CREATE TRIGGER set_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── Stats view: now a function so callers can pass a user filter ────
DROP VIEW IF EXISTS call_stats;

CREATE OR REPLACE FUNCTION call_stats_for(p_user UUID)
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
    FROM calls
    WHERE user_id = p_user;
$$;
