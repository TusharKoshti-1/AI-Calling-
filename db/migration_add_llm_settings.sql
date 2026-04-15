-- ============================================================
-- CallSara — Migration: Add LLM Provider Settings
-- Run this in Supabase SQL Editor ONLY if you already have
-- the database set up from a previous schema.sql run.
-- If you are setting up fresh, just run schema.sql — it
-- already includes these rows.
-- ============================================================

-- Add LLM provider keys to settings table.
-- ON CONFLICT (key) DO NOTHING means safe to run multiple times.
INSERT INTO settings (key, value) VALUES
    ('llm_provider',  'groq'),
    ('groq_model',    'llama-3.3-70b-versatile'),
    ('openai_model',  'gpt-4o-mini'),
    ('openai_api_key','')
ON CONFLICT (key) DO NOTHING;

-- Verify the rows were added:
SELECT key, value, updated_at FROM settings ORDER BY key;
