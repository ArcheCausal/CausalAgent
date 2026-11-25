"""Copy a parent checkpoint ancestry into the new Job's own PostgreSQL thread."""

from Agent.deep_agent_tools.identity import build_deep_agent_run_id


# Delta channels need their parent chain and writes. At the branch point the
# only pending write we keep is the original question, because the fold task
# will execute again after receiving the new answer.
ANCESTRY = """
WITH RECURSIVE chain AS (
    SELECT * FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id = %s
    UNION ALL
    SELECT p.* FROM checkpoints p JOIN chain c
      ON p.thread_id = c.thread_id AND p.checkpoint_ns = c.checkpoint_ns
      AND p.checkpoint_id = c.parent_checkpoint_id
)
"""


async def copy_interrupt_checkpoint(saver, *, source: str, anchor: str, target: str) -> None:
    """Materialize a fork once. The destination may already have advanced after a crash."""
    identities = {"job_id": target, "deep_agent_run_id": build_deep_agent_run_id(job_id=target)}
    async with saver.conn.connection() as connection, connection.transaction():
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (target,))
            await cursor.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = %s AND checkpoint_ns = '' LIMIT 1",
                (target,),
            )
            if await cursor.fetchone():
                return
            await cursor.execute(
                ANCESTRY + """
                INSERT INTO checkpoints
                    (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata)
                SELECT %s, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
                    checkpoint, metadata || jsonb_build_object('job_id', %s::text)
                FROM chain
                """,
                (source, anchor, target, target),
            )
            if not cursor.rowcount:
                raise RuntimeError("历史追问 checkpoint 已不可用")
            await cursor.execute(
                ANCESTRY + """
                INSERT INTO checkpoint_blobs (thread_id, checkpoint_ns, channel, version, type, blob)
                SELECT DISTINCT %s, b.checkpoint_ns, b.channel, b.version, b.type, b.blob
                FROM chain c CROSS JOIN LATERAL jsonb_each_text(c.checkpoint->'channel_versions') AS v
                JOIN checkpoint_blobs b ON b.thread_id = c.thread_id AND b.checkpoint_ns = c.checkpoint_ns
                    AND b.channel = v.key AND b.version = v.value
                """,
                (source, anchor, target),
            )
            for channel, value in identities.items():
                value_type, blob = saver.serde.dumps_typed(value)
                await cursor.execute(
                    "UPDATE checkpoint_blobs SET type = %s, blob = %s "
                    "WHERE thread_id = %s AND checkpoint_ns = '' AND channel = %s AND type <> 'empty'",
                    (value_type, blob, target, channel),
                )
                await cursor.execute(
                    """
                    UPDATE checkpoints SET checkpoint =
                        jsonb_set(checkpoint, ARRAY['channel_values', %s], to_jsonb(%s::text), false)
                    WHERE thread_id = %s AND checkpoint_ns = ''
                      AND jsonb_typeof(checkpoint->'channel_values'->%s) = 'string'
                    """,
                    (channel, value, target, channel),
                )
            await cursor.execute(
                ANCESTRY + """
                INSERT INTO checkpoint_writes
                    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob, task_path)
                SELECT %s, w.checkpoint_ns, w.checkpoint_id, w.task_id, w.idx, w.channel, w.type, w.blob, w.task_path
                FROM chain c JOIN checkpoint_writes w USING (thread_id, checkpoint_ns, checkpoint_id)
                WHERE c.checkpoint_id <> %s OR w.channel = '__interrupt__'
                """,
                (source, anchor, target, anchor),
            )
