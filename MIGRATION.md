# Migration from the single-tenant version

This release adds **multi-tenant authentication**. Every call, message,
and setting is now scoped to a user account. Existing data remains
intact — pre-existing rows simply have `user_id = NULL` and are treated
as legacy.

---

## 0 · Rotate leaked keys first (if you haven't)

The original `config.py` committed live keys to git. Rotate at the vendor:

- **Groq** – Groq Cloud console → API Keys → revoke + create new
- **Cartesia** – Dashboard → API Keys → revoke + create new
- **Supabase service role** – Settings → API → regenerate service role JWT
- **Twilio auth token** – Console → Account → rotate

---

## 1 · Run the new migration

In your Supabase SQL editor, paste and run:

```
app/db/migrations/002_multi_tenant.sql
```

It is **idempotent and additive**:

- Creates `users` and `sessions` tables
- Adds `user_id` columns to `calls`, `messages`, `settings` (nullable)
- Changes `settings` PK to a surrogate id with a unique index on
  `(COALESCE(user_id, sentinel), key)` so NULL user_id can represent
  "global default"
- Replaces the `call_stats` view with a `call_stats_for(user_id)` function

Existing rows are not modified. You can run it multiple times safely.

---

## 2 · Update your `.env`

Add these new variables:

```
SESSION_SECRET=<long-random-string>          # required in production
SESSION_TTL_HOURS=720
SESSION_COOKIE_SECURE=true                   # if you're behind HTTPS
ALLOW_PUBLIC_SIGNUP=true                     # or false after admin is bootstrapped
VERIFY_TWILIO_SIGNATURE=true                 # recommended in production
```

Generate `SESSION_SECRET` with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Remove the old `ADMIN_API_KEY` var — it's no longer used.

---

## 3 · Deploy and create your first account

1. Deploy the new build.
2. Visit `https://your-domain/` — you'll be redirected to `/signin`.
3. Click **Create one** to sign up.
4. The first signup is auto-promoted to admin.
5. (Optional) Set `ALLOW_PUBLIC_SIGNUP=false` and redeploy to close the
   door behind you.

Your legacy call data is still in the DB, but it belongs to no user
(`user_id IS NULL`), so no tenant will see it. If you want to claim it:

```sql
UPDATE calls    SET user_id = '<your-user-id>' WHERE user_id IS NULL;
UPDATE messages SET user_id = '<your-user-id>' WHERE user_id IS NULL;
```

Look up your user-id with:

```sql
SELECT id, email FROM users ORDER BY created_at ASC LIMIT 1;
```

---

## 4 · Per-user settings

Previously there was one global set of settings. Now each user has their
own. When a user signs in for the first time, their settings are
seeded from your `.env` defaults (agent_name, agency_name, voice_id, etc.),
and they can customise from the Settings page.

If you had non-default settings previously (system prompt, voice, agent
name), they still exist as `user_id = NULL` global defaults — every new
user will inherit them unless the row is overridden.

---

## 5 · Twilio webhook URLs

No change — the webhook paths are the same:

```
POST {BASE_URL}/webhooks/twilio/greeting
POST {BASE_URL}/webhooks/twilio/process-speech
POST {BASE_URL}/webhooks/twilio/call-status
POST {BASE_URL}/webhooks/twilio/recording-status
```

The legacy aliases (`/twiml-greeting`, `/process-speech`, `/call-status`,
`/recording-status`, `/opening-audio`, `/reply-audio`) are also kept.

When a user initiates a call via `/api/call`, Twilio receives the new
URLs automatically. Calls-in-flight during a deploy keep working via
the legacy aliases.

---

## 6 · Rollback plan

The DB changes are additive — dropping to the old code still works
(the legacy code just ignores the new columns). Keep the session
tables; they harm nothing.

To fully reverse:

```sql
ALTER TABLE calls    DROP COLUMN user_id;
ALTER TABLE messages DROP COLUMN user_id;
ALTER TABLE settings DROP COLUMN user_id;
ALTER TABLE settings DROP COLUMN id;
ALTER TABLE settings ADD PRIMARY KEY (key);
DROP TABLE sessions;
DROP TABLE users;
DROP FUNCTION IF EXISTS call_stats_for(UUID);
```

But in practice, keep them around — they cost nothing and let you redeploy
the new version instantly.
