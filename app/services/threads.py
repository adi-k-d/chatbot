from app.database import get_pool
from app.models import ThreadState
from app.observability.logging import get_logger

log = get_logger(__name__)


async def upsert_thread(
    *,
    business_id: str,
    customer_number: str,
    business_number: str,
    is_inbound: bool,
) -> ThreadState:
    """Atomically create or update a conversation thread, returning its state."""
    pool = get_pool()

    inbound_inc = 1 if is_inbound else 0
    outbound_inc = 0 if is_inbound else 1

    row = await pool.fetchrow(
        """
        INSERT INTO conversation_threads (
            id, business_id, customer_number, business_number,
            inbound_count, outbound_count, user_state,
            created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1, $2, $3,
            $4, $5, '{}',
            NOW(), NOW()
        )
        ON CONFLICT (business_id, customer_number) DO UPDATE
          SET inbound_count  = conversation_threads.inbound_count  + $4,
              outbound_count = conversation_threads.outbound_count + $5,
              updated_at     = NOW()
        RETURNING id, user_state, metadata
        """,
        business_id,
        customer_number,
        business_number,
        inbound_inc,
        outbound_inc,
    )

    return ThreadState(
        id=str(row["id"]),
        user_state=dict(row["user_state"] or {}),
        metadata=dict(row["metadata"] or {}) if row["metadata"] else {},
        inbound_count=inbound_inc,
    )


async def update_thread_stage(thread_id: str, stage: str) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE conversation_threads
        SET user_state = jsonb_set(
                COALESCE(user_state, '{}'),
                '{stage}',
                to_jsonb($1::text)
            ),
            updated_at = NOW()
        WHERE id = $2
        """,
        stage,
        thread_id,
    )
