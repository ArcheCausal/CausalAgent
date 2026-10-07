import { describe, expect, it } from 'vitest'
import {
  firstSqlTable,
  mapSqlBusinessMeaning,
  normalizeSqlDigest,
  sqlOperation,
  toSqlDigestView,
} from '../src/lib/sqlSemantics'

describe('SQL Digest 业务语义映射', () => {
  it('规范化大小写、反引号和多余空白后仍能识别操作与表名', () => {
    const normalized = normalizeSqlDigest(' select  *\nFROM `analysis_jobs`  ')
    expect(normalized).toBe('SELECT * FROM ANALYSIS_JOBS')
    expect(sqlOperation(normalized)).toBe('SELECT')
    expect(firstSqlTable(normalized)).toBe('analysis_jobs')
  })

  it('任务领取特征优先于 analysis_jobs 的普通查询规则', () => {
    const meaning = mapSqlBusinessMeaning(
      'select * from `analysis_jobs` where status = ? for update skip locked',
    )
    expect(meaning).toMatchObject({
      module: '任务调度',
      action: 'Worker 领取待执行或失联任务',
      description: 'Worker 从主库锁定一条可执行任务，避免多个 Worker 重复领取。',
      confidence: 'confirmed',
      evidence: 'app/agent/job_service.py · claim_next_job()',
    })
  })

  it.each([
    ['INSERT INTO analysis_jobs (job_id) VALUES (?)', '创建分析任务'],
    ['UPDATE analysis_jobs SET heartbeat_at = ? WHERE job_id = ?', '更新 Worker 心跳或任务租约'],
    ['UPDATE analysis_jobs SET status = ? WHERE job_id = ?', '更新分析任务状态'],
    ['SELECT status FROM analysis_jobs WHERE job_id = ?', '查询分析任务状态'],
    ['INSERT INTO analysis_job_events (job_id) VALUES (?)', '记录任务进度事件'],
    ['SELECT * FROM analysis_job_events WHERE job_id = ?', '读取任务进度事件'],
  ])('识别分析任务工作流：%s', (sql, action) => {
    expect(mapSqlBusinessMeaning(sql).action).toBe(action)
  })

  it.each([
    ['SELECT * FROM users WHERE id = ?', '用户与权限'],
    ['SELECT * FROM sessions WHERE user_id = ?', '聊天会话'],
    ['INSERT INTO chat_messages (content) VALUES (?)', '聊天消息'],
    ['SELECT * FROM user_files WHERE user_id = ?', '文件管理'],
    ['SELECT * FROM file_objects WHERE owner_user_id = ?', '文件管理'],
    ['DELETE FROM chat_attachments WHERE message_id = ?', '消息附件'],
    ['SELECT * FROM checkpoint_cleanup_outbox WHERE status = ?', 'Agent 持久化生命周期'],
    ['SELECT * FROM user_memory_cleanup_outbox WHERE status = ?', 'Agent 持久化生命周期'],
    ['SELECT * FROM database_monitor_snapshots WHERE snapshot_key = ?', '数据库监控'],
    ['UPDATE database_monitor_settings SET version = ?', '监控配置'],
    ['INSERT INTO admin_audit_events (action) VALUES (?)', '管理员审计'],
  ])('已知业务表映射到稳定模块：%s', (sql, module) => {
    const meaning = mapSqlBusinessMeaning(sql)
    expect(meaning.module).toBe(module)
    expect(meaning.confidence).toBe('confirmed')
  })

  it.each([
    'SELECT * FROM checkpoints WHERE thread_id = ?',
    'INSERT INTO checkpoint_writes (thread_id) VALUES (?)',
  ])('已迁移的 MySQL checkpoint 表不再标记为现行 Agent 状态：%s', (sql) => {
    const meaning = mapSqlBusinessMeaning(sql)
    expect(meaning.module).toBe('其他数据库表')
    expect(meaning.confidence).toBe('inferred')
  })

  it.each([
    ['COMMIT', '提交事务'],
    ['ROLLBACK', '回滚事务'],
    ['START TRANSACTION', '开始显式事务'],
    ['SET NAMES ? COLLATE ?', '设置连接字符集'],
    ['SET `autocommit` = ?', '恢复连接的自动提交模式'],
  ])('数据库基础语句也映射为可读语义：%s', (sql, action) => {
    const meaning = mapSqlBusinessMeaning(sql)
    expect(meaning.action).toBe(action)
    expect(meaning.confidence).toBe('confirmed')
    expect(meaning.evidence).not.toBe('')
  })

  it.each([
    [
      'SHOW GLOBAL VARIABLES WHERE `Variable_name` IN (...)',
      '读取连接上限与慢查询配置',
      'Database/inspection.py',
    ],
    [
      'SHOW GLOBAL STATUS WHERE `Variable_name` IN (...)',
      '读取连接与慢查询运行状态',
      'Database/inspection.py',
    ],
    [
      'SELECT snapshot_key, payload_json, UTC_TIMESTAMP (?) AS database_now FROM database_monitor_snapshots WHERE snapshot_key IN (...)',
      '读取四类共享监控快照',
      'Database/monitoring.py',
    ],
    [
      'INSERT INTO database_monitor_snapshots (snapshot_key, payload_json, observed_at) VALUES (?, ..., UTC_TIMESTAMP (?)) ON DUPLICATE KEY UPDATE payload_json = VALUES (payload_json)',
      '写入本轮监控采集结果',
      'Database/monitoring.py',
    ],
    [
      'SELECT s.id, u.username AS updated_by_username FROM database_monitor_settings AS s LEFT JOIN users AS u ON u.id = s.updated_by_user_id',
      '读取在线监控配置和修改人',
      'Database/monitor_settings.py',
    ],
    [
      'SELECT SUM (status = ?) AS queued, SUM (status = ?) AS running, SUM (status = ?) AS max_attempts_running FROM analysis_jobs',
      '汇总活动任务健康状态',
      'get_worker_snapshot_report()',
    ],
    [
      'SELECT job_id, status, worker_id, heartbeat_at FROM analysis_jobs WHERE status IN (...)',
      '读取活动任务明细',
      'get_worker_snapshot_report()',
    ],
    [
      'SELECT DIGEST_TEXT FROM performance_schema . events_statements_summary_by_digest ORDER BY SUM_TIMER_WAIT',
      '采集高负载 SQL 摘要',
      'inspect_slow_queries()',
    ],
    [
      'SELECT GET_LOCK (...) AS acquired',
      '获取监控采集命名锁',
      'collect_snapshot()',
    ],
    [
      'SELECT RELEASE_LOCK (?)',
      '释放监控采集命名锁',
      'collect_snapshot()',
    ],
    [
      'SELECT VERSION ( ) AS version, UTC_TIMESTAMP (?) AS database_time',
      '检查主库连接与版本',
      'inspect_primary()',
    ],
    [
      'SELECT id, username, role, is_active FROM users WHERE id = ?',
      '按用户 ID 复核身份、角色和启用状态',
      'find_user_by_id()',
    ],
    [
      'SELECT @@GLOBAL . server_uuid AS server_uuid',
      '确认慢查询计数来源实例',
      '_server_instance_id()',
    ],
    [
      'SET @@SESSION . autocommit = OFF',
      '恢复连接的自动提交模式',
      'pool_reset_session=True',
    ],
    [
      'SET @@SESSION . sql_mode = ?',
      '恢复连接的 SQL 模式',
      'pool_reset_session=True',
    ],
  ])('当前监控快照签名由代码证据确认：%s', (sql, action, evidence) => {
    const meaning = mapSqlBusinessMeaning(sql)
    expect(meaning.action).toBe(action)
    expect(meaning.confidence).toBe('confirmed')
    expect(meaning.evidence).toContain(evidence)
  })

  it('未知表按操作和表名弱推断，并明确标注推断', () => {
    const meaning = mapSqlBusinessMeaning('SELECT * FROM custom_table WHERE id = ?')
    expect(meaning.module).toBe('其他数据库表')
    expect(meaning.action).toBe('推断：查询 custom_table 数据')
    expect(meaning.description).toContain('具体业务用途请查看原始 SQL')
    expect(meaning.confidence).toBe('inferred')
  })

  it('空 Digest 安全回退为未识别语义', () => {
    const meaning = mapSqlBusinessMeaning(null)
    expect(meaning.action).toBe('无法识别 SQL')
    expect(meaning.confidence).toBe('inferred')
  })

  it('统一视图保留原始字段并兼容 digest 与 execution_count 别名', () => {
    const raw = {
      digest: 'SELECT * FROM users',
      execution_count: 4,
      total_seconds: 1.25,
      avg_seconds: 0.3125,
      rows_examined: 8,
      rows_sent: 4,
    }
    const view = toSqlDigestView(raw)
    expect(view.raw).toBe(raw)
    expect(view.digestText).toBe(raw.digest)
    expect(view.countStar).toBe(raw.execution_count)
    expect(view.totalSeconds).toBe(raw.total_seconds)
    expect(view.averageSeconds).toBe(raw.avg_seconds)
    expect(view.rowsExamined).toBe(raw.rows_examined)
    expect(view.rowsSent).toBe(raw.rows_sent)
  })
})
