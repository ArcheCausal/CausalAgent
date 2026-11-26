"""Add persisted processing datasets to analysis jobs.

Revision ID: x1y2z3a4b5c6
Revises: e1f2a3b4c5d6
Create Date: 2026-10-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Store immutable candidate artifacts and a transactionally checked Job pointer."""
    op.execute(
        """
        CREATE TABLE analysis_job_datasets (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            job_id VARCHAR(36) NOT NULL,
            revision INT NOT NULL,
            idempotency_key CHAR(64) NOT NULL,
            source_hash CHAR(64) NOT NULL,
            data_object_id BIGINT NOT NULL,
            script_object_id BIGINT NOT NULL,
            data_hash CHAR(64) NOT NULL,
            script_hash CHAR(64) NOT NULL,
            status ENUM('pending_validation', 'validated', 'rejected')
                NOT NULL DEFAULT 'pending_validation',
            metadata JSON NOT NULL,
            validation_report JSON DEFAULT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            validated_at DATETIME(6) DEFAULT NULL,
            UNIQUE KEY uq_analysis_job_datasets_job_revision (job_id, revision),
            UNIQUE KEY uq_analysis_job_datasets_job_idempotency (job_id, idempotency_key),
            UNIQUE KEY uq_analysis_job_datasets_job_id (job_id, id),
            INDEX idx_analysis_job_datasets_objects (data_object_id, script_object_id),
            INDEX idx_analysis_job_datasets_job_status (job_id, status, revision),
            CONSTRAINT fk_analysis_job_datasets_job
                FOREIGN KEY (job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE,
            CONSTRAINT fk_analysis_job_datasets_data_object
                FOREIGN KEY (data_object_id) REFERENCES file_objects(id) ON DELETE RESTRICT,
            CONSTRAINT fk_analysis_job_datasets_script_object
                FOREIGN KEY (script_object_id) REFERENCES file_objects(id) ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )
    # This pointer deliberately has no reverse foreign key. Jobs cascade from
    # sessions, so a second cascading edge back to their own dataset would form
    # a cycle. The binding service locks both rows and checks job_id/status in
    # one MySQL transaction before updating this value.
    op.execute(
        """
        ALTER TABLE analysis_jobs
            ADD COLUMN prepared_dataset_id BIGINT DEFAULT NULL AFTER input_filename,
            ADD INDEX idx_analysis_jobs_prepared_dataset (prepared_dataset_id)
        """
    )
    op.execute(
        """
        CREATE TABLE analysis_sandbox_sessions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            request_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
            request_digest CHAR(64) COLLATE utf8mb4_bin NOT NULL,
            source_hash CHAR(64) COLLATE utf8mb4_bin NOT NULL,
            job_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
            worker_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
            attempt_count INT NOT NULL,
            lease_epoch BIGINT NOT NULL,
            remote_sandbox_id VARCHAR(255) COLLATE utf8mb4_bin DEFAULT NULL,
            status ENUM('creating', 'active', 'deleting', 'uncertain', 'closed')
                NOT NULL DEFAULT 'creating',
            metadata JSON NOT NULL,
            operation_id VARCHAR(128) COLLATE utf8mb4_bin DEFAULT NULL,
            operation_type VARCHAR(32) DEFAULT NULL,
            operation_digest CHAR(64) COLLATE utf8mb4_bin DEFAULT NULL,
            operation_status ENUM('pending', 'succeeded', 'uncertain') DEFAULT NULL,
            operation_result JSON DEFAULT NULL,
            heartbeat_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            expires_at DATETIME(6) NOT NULL,
            max_expires_at DATETIME(6) NOT NULL,
            next_retry_at DATETIME(6) DEFAULT NULL,
            version BIGINT NOT NULL DEFAULT 1,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            closed_at DATETIME(6) DEFAULT NULL,
            open_job_id VARCHAR(36) COLLATE utf8mb4_bin
                GENERATED ALWAYS AS (CASE WHEN status <> 'closed' THEN job_id END) STORED,
            UNIQUE KEY uq_analysis_sandbox_sessions_request (request_id),
            UNIQUE KEY uq_analysis_sandbox_sessions_remote (remote_sandbox_id),
            UNIQUE KEY uq_analysis_sandbox_sessions_open_job (open_job_id),
            INDEX idx_analysis_sandbox_sessions_reconcile (status, next_retry_at, id),
            INDEX idx_analysis_sandbox_sessions_expiry (status, expires_at),
            INDEX idx_analysis_sandbox_sessions_job (job_id),
            CONSTRAINT ck_analysis_sandbox_sessions_deadline
                CHECK (expires_at <= max_expires_at),
            CONSTRAINT ck_analysis_sandbox_sessions_identity
                CHECK (attempt_count > 0 AND lease_epoch > 0 AND version > 0)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    """Refuse to drop persisted artifacts or lifecycle records, including closed rows."""
    connection = op.get_bind()
    artifact_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM analysis_job_datasets"
    ).scalar_one()
    if artifact_count:
        raise RuntimeError(
            "analysis_job_datasets contains persisted artifacts; archive them before downgrade"
        )
    session_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM analysis_sandbox_sessions"
    ).scalar_one()
    if session_count:
        raise RuntimeError(
            "analysis_sandbox_sessions is not empty; archive lifecycle records before downgrade"
        )
    op.execute(
        """
        ALTER TABLE analysis_jobs
            DROP INDEX idx_analysis_jobs_prepared_dataset,
            DROP COLUMN prepared_dataset_id
        """
    )
    op.execute("DROP TABLE analysis_sandbox_sessions")
    op.execute("DROP TABLE analysis_job_datasets")
