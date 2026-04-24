-- ─────────────────────────────────────────────────────────────
-- customer_memory  —  per-phone cross-call memory for Sara
-- ─────────────────────────────────────────────────────────────
-- One row per (user_id, phone) pair. Whenever Sara calls this phone
-- number again, the orchestrator loads the row and injects the useful
-- fields into the system prompt as "CUSTOMER CONTEXT". At the end of
-- the call a background task re-extracts the important facts from
-- the transcript and upserts them here.
--
-- Why a dedicated table rather than scanning `messages`:
--   • Keeps the per-turn read cheap (single indexed lookup).
--   • Extracted facts are normalised — the LLM doesn't have to re-read
--     and re-summarise 20 past messages on every new call.
--   • Survives across indefinitely many past calls without ballooning
--     the prompt.
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.customer_memory (
    id               uuid        NOT NULL DEFAULT uuid_generate_v4(),
    user_id          uuid        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    phone            text        NOT NULL,

    -- Extracted, normalised facts. All optional — NULL means "unknown".
    car_model        text,
    service_status   text,         -- e.g. "due", "overdue", "done recently", "scheduled"
    last_call_summary text,        -- one-paragraph human-readable recap
    preferred_callback text,       -- e.g. "evenings", "weekends", "after 6pm"
    notes            text,         -- free-form useful context for next call

    -- Classification carried forward ("was this a hot / warm / cold lead last time")
    last_lead_status text,         -- 'hot' | 'warm' | 'cold' | NULL

    call_count       integer     NOT NULL DEFAULT 0,
    first_seen_at    timestamptz NOT NULL DEFAULT NOW(),
    last_seen_at     timestamptz NOT NULL DEFAULT NOW(),
    updated_at       timestamptz NOT NULL DEFAULT NOW(),

    CONSTRAINT customer_memory_pkey PRIMARY KEY (id),
    -- One memory row per (tenant, phone). Lets us UPSERT cleanly.
    CONSTRAINT customer_memory_user_phone_uniq UNIQUE (user_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_customer_memory_user_phone
    ON public.customer_memory (user_id, phone);
