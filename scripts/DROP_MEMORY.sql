-- ════════════════════════════════════════════════════════════════
-- DROP_MEMORY.sql
--
-- Surgical follow-up. Use this if you ran RESET_AND_REBUILD earlier
-- and just want to remove the customer_memory feature WITHOUT wiping
-- your user account, calls, messages, or settings.
--
-- After running this:
--   • The customer_memory table no longer exists.
--   • Everything else is untouched.
--   • Deploy the new code (which has all memory logic removed).
--
-- Safe to run multiple times — uses IF EXISTS guards.
-- ════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS public.customer_memory CASCADE;

-- Sanity check: confirm we still have the tables we want.
-- (Uncomment to run inline.)
-- SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
--   → expected: calls, messages, sessions, settings, users
