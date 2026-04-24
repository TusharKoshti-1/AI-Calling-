"""
app.db.repositories.customer_memory
───────────────────────────────────
Per-phone memory for a tenant's customers. Scoped by user_id so SaaS
tenants can't read each other's customer data.

The orchestrator uses this in two places:
  • At the start of an outbound/inbound call — `get(user_id, phone)` to
    pull prior context into the system prompt.
  • At the end of a call — `upsert_facts(...)` to save the freshly
    extracted facts for next time.

All fields are optional. A NULL means "unknown" — the extractor only
overwrites a field when it has a real value for it.
"""
from __future__ import annotations

from typing import Any

from app.db.session import get_pool


class CustomerMemoryRepository:

    async def get(self, user_id: str, phone: str) -> dict[str, Any] | None:
        """Fetch the memory row for this (user, phone), or None."""
        if not phone:
            return None
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT car_model, service_status, last_call_summary,
                       preferred_callback, notes, last_lead_status,
                       call_count, first_seen_at, last_seen_at
                FROM customer_memory
                WHERE user_id = $1 AND phone = $2
                """,
                user_id, phone,
            )
            return dict(row) if row else None

    async def bump_call_count(self, user_id: str, phone: str) -> None:
        """Record that we just placed / received another call to this phone.

        Upserts a bare row if there's no prior memory yet — that way the
        `call_count` field reflects total contacts even before the first
        extraction has run.
        """
        if not phone:
            return
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO customer_memory (user_id, phone, call_count,
                                             first_seen_at, last_seen_at, updated_at)
                VALUES ($1, $2, 1, NOW(), NOW(), NOW())
                ON CONFLICT (user_id, phone) DO UPDATE SET
                    call_count   = customer_memory.call_count + 1,
                    last_seen_at = NOW(),
                    updated_at   = NOW()
                """,
                user_id, phone,
            )

    async def upsert_facts(
        self,
        user_id: str,
        phone: str,
        *,
        car_model: str | None = None,
        service_status: str | None = None,
        last_call_summary: str | None = None,
        preferred_callback: str | None = None,
        notes: str | None = None,
        last_lead_status: str | None = None,
    ) -> None:
        """Merge freshly extracted facts into the memory row.

        We explicitly COALESCE each column so that passing NULL means
        "don't touch" rather than "clobber the existing value". This is
        important because the extractor only fills fields it's confident
        about — older values should survive a silent extraction.
        """
        if not phone:
            return
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO customer_memory (
                    user_id, phone, car_model, service_status,
                    last_call_summary, preferred_callback, notes,
                    last_lead_status, call_count,
                    first_seen_at, last_seen_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 1, NOW(), NOW(), NOW())
                ON CONFLICT (user_id, phone) DO UPDATE SET
                    car_model          = COALESCE($3, customer_memory.car_model),
                    service_status     = COALESCE($4, customer_memory.service_status),
                    last_call_summary  = COALESCE($5, customer_memory.last_call_summary),
                    preferred_callback = COALESCE($6, customer_memory.preferred_callback),
                    notes              = COALESCE($7, customer_memory.notes),
                    last_lead_status   = COALESCE($8, customer_memory.last_lead_status),
                    last_seen_at       = NOW(),
                    updated_at         = NOW()
                """,
                user_id, phone,
                car_model, service_status, last_call_summary,
                preferred_callback, notes, last_lead_status,
            )
