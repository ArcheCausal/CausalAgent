"""add analysis contexts for session scoped multi analysis routing

Revision ID: c9d0e1f2a3b4
Revises: w9c0d1e2f3a4
Create Date: 2026-09-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "w9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建立跨 Job 的分析上下文事实表，并把 Session、Job 和输入绑定到上下文。"""
    op.execute(
        """
        CREATE TABLE analysis_contexts (
            analysis_context_id CHAR(36) NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            user_id INT NOT NULL,
            status ENUM('active', 'archived') NOT NULL DEFAULT 'active',
            input_user_file_id BIGINT DEFAULT NULL,
            file_object_id BIGINT DEFAULT NULL,
            file_hash CHAR(64) DEFAULT NULL,
            filename VARCHAR(255) DEFAULT NULL,
            target VARCHAR(255) DEFAULT NULL,
            treatment VARCHAR(255) DEFAULT NULL,
            analysis_question MEDIUMTEXT DEFAULT NULL,
            latest_algorithm_summary JSON DEFAULT NULL,
            latest_rag_evidence JSON DEFAULT NULL,
            latest_web_evidence JSON DEFAULT NULL,
            latest_report_message_id BIGINT DEFAULT NULL,
            latest_report_id VARCHAR(64) DEFAULT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ON UPDATE CURRENT_TIMESTAMP(6),
            INDEX idx_analysis_contexts_session_updated
                (session_id, updated_at DESC, analysis_context_id),
            INDEX idx_analysis_contexts_user_session_status
                (user_id, session_id, status),
            INDEX idx_analysis_contexts_input_user_file (input_user_file_id),
            CONSTRAINT fk_analysis_contexts_session
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_contexts_user
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_contexts_input_user_file
                FOREIGN KEY (input_user_file_id) REFERENCES user_files(id)
                ON DELETE SET NULL,
            CONSTRAINT fk_analysis_contexts_file_object
                FOREIGN KEY (file_object_id) REFERENCES file_objects(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )

    # sessions.active_analysis_context_id 只保存当前默认上下文的指针。
    # 这里不加外键：analysis_contexts 已对 sessions 建立 ON DELETE CASCADE，
    # 反向再建外键会形成级联环，删除 Session 时行为依赖存储引擎处理顺序。
    # 指针写入始终在同一事务内按 user_id + session_id 校验目标上下文归属。
    op.execute(
        """
        ALTER TABLE sessions
            ADD COLUMN active_analysis_context_id CHAR(36) DEFAULT NULL
                AFTER archived_at
        """
    )

    op.execute(
        """
        ALTER TABLE analysis_jobs
            ADD COLUMN analysis_context_id CHAR(36) DEFAULT NULL AFTER session_id,
            ADD INDEX idx_analysis_jobs_analysis_context (analysis_context_id),
            ADD CONSTRAINT fk_analysis_jobs_analysis_context
                FOREIGN KEY (analysis_context_id)
                REFERENCES analysis_contexts(analysis_context_id) ON DELETE SET NULL
        """
    )

    op.execute(
        """
        ALTER TABLE analysis_job_inputs
            ADD COLUMN analysis_context_id CHAR(36) DEFAULT NULL,
            ADD INDEX idx_analysis_job_inputs_analysis_context (analysis_context_id),
            ADD CONSTRAINT fk_analysis_job_inputs_analysis_context
                FOREIGN KEY (analysis_context_id)
                REFERENCES analysis_contexts(analysis_context_id) ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    """只移除本次新增的上下文结构，不删除任何会话、Job、输入或报告数据。"""
    op.execute(
        """
        ALTER TABLE analysis_job_inputs
            DROP FOREIGN KEY fk_analysis_job_inputs_analysis_context,
            DROP INDEX idx_analysis_job_inputs_analysis_context,
            DROP COLUMN analysis_context_id
        """
    )
    op.execute(
        """
        ALTER TABLE analysis_jobs
            DROP FOREIGN KEY fk_analysis_jobs_analysis_context,
            DROP INDEX idx_analysis_jobs_analysis_context,
            DROP COLUMN analysis_context_id
        """
    )
    op.execute(
        """
        ALTER TABLE sessions
            DROP COLUMN active_analysis_context_id
        """
    )
    op.execute("DROP TABLE IF EXISTS analysis_contexts")
