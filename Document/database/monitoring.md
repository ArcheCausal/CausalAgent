# 数据库监控

文档职责：记录 monitor worker、数据库共享快照、在线采集配置、quick/deep audit 和 cleanup 运行状态的当前实现。

适用范围：修改 `Database/monitor_worker.py`、`Database/monitoring.py`、`Database/monitor_settings.py`、数据库看板 API 或 cleanup 心跳时使用；管理员页面只描述消费契约，见 [`../admin/api.md`](../admin/api.md)。

## 采集模型

monitor 入口是 `python -m Database.monitor_worker`。它把采集结果写入 MySQL `database_monitor_snapshots`，以便多个 Web 进程共享同一份最近事实。采集由 MySQL 命名锁保护，避免多个 monitor 或手动请求同时执行同一快照。

核心快照分为：

| 快照组 | 典型内容 | 默认周期 |
| --- | --- | --- |
| `realtime` | 主库/从库状态、连接和 Job/worker 运行摘要 | 10 秒 |
| `sql_performance` | Performance Schema digest、`Slow_queries` 窗口增量 | 60 秒 |
| `capacity` | revision、表容量等可带估算性质的容量信息 | 900 秒 |
| `integrity` | 运行期 quick integrity | 默认不定时；启用后 86400 秒 |
| `deep_audit` | schema-aware 手动深审计 | 仅手动 |
| `agent_persistence_cleanup_outbox` | checkpoint 与用户记忆两类脱敏 outbox 汇总和有限明细 | 按 realtime 采集 |
| `agent_persistence_cleanup_runtime` | 清理 worker 心跳、当前任务类型和本次启动统计 | 按 worker 心跳周期（默认 10 秒） |

数据库看板的 GET 只读最近快照，不在 Web 请求中现场运行完整采集。`POST /api/admin/db/refresh` 和 `POST /api/admin/db/integrity/run` 只登记 `refresh_requested_at`，实际工作由 monitor 进程完成。

`collect_snapshot()` 继续返回原有 `bool`，不会把日志合同暴露给调用者。采集器异常仍可生成并持久化 degraded/unknown 快照，但每个快照键只在转移边界记录 `monitor.snapshot.failed`；真正恢复后记录一次 `monitor.snapshot.recovered`。`GET_LOCK` 返回未获得只表示其他 monitor 已持锁，是正常竞争并保持静默；只有获取或释放锁的数据库操作异常才进入 `monitor.lock.failed/recovered`。

## 在线配置

七个配置字段保存在 `database_monitor_settings` 单例行中，解析优先级固定为：

```text
数据库覆盖 > 环境变量 > 代码默认值
```

数据库字段为 `NULL` 时表示继承。每个进程最多缓存 5 秒；数据库读取失败时先使用最后有效值，再回退到环境变量/代码默认值并标记降级。保存使用版本锁，成功、拒绝和失败都写入 `admin_audit_events`。

在线配置读取失败和恢复使用 `monitor.config.degraded/recovered` 的转移日志，不再随每个 5 秒缓存周期重复 warning；容量配置不足映射为稳定的 `pool_capacity_below_snapshot_count` 原因码。首次失败、原因变化和每 5 分钟提醒会记录，恢复只记录一次，所有事件都不包含配置正文、数据库地址或异常文本。

| 字段 | 默认值 | 有效范围 |
| --- | --- | --- |
| `auto_refresh_enabled` | `true` | 布尔 |
| `realtime_interval_seconds` | `10` | 5-10 |
| `sql_interval_seconds` | `60` | 30-60 |
| `table_capacity_interval_seconds` | `900` | 300-900 |
| `slow_query_warning_delta` | `1` | 大于等于 1 |
| `integrity_enabled` | `false` | 布尔 |
| `integrity_interval_seconds` | `86400` | 大于等于 3600 |

路由、SQL 和 Vue 前端不能硬编码这些调度策略。连接使用率 warning/error 默认阈值为 70%/85%，快速 SELECT 超时默认由 `DB_INSPECTION_QUERY_TIMEOUT_MS=3000` 控制。

## SQL 性能语义

SQL digest 区块表示“SQL 性能摘要/高负载 SQL”，不是慢查询日志。候选语句按单次平均 `AVG_TIMER_WAIT` 降序，平均耗时相同时按累计 `SUM_TIMER_WAIT` 降序；慢查询告警优先使用采集窗口内 `Slow_queries` 增量，累计值只作兼容和辅助展示。

所有 SQL 性能摘要必须展示 `observed_at`、逻辑 `source_role/source_alias`、warning 和 estimate 语义。监控账号只能读取业务库允许范围和指定 Performance Schema digest，不能因为看板而扩大为全局权限。

应用慢查询运行日志与看板摘要分离。`db.query.slow` 只记录 SQL 操作类型、`duration_ms`、规范化 SQL 的完整 SHA-256 `statement_digest` 和被抑制次数；不记录规范化 SQL、参数或结果。相同 digest 使用 5 分钟窗口和最多 1024 项的进程内 LRU 限频。

主从读取失败只使用 `primary/replica` 逻辑别名和稳定 reason code。首次回退、原因变化、5 分钟提醒以及恢复分别由 `db.replica.fallback/recovered` 表示，不记录 host、端口、库名、账号、连接 URL 或驱动异常消息。readiness 的最终失败由对应进程 startup 事件承担，请求级 5xx 只在最外层结果边界补充，不在中间 service 层重复打印同一异常。

## Integrity 与 Deep Audit

quick integrity 复用独立 PostgreSQL 只读连接，确认 checkpoint 连通性、官方表集合和 setup migration 版本，同时检查 MySQL 两张 cleanup outbox 的外键/领取索引与失败清理任务。它不再查询已经迁移走的 MySQL checkpoint 表，也不要求 `chat_messages` 必须分区。

deep audit 只接受手动请求，不定时调度，不自动修复。它覆盖 Alembic revision、关键 schema、utf8mb4/UTC/隔离级别、账号职责结论、Job/Event、cleanup outbox、归档关系、`active_session_key` 和逐从库状态；每项有超时和异常样本上限。返回值只包含逻辑别名、计数和安全结论，不返回账号、host、grants、密码或连接串。

## Cleanup 运行状态

cleanup worker 约每 10 秒写入 `agent_persistence_cleanup_runtime` 心跳，快照包含当前任务类型（`checkpoint` 或 `user_memory`）。monitor 还采集 `agent_persistence_cleanup_outbox`：checkpoint 与 user_memory 两张表各自汇总 pending、due、processing、租约过期和 failed 数量，并合并最多 100 条脱敏条目；不返回 `last_error` 原文，只返回 `has_error` 和安全错误状态。管理员后台的 database、cleanup-worker、outbox 三段视图都消费这些共享快照。

每个 outbox attempt 只在最终边界记录一次 `agent.persistence.cleanup.succeeded` 或 `agent.persistence.cleanup.failed`；数据库中的 `last_error` 仍服务于状态机，但不进入运行日志。claim、heartbeat、运行快照发布等循环级故障使用 `agent.persistence.cleanup.runtime.degraded/recovered` 转移事件。日志改造不改变 cleanup 的数据库写入、fencing、幂等、租约或 outbox 状态机。
