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
        CREATE TABLE analysis_sandbox_capacity (
            id TINYINT NOT NULL PRIMARY KEY,
            max_active_sandboxes INT NOT NULL,
            CONSTRAINT ck_analysis_sandbox_capacity_positive
                CHECK (max_active_sandboxes > 0)
        ) ENGINE=InnoDB
        """
    )
    op.execute(
        "INSERT INTO analysis_sandbox_capacity (id, max_active_sandboxes) VALUES (1, 4)"
    )
    op.execute(
        """
        CREATE TABLE analysis_sandbox_leases (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            job_id VARCHAR(36) NOT NULL,
            worker_id VARCHAR(128) NOT NULL,
            lease_epoch BIGINT NOT NULL,
            lease_token CHAR(36) NOT NULL,
            expires_at DATETIME(6) NOT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_analysis_sandbox_leases_job (job_id),
            UNIQUE KEY uq_analysis_sandbox_leases_token (lease_token),
            INDEX idx_analysis_sandbox_leases_expiry (expires_at),
            CONSTRAINT fk_analysis_sandbox_leases_job
                FOREIGN KEY (job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def downgrade() -> None:
    """Refuse to drop persisted artifacts or active leases."""
    connection = op.get_bind()
    artifact_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM analysis_job_datasets"
    ).scalar_one()
    if artifact_count:
        raise RuntimeError(
            "analysis_job_datasets contains persisted artifacts; archive them before downgrade"
        )
    lease_count = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM analysis_sandbox_leases"
    ).scalar_one()
    if lease_count:
        raise RuntimeError(
            "analysis_sandbox_leases is not empty; stop sandbox work before downgrade"
        )
    op.execute(
        """
        ALTER TABLE analysis_jobs
            DROP INDEX idx_analysis_jobs_prepared_dataset,
            DROP COLUMN prepared_dataset_id
        """
    )
    op.execute("DROP TABLE analysis_sandbox_leases")
    op.execute("DROP TABLE analysis_sandbox_capacity")
    op.execute("DROP TABLE analysis_job_datasets")
