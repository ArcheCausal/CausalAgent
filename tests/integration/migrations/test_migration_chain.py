import unittest
from pathlib import Path


MIGRATION_PATH = Path(
    "Database/migrations/versions/a8b9c0d1e2f3_add_user_role.py"
)


class UserRoleMigrationTests(unittest.TestCase):
    """静态验证用户角色 migration 的链路与最小结构。"""

    def test_role_migration_is_appended_to_current_head(self):
        """新 revision 必须直接承接 analysis jobs migration。"""
        text = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "a8b9c0d1e2f3"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "e7a9b2c3d4f5"', text)

    def test_role_migration_uses_two_non_null_roles_with_user_default(self):
        """角色字段只能包含 user/admin，且历史用户默认保持 user。"""
        text = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("ENUM('user', 'admin') NOT NULL DEFAULT 'user'", text)
        self.assertNotIn("super_admin", text)

    def test_downgrade_only_drops_role_column(self):
        """回滚不得修改或删除 users 表的其他结构。"""
        text = MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('op.execute("ALTER TABLE users DROP COLUMN role")', text)
        self.assertNotIn("DROP TABLE users", text)

    def test_readiness_checks_role_column(self):
        """应用启动检查必须发现未执行角色 migration 的数据库。"""
        text = Path("app/db.py").read_text(encoding="utf-8")
        self.assertIn("column_name = 'role'", text)
        self.assertIn("数据库关键字段缺失: users.role", text)


