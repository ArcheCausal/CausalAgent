"""Version chat messages, input boundaries and preprocessing outputs."""

from alembic import op


revision: str = "e2f3a4b5c6d7"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN conversation_version BIGINT UNSIGNED NOT NULL DEFAULT 0")
    op.execute("""
        ALTER TABLE chat_messages
            ADD COLUMN replaced_by_job_id CHAR(36) DEFAULT NULL,
            ADD COLUMN checkpoint_id CHAR(36) DEFAULT NULL,
            ADD COLUMN checkpoint_namespace VARCHAR(128) DEFAULT NULL,
            ADD COLUMN checkpoint_question_id VARCHAR(255) DEFAULT NULL,
            ADD INDEX idx_chat_messages_current (session_id, user_id, replaced_by_job_id, id)
    """)
    op.execute("""
        ALTER TABLE analysis_job_inputs
            ADD COLUMN context_before JSON DEFAULT NULL,
            ADD COLUMN file_at_submit JSON DEFAULT NULL,
            ADD COLUMN replaces_message_id BIGINT DEFAULT NULL,
            ADD COLUMN conversation_version BIGINT UNSIGNED DEFAULT NULL,
            ADD COLUMN fork_from JSON DEFAULT NULL,
            ADD COLUMN replaced_context JSON DEFAULT NULL
    """)
    op.execute("""
        ALTER TABLE analysis_contexts ADD COLUMN latest_preprocess_version_id BIGINT DEFAULT NULL
    """)
    op.execute("""
        CREATE TABLE analysis_preprocess_versions (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            job_id CHAR(36) NOT NULL,
            input_id BIGINT NOT NULL,
            call_id VARCHAR(255) NOT NULL,
            source_file_hash CHAR(64) NOT NULL,
            parent_version_id BIGINT DEFAULT NULL,
            recipe JSON NOT NULL,
            output JSON NOT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_analysis_preprocess_call (job_id, input_id, call_id),
            CONSTRAINT fk_preprocess_job
                FOREIGN KEY (job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def downgrade() -> None:
    # Undo schema only when no branch or new preprocessing output would be lost.
    pointer = op.get_bind().exec_driver_sql(
        "SELECT EXISTS (SELECT 1 FROM chat_messages WHERE replaced_by_job_id IS NOT NULL)"
        " OR EXISTS (SELECT 1 FROM analysis_preprocess_versions)"
    ).scalar()
    if pointer:
        raise RuntimeError("该数据库已有会话分支或预处理版本，禁止直接降级")
    op.execute("DROP TABLE analysis_preprocess_versions")
    op.execute("ALTER TABLE analysis_contexts DROP COLUMN latest_preprocess_version_id")
    op.execute("""
        ALTER TABLE analysis_job_inputs
            DROP COLUMN replaced_context, DROP COLUMN fork_from, DROP COLUMN conversation_version,
            DROP COLUMN replaces_message_id, DROP COLUMN file_at_submit, DROP COLUMN context_before
    """)
    op.execute("""
        ALTER TABLE chat_messages
            DROP INDEX idx_chat_messages_current, DROP COLUMN checkpoint_question_id,
            DROP COLUMN checkpoint_namespace, DROP COLUMN checkpoint_id, DROP COLUMN replaced_by_job_id
    """)
    op.execute("ALTER TABLE sessions DROP COLUMN conversation_version")