class DatabaseMonitorSnapshotMigrationTests(unittest.TestCase):
    """静态验证共享监控快照 migration 及启动就绪检查。"""

    def test_snapshot_migration_extends_role_head_and_seeds_all_layers(self):
        """快照 revision 必须承接当前 head，并预建四个分层快照键。"""
        path = Path(
            "Database/migrations/versions/b1c2d3e4f5a6_add_database_monitor_snapshots.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "b1c2d3e4f5a6"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"', text)
        self.assertIn("CREATE TABLE database_monitor_snapshots", text)
        for snapshot_key in ("realtime", "sql_performance", "capacity", "integrity"):
            self.assertIn(f"'{snapshot_key}'", text)

    def test_snapshot_migration_supports_payload_observation_and_refresh_request(self):
        """共享表同时保存负载、采集时间和待处理手动刷新时间。"""
        path = Path(
            "Database/migrations/versions/b1c2d3e4f5a6_add_database_monitor_snapshots.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn("payload_json JSON", text)
        self.assertIn("observed_at DATETIME(6)", text)
        self.assertIn("refresh_requested_at DATETIME(6)", text)
        self.assertIn("DROP TABLE IF EXISTS database_monitor_snapshots", text)

    def test_readiness_requires_snapshot_table(self):
        """应用和 monitor 启动前都能发现未执行快照 migration 的数据库。"""
        text = Path("app/db.py").read_text(encoding="utf-8")
        self.assertIn('"database_monitor_snapshots"', text)


class DatabaseMonitorSettingsMigrationTests(unittest.TestCase):
    """静态验证在线配置与管理员审计 migration。"""

    def test_settings_migration_extends_snapshot_head_and_seeds_singleton(self):
        """新 revision 必须承接快照 head 并创建全空覆盖单例。"""
        path = Path(
            "Database/migrations/versions/c2d3e4f5a6b7_add_monitor_settings_and_admin_audit.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "c2d3e4f5a6b7"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"', text)
        self.assertIn("CREATE TABLE database_monitor_settings", text)
        self.assertIn("INSERT INTO database_monitor_settings (id)", text)
        self.assertIn("VALUES (1)", text)
        self.assertIn("realtime_interval_seconds BETWEEN 5 AND 10", text)
        self.assertIn("sql_interval_seconds BETWEEN 30 AND 60", text)
        self.assertIn("table_capacity_interval_seconds BETWEEN 300 AND 900", text)
        self.assertIn("integrity_interval_seconds >= 3600", text)

    def test_audit_survives_user_deletion_and_keeps_request_id(self):
        """用户删除应保留审计快照，并支持 request ID 检索。"""
        path = Path(
            "Database/migrations/versions/c2d3e4f5a6b7_add_monitor_settings_and_admin_audit.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE admin_audit_events", text)
        self.assertIn("actor_username VARCHAR(255) NOT NULL", text)
        self.assertIn("ON DELETE SET NULL", text)
        self.assertIn("request_id VARCHAR(64) NOT NULL", text)
        self.assertIn("CHECK (result IN ('success', 'rejected', 'failed'))", text)
        self.assertIn("idx_admin_audit_request", text)

    def test_readiness_requires_both_new_tables(self):
        """Web、worker 与 monitor 启动检查必须发现未升级结构。"""
        text = Path("app/db.py").read_text(encoding="utf-8")

        self.assertIn('"database_monitor_settings"', text)
        self.assertIn('"admin_audit_events"', text)


class AdminReadIndexMigrationTests(unittest.TestCase):
    """静态验证 3.1 只读后台索引 migration 的链路与最小回滚范围。"""

    def test_read_index_migration_extends_current_head(self):
        """3.1 revision 必须直接承接监控配置与审计表 migration。"""
        path = Path(
            "Database/migrations/versions/d3e4f5a6b7c8_add_admin_read_indexes.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "d3e4f5a6b7c8"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"', text)

    def test_read_index_migration_contains_all_bounded_query_indexes(self):
        """列表页需要的五组筛选/排序索引必须一次性存在。"""
        text = Path(
            "Database/migrations/versions/d3e4f5a6b7c8_add_admin_read_indexes.py"
        ).read_text(encoding="utf-8")

        for index_name in (
            "idx_users_admin_role_active",
            "idx_sessions_admin_activity",
            "idx_analysis_jobs_admin_created",
            "idx_uploaded_files_admin_uploaded",
            "idx_admin_audit_target_created",
        ):
            self.assertIn(index_name, text)

    def test_downgrade_only_drops_new_indexes(self):
        """回滚只能移除本 revision 新增的索引，不得删除业务表或字段。"""
        text = Path(
            "Database/migrations/versions/d3e4f5a6b7c8_add_admin_read_indexes.py"
        ).read_text(encoding="utf-8")

        self.assertIn("DROP INDEX idx_users_admin_role_active", text)
        self.assertIn("DROP INDEX idx_admin_audit_target_created", text)
        self.assertNotIn("DROP TABLE", text)
        self.assertNotIn("DROP COLUMN", text)


class AgentJobIdempotencyMigrationTests(unittest.TestCase):
    """静态验证分析任务请求幂等 migration 和启动检查。"""

    def test_idempotency_migration_extends_postgres_checkpoint_head(self):
        """请求幂等 revision 必须承接当前合并后的 migration head。"""
        path = Path(
            "Database/migrations/versions/f9a0b1c2d3e4_add_agent_job_idempotency.py"
        )
        text = path.read_text(encoding="utf-8")

        self.assertIn('revision: str = "f9a0b1c2d3e4"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "f8b9c0d1e2f3"', text)
        self.assertIn("ADD COLUMN idempotency_key VARCHAR(128)", text)
        self.assertIn("ADD COLUMN request_fingerprint CHAR(64)", text)
        self.assertIn("uq_analysis_jobs_user_idempotency", text)

    def test_readiness_and_deep_audit_require_idempotency_schema(self):
        """应用启动和 deep audit 都必须识别请求幂等结构缺失。"""
        db_text = Path("app/db.py").read_text(encoding="utf-8")
        audit_text = Path("Database/deep_audit.py").read_text(encoding="utf-8")

        self.assertIn("table_name = 'analysis_jobs'", db_text)
        self.assertIn("'idempotency_key', 'request_fingerprint', 'lease_epoch'", db_text)
        self.assertIn("uq_analysis_jobs_user_idempotency", db_text)
        self.assertIn('"idempotency_key"', audit_text)
        self.assertIn('"request_fingerprint"', audit_text)
        self.assertIn('"uq_analysis_jobs_user_idempotency"', audit_text)


class FileLibraryAndJobRecoveryMigrationTests(unittest.TestCase):
    """静态验证测试库文件库替换、Job 输入账本和回滚边界。"""

    PATH = Path(
        "Database/migrations/versions/a1b2c3d4e5f6_add_file_library_and_job_recovery.py"
    )

    def test_migration_extends_current_head_and_replaces_legacy_files_directly(self):
        """新 revision 必须直接删除旧文件表，不读取或回填旧数据。"""
        text = self.PATH.read_text(encoding="utf-8")

        self.assertIn('revision: str = "a1b2c3d4e5f6"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"', text)
        self.assertIn('op.execute("DROP TABLE IF EXISTS uploaded_files")', text)
        self.assertIn("CREATE TABLE file_objects", text)
        self.assertIn("CREATE TABLE user_files", text)
        self.assertNotIn("INSERT INTO file_objects", text)
        self.assertNotIn("INSERT INTO user_files", text)
        self.assertNotIn("INSERT INTO uploaded_files", text)
        self.assertNotIn("拒绝迁移", text)

    def test_migration_adds_frozen_inputs_and_recovery_schema(self):
        """升级必须覆盖可恢复状态、冻结文件快照和用户输入账本。"""
        text = self.PATH.read_text(encoding="utf-8")

        for fragment in (
            "waiting_input",
            "lease_epoch",
            "recovery_count",
            "resume_count",
            "input_user_file_id",
            "input_object_id",
            "current_question_id",
            "analysis_job_inputs",
            "uq_analysis_job_inputs_sequence",
            "uq_analysis_job_inputs_idempotency",
            "event_key",
            "source_event_id",
        ):
            self.assertIn(fragment, text)

    def test_downgrade_restores_empty_legacy_table_without_old_partition_layout(self):
        """回滚只恢复空旧文件表，并保持当前 head 的非分区聊天表结构。"""
        text = self.PATH.read_text(encoding="utf-8")
        downgrade = text.split("def downgrade()", 1)[1]

        self.assertIn("CREATE TABLE uploaded_files", downgrade)
        self.assertNotIn("INSERT INTO uploaded_files", downgrade)
        self.assertNotIn("PARTITION BY RANGE", downgrade)
        self.assertNotIn("REMOVE PARTITIONING", text)


class JobExecutionReleaseMigrationTests(unittest.TestCase):
    """验证即时逻辑取消所需的执行占用字段和安全回滚边界。"""

    PATH = Path(
        "Database/migrations/versions/b2c3d4e5f6a7_add_job_execution_release_state.py"
    )

    def test_migration_extends_current_job_head(self):
        text = self.PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "b2c3d4e5f6a7"', text)
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"', text)
        for fragment in (
            "execution_state",
            "execution_released_at",
            "execution_release_reason",
            "idx_analysis_jobs_execution_state_heartbeat",
            "worker_confirmed",
            "lease_expired",
        ):
            self.assertIn(fragment, text)

    def test_downgrade_only_removes_new_execution_structure(self):
        text = self.PATH.read_text(encoding="utf-8")
        downgrade = text.split("def downgrade()", 1)[1]
        self.assertIn("DROP INDEX idx_analysis_jobs_execution_state_heartbeat", downgrade)
        self.assertIn("DROP COLUMN execution_state", downgrade)
        self.assertNotIn("DROP TABLE", downgrade)


class DevelopAndRagMergeMigrationTests(unittest.TestCase):
    """验证 develop 权威链与重新编号后的 RAG 链最终只在新合流点汇合。"""

    def test_develop_authority_migrations_keep_original_revisions(self):
        """develop 的文件库、执行释放、request ID 和 web search revision 不改写。"""
        expected = {
            "Database/migrations/versions/a1b2c3d4e5f6_add_file_library_and_job_recovery.py": (
                'revision: str = "a1b2c3d4e5f6"',
                'down_revision: Union[str, Sequence[str], None] = "f9a0b1c2d3e4"',
            ),
            "Database/migrations/versions/b2c3d4e5f6a7_add_job_execution_release_state.py": (
                'revision: str = "b2c3d4e5f6a7"',
                'down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"',
            ),
            "Database/migrations/versions/c3d4e5f6a7b8_add_analysis_job_request_id.py": (
                'revision: str = "c3d4e5f6a7b8"',
                'down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"',
            ),
            "Database/migrations/versions/a0b1c2d3e4f5_merge_web_search_and_request_id_heads.py": (
                'revision: str = "a0b1c2d3e4f5"',
                '"2d3e4f5a6b7c"',
                '"c3d4e5f6a7b8"',
            ),
        }
        for relative_path, fragments in expected.items():
            text = Path(relative_path).read_text(encoding="utf-8")
            for fragment in fragments:
                self.assertIn(fragment, text, relative_path)

    def test_rag_chain_is_renumbered_and_ends_at_new_merge_head(self):
        """RAG DDL 使用唯一 revision，并经新无 DDL merge 汇入 develop head。"""
        rag_files = {
            "Database/migrations/versions/r1a2b3c4d5e6f_add_rag_eval_profiles.py": (
                'revision: str = "r1a2b3c4d5e6f"',
                'down_revision: Union[str, Sequence[str], None] = "e7a9b2c3d4f5"',
            ),
            "Database/migrations/versions/r2b3c4d5e6f7_add_rag_eval_jobs.py": (
                'revision: str = "r2b3c4d5e6f7"',
                'down_revision: Union[str, Sequence[str], None] = "r1a2b3c4d5e6f"',
            ),
            "Database/migrations/versions/r3c4d5e6f7a8_merge_rag_eval_and_postgres_heads.py": (
                'revision: str = "r3c4d5e6f7a8"',
                '"r2b3c4d5e6f7"',
                '"f8b9c0d1e2f3"',
            ),
            "Database/migrations/versions/g7c8d9e0f1a2_add_rag_eval_job_kind.py": (
                'down_revision: Union[str, Sequence[str], None] = "r3c4d5e6f7a8"',
            ),
            "Database/migrations/versions/s4d5e6f7a8b9_merge_develop_and_rag_eval.py": (
                'revision: str = "s4d5e6f7a8b9"',
                '"a0b1c2d3e4f5"',
                '"i9e0f1a2b3c4"',
                "不执行额外 schema 变更",
            ),
        }
        for relative_path, fragments in rag_files.items():
            text = Path(relative_path).read_text(encoding="utf-8")
            for fragment in fragments:
                self.assertIn(fragment, text, relative_path)

    def test_equivalent_feature_migrations_are_not_in_revision_directory(self):
        """等价 feature 文件和旧的依赖其上的合流点不能残留。"""
        for filename in (
            "j0a1b2c3d4e5_add_file_library_and_job_recovery.py",
            "k0b2c3d4e5f6_add_job_execution_release_state.py",
            "j9e0f1a2b3c4_merge_agent_jobs_and_rag_eval.py",
        ):
            self.assertFalse(
                (Path("Database/migrations/versions") / filename).exists(),
                filename,
            )


class UserMemoryCleanupOutboxMigrationTests(unittest.TestCase):
    """静态验证用户长期记忆清理 outbox migration 与启动就绪检查。"""

    MIGRATION_PATH = Path(
        "Database/migrations/versions/"
        "t5e6f7a8b9c0_add_user_memory_cleanup_outbox.py"
    )

    def test_migration_extends_current_head(self):
        """新 revision 直接承接当前唯一 head。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "t5e6f7a8b9c0"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "s4d5e6f7a8b9"',
            text,
        )

    def test_migration_creates_lease_and_aggregate_columns(self):
        """表结构包含状态、重试、租约、脱敏错误结论和完成时间。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE user_memory_cleanup_outbox", text)
        for column in (
            "user_id INT NOT NULL",
            "operation_id CHAR(36) DEFAULT NULL",
            "status VARCHAR(16) NOT NULL DEFAULT 'pending'",
            "attempts TINYINT UNSIGNED NOT NULL DEFAULT 0",
            "available_at DATETIME(6)",
            "lease_expires_at DATETIME(6) DEFAULT NULL",
            "completed_at DATETIME(6) DEFAULT NULL",
        ):
            self.assertIn(column, text)
        self.assertIn("UNIQUE KEY uq_user_memory_cleanup_outbox_user (user_id)", text)
        self.assertIn(
            "INDEX idx_user_memory_cleanup_outbox_claim",
            text,
        )

    def test_migration_keeps_no_user_foreign_key(self):
        """用户删除后任务必须保留，因此 user_id 不关联 users 外键。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertNotIn("REFERENCES users", text)
        self.assertIn(
            "FOREIGN KEY (operation_id) REFERENCES admin_operations(operation_id)",
            text,
        )

    def test_downgrade_only_drops_the_new_outbox(self):
        """回滚只删除本次新增账本，不触碰其他结构。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        downgrade = text.split("def downgrade()")[1]
        self.assertIn("DROP TABLE IF EXISTS user_memory_cleanup_outbox", downgrade)
        self.assertNotIn("DROP TABLE", downgrade.replace(
            "DROP TABLE IF EXISTS user_memory_cleanup_outbox", ""
        ))

    def test_readiness_requires_outbox_table_and_claim_index(self):
        """应用启动检查必须同时覆盖新表和新领取索引。"""
        text = Path("app/db.py").read_text(encoding="utf-8")
        self.assertIn('"user_memory_cleanup_outbox"', text)
        self.assertIn("idx_user_memory_cleanup_outbox_claim", text)


class MonitorSnapshotKeyWidthMigrationTests(unittest.TestCase):
    """静态验证快照键长度扩展 migration 与清理快照命名。"""

    MIGRATION_PATH = Path(
        "Database/migrations/versions/u7a8b9c0d1e2_widen_monitor_snapshot_key.py"
    )

    def test_migration_extends_memory_cleanup_head(self):
        """新 revision 直接承接记忆清理 outbox revision。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "u7a8b9c0d1e2"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "t5e6f7a8b9c0"',
            text,
        )

    def test_migration_widens_snapshot_key_without_truncating(self):
        """upgrade 放宽到 64 字符，downgrade 只恢复上限而不改写快照内容。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("MODIFY snapshot_key VARCHAR(64) NOT NULL", text)
        self.assertIn("MODIFY snapshot_key VARCHAR(32) NOT NULL", text)
        self.assertNotIn("UPDATE database_monitor_snapshots", text)
        self.assertNotIn("DELETE FROM", text)

    def test_cleanup_snapshot_keys_fit_the_widened_column(self):
        """清理快照键必须落在放宽后的上限内，且至少一个键依赖这次放宽。"""
        from Database.monitoring import (
            CLEANUP_OUTBOX_SNAPSHOT_KEY,
            CLEANUP_RUNTIME_SNAPSHOT_KEY,
        )

        lengths = {
            snapshot_key: len(snapshot_key)
            for snapshot_key in (
                CLEANUP_RUNTIME_SNAPSHOT_KEY,
                CLEANUP_OUTBOX_SNAPSHOT_KEY,
            )
        }
        for snapshot_key, length in lengths.items():
            self.assertLessEqual(length, 64, snapshot_key)
        self.assertTrue(
            any(length > 32 for length in lengths.values()),
            lengths,
        )


class ReportDocumentMigrationTests(unittest.TestCase):
    """静态验证结构化报告附件枚举 migration 与就绪检查。"""

    MIGRATION_PATH = Path(
        "Database/migrations/versions/"
        "v8b9c0d1e2f3_add_report_document_to_attachment_type.py"
    )

    def test_migration_extends_current_head(self):
        """新 revision 直接承接当前唯一 head。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "v8b9c0d1e2f3"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "u7a8b9c0d1e2"',
            text,
        )

    def test_upgrade_only_adds_report_document_enum_value(self):
        """升级只放宽 attachment_type 枚举，不删除历史附件数据。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        upgrade = text.split("def downgrade()", 1)[0]
        self.assertIn("'web_search_references', 'report_document'", upgrade)
        self.assertIn(
            "'causal_graph', 'analysis_result', 'file_content', 'other', 'visualization'",
            upgrade,
        )
        self.assertNotIn("DELETE", upgrade)
        self.assertNotIn("DROP", upgrade)

    def test_downgrade_deletes_only_report_document_rows(self):
        """回滚只能移除本次新增枚举值对应的附件。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        downgrade = text.split("def downgrade()", 1)[1]
        self.assertIn("DELETE FROM chat_attachments", downgrade)
        self.assertIn("WHERE attachment_type = 'report_document'", downgrade)
        self.assertNotIn("DROP TABLE", downgrade)
        self.assertNotIn("DROP COLUMN", downgrade)

    def test_readiness_requires_report_document_enum(self):
        """应用启动检查必须能发现未执行报告附件 migration 的数据库。"""
        text = Path("Database/inspection.py").read_text(encoding="utf-8")
        self.assertIn("report_document", text)


class RbacTablesMigrationTests(unittest.TestCase):
    """静态验证 RBAC 关系表、初始授权关系与启动就绪检查。"""

    MIGRATION_PATH = Path(
        "Database/migrations/versions/w9c0d1e2f3a4_add_rbac_tables.py"
    )

    FIRST_PHASE_PERMISSIONS = (
        "dashboard.access",
        "rag_eval.access",
        "rag_eval.read",
        "rag_eval.run",
        "rag_eval.publish",
        "rag_eval.rollback",
        "rag_eval.governance",
        "admin.access",
        "admin.users.read",
        "admin.users.write",
        "admin.database.read",
        "admin.database.write",
        "admin.sensitive.read",
    )

    def test_migration_extends_the_current_head(self):
        """新 revision 直接承接报告附件枚举 revision。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "w9c0d1e2f3a4"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "v8b9c0d1e2f3"',
            text,
        )

    def test_migration_creates_the_four_relation_tables(self):
        """角色、权限与两张关系表必须一次建立，并用联合主键去重。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        for table in ("roles", "permissions", "user_roles", "role_permissions"):
            self.assertIn(f"CREATE TABLE {table} (", text)
        self.assertIn("PRIMARY KEY (user_id, role_id)", text)
        self.assertIn("PRIMARY KEY (role_id, permission_id)", text)
        self.assertIn("UNIQUE KEY uq_roles_role_key (role_key)", text)
        self.assertIn("UNIQUE KEY uq_permissions_permission_key (permission_key)", text)
        self.assertIn("FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE", text)

    def test_migration_seeds_two_roles_and_first_phase_permissions(self):
        """第一阶段只初始化 user 与 admin，并登记全部权限键。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('("user", "普通用户"', text)
        self.assertIn('("admin", "管理员"', text)
        for permission_key in self.FIRST_PHASE_PERMISSIONS:
            with self.subTest(permission_key=permission_key):
                self.assertIn(f'("{permission_key}"', text)

    def test_normal_user_keeps_only_dashboard_and_admin_keeps_everything(self):
        """普通角色只拥有普通应用权限，管理员拥有第一阶段全部权限。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('"user": ("dashboard.access",)', text)
        self.assertIn(
            '"admin": tuple(permission_key for permission_key, _, _ in PERMISSION_SEEDS)',
            text,
        )

    def test_migration_backfills_relations_from_the_compatibility_column(self):
        """回填只按现有 users.role 建立关系，不修改兼容字段本身。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("JOIN roles ON roles.role_key = users.role", text)
        self.assertIn("INSERT INTO user_roles (user_id, role_id)", text)
        self.assertNotIn("UPDATE users", text)

    def test_downgrade_only_drops_the_new_tables(self):
        """回滚只删除本次新增的四张表，保留 users.role 与业务数据。"""
        downgrade = self.MIGRATION_PATH.read_text(encoding="utf-8").split("def downgrade()")[1]
        for table in ("role_permissions", "user_roles", "permissions", "roles"):
            self.assertIn(f"DROP TABLE IF EXISTS {table}", downgrade)
        self.assertNotIn("ALTER TABLE users", downgrade)
        self.assertNotIn("DROP COLUMN", downgrade)

    def test_readiness_requires_relations_and_seeded_roles(self):
        """应用启动检查必须同时覆盖新表与已初始化的两个角色。"""
        text = Path("app/db.py").read_text(encoding="utf-8")
        for fragment in (
            '"roles"',
            '"permissions"',
            '"user_roles"',
            '"role_permissions"',
            'FROM roles',
            "数据库角色缺失",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)


class AnalysisContextsMigrationTests(unittest.TestCase):
    """静态验证分析上下文 migration、绑定字段和启动就绪检查。"""

    MIGRATION_PATH = Path(
        "Database/migrations/versions/c9d0e1f2a3b4_add_analysis_contexts.py"
    )

    def test_migration_extends_the_current_head(self):
        """新 revision 直接承接 RBAC 关系表 revision。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "c9d0e1f2a3b4"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "w9c0d1e2f3a4"',
            text,
        )

    def test_migration_creates_context_table_with_snapshot_and_summary_columns(self):
        """上下文表必须同时保存文件快照、分析参数、结构化摘要和报告引用。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE analysis_contexts (", text)
        for column in (
            "analysis_context_id CHAR(36) NOT NULL PRIMARY KEY",
            "session_id VARCHAR(36) NOT NULL",
            "user_id INT NOT NULL",
            "status ENUM('active', 'archived') NOT NULL DEFAULT 'active'",
            "input_user_file_id BIGINT DEFAULT NULL",
            "file_object_id BIGINT DEFAULT NULL",
            "file_hash CHAR(64) DEFAULT NULL",
            "filename VARCHAR(255) DEFAULT NULL",
            "target VARCHAR(255) DEFAULT NULL",
            "treatment VARCHAR(255) DEFAULT NULL",
            "analysis_question MEDIUMTEXT DEFAULT NULL",
            "latest_algorithm_summary JSON DEFAULT NULL",
            "latest_rag_evidence JSON DEFAULT NULL",
            "latest_web_evidence JSON DEFAULT NULL",
            "latest_report_message_id BIGINT DEFAULT NULL",
            "latest_report_id VARCHAR(64) DEFAULT NULL",
        ):
            with self.subTest(column=column):
                self.assertIn(column, text)
        self.assertIn("INDEX idx_analysis_contexts_session_updated", text)
        self.assertIn("INDEX idx_analysis_contexts_user_session_status", text)
        self.assertIn("FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE", text)

    def test_migration_binds_session_job_and_input_without_rewriting_data(self):
        """Session 指针、Job 绑定和输入账本字段都可空，且不更新历史数据。"""
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        upgrade = text.split("def downgrade()", 1)[0]

        self.assertIn("ALTER TABLE sessions", upgrade)
        self.assertIn("ADD COLUMN active_analysis_context_id CHAR(36) DEFAULT NULL", upgrade)
        self.assertIn("ALTER TABLE analysis_jobs", upgrade)
        self.assertIn("ALTER TABLE analysis_job_inputs", upgrade)
        self.assertIn("fk_analysis_jobs_analysis_context", upgrade)
        self.assertIn("fk_analysis_job_inputs_analysis_context", upgrade)
        self.assertNotIn("UPDATE sessions", upgrade)
        self.assertNotIn("UPDATE analysis_jobs", upgrade)

    def test_downgrade_only_drops_new_context_structure(self):
        """回滚只删除本次新增的结构，不删除会话、Job、输入或报告数据。"""
        downgrade = self.MIGRATION_PATH.read_text(encoding="utf-8").split(
            "def downgrade()"
        )[1]

        self.assertIn("DROP COLUMN analysis_context_id", downgrade)
        self.assertIn("DROP COLUMN active_analysis_context_id", downgrade)
        self.assertIn("DROP TABLE IF EXISTS analysis_contexts", downgrade)
        self.assertNotIn("DELETE FROM", downgrade)

    def test_readiness_requires_context_table_columns_and_indexes(self):
        """应用启动检查必须能发现未执行上下文 migration 的数据库。"""
        text = Path("app/db.py").read_text(encoding="utf-8")
        for fragment in (
            '"analysis_contexts"',
            '"active_analysis_context_id"',
            "idx_analysis_contexts_session_updated",
            "idx_analysis_contexts_user_session_status",
            "idx_analysis_jobs_analysis_context",
            "idx_analysis_job_inputs_analysis_context",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)


class RegistrationRoleBackfillMigrationTests(unittest.TestCase):
    """静态验证注册缺陷遗留角色关系的无损回填。"""

    MIGRATION_PATH = Path(
        "Database/migrations/versions/d0e1f2a3b4c5_backfill_missing_user_roles.py"
    )

    def test_migration_extends_analysis_context_head(self):
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn('revision: str = "d0e1f2a3b4c5"', text)
        self.assertIn(
            'down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"',
            text,
        )

    def test_upgrade_backfills_user_and_legacy_admin_relations_without_overwrite(self):
        text = self.MIGRATION_PATH.read_text(encoding="utf-8")
        upgrade = text.split("def downgrade()", 1)[0]
        self.assertEqual(upgrade.count("INSERT INTO user_roles (user_id, role_id)"), 2)
        self.assertIn("roles.role_key = 'user'", upgrade)
        self.assertIn("roles.role_key = 'admin'", upgrade)
        self.assertIn("users.role = 'admin'", upgrade)
        self.assertEqual(upgrade.count("user_roles.user_id IS NULL"), 2)
        self.assertNotIn("UPDATE users", upgrade)
        self.assertNotIn("DELETE FROM", upgrade)

    def test_downgrade_does_not_delete_indistinguishable_role_relations(self):
        downgrade = self.MIGRATION_PATH.read_text(encoding="utf-8").split(
            "def downgrade()",
            1,
        )[1]
        self.assertNotIn("DELETE FROM", downgrade)
        self.assertNotIn("DROP TABLE", downgrade)


if __name__ == "__main__":
    unittest.main()
