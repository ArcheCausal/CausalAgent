import { meaningForRepositoryQuery } from './sqlRepositoryEvidence'

export type SqlSemanticConfidence = 'confirmed' | 'inferred'

export interface SqlBusinessMeaning {
  module: string
  action: string
  description: string
  confidence: SqlSemanticConfidence
  evidence: string
}

export interface SqlDigestView {
  raw: Record<string, unknown>
  digestText: string
  countStar: unknown
  totalSeconds: unknown
  averageSeconds: unknown
  rowsExamined: unknown
  rowsSent: unknown
  meaning: SqlBusinessMeaning
}

interface TableRule {
  module: string
  subject: string
  evidence: string
  actions?: Partial<Record<SqlOperation, string>>
}

type SqlOperation =
  | 'SELECT'
  | 'INSERT'
  | 'UPDATE'
  | 'DELETE'
  | 'REPLACE'
  | 'COMMIT'
  | 'ROLLBACK'
  | 'START'
  | 'SET'
  | 'SHOW'
  | 'CALL'
  | 'ALTER'
  | 'CREATE'
  | 'DROP'
  | 'TRUNCATE'
  | 'UNKNOWN'

const OPERATION_LABELS: Record<SqlOperation, string> = {
  SELECT: '查询',
  INSERT: '新增',
  UPDATE: '更新',
  DELETE: '删除',
  REPLACE: '写入',
  COMMIT: '提交',
  ROLLBACK: '回滚',
  START: '开始',
  SET: '设置',
  SHOW: '查看',
  CALL: '调用',
  ALTER: '修改',
  CREATE: '创建',
  DROP: '删除',
  TRUNCATE: '清空',
  UNKNOWN: '处理',
}

const TABLE_RULES: Record<string, TableRule> = {
  users: {
    module: '用户与权限',
    subject: '用户账户',
    evidence: 'app/auth/service.py · find_user() / find_user_by_id() / register_user()',
    actions: {
      SELECT: '读取用户身份或权限',
      INSERT: '创建用户账户',
      UPDATE: '更新用户账户或权限',
      DELETE: '删除用户账户',
    },
  },
  sessions: {
    module: '聊天会话',
    subject: '会话记录',
    evidence: 'app/chat/routes.py、app/chat/services.py、app/agent/job_service.py',
    actions: {
      SELECT: '读取聊天会话',
      INSERT: '创建聊天会话',
      UPDATE: '更新会话状态或标题',
      DELETE: '删除聊天会话',
    },
  },
  chat_messages: {
    module: '聊天消息',
    subject: '聊天消息',
    evidence: 'app/chat/routes.py、app/chat/services.py',
    actions: {
      SELECT: '读取聊天记录',
      INSERT: '保存聊天消息',
      UPDATE: '更新聊天消息',
      DELETE: '删除聊天记录',
    },
  },
  user_files: {
    module: '文件管理',
    subject: '用户上传文件',
    evidence: 'app/files/routes.py、Database/agent_connect.py、app/admin/business_service.py',
    actions: {
      SELECT: '读取用户上传文件',
      INSERT: '保存用户上传文件',
      UPDATE: '更新文件访问信息',
      DELETE: '删除用户上传文件',
    },
  },
  file_objects: {
    module: '文件管理',
    subject: '不可变文件对象',
    evidence: 'app/files/routes.py、Database/agent_connect.py、app/admin/business_service.py',
    actions: {
      SELECT: '读取不可变文件对象',
      INSERT: '保存不可变文件对象',
      UPDATE: '不允许更新文件对象',
      DELETE: '删除无引用文件对象',
    },
  },
  chat_attachments: {
    module: '消息附件',
    subject: '聊天消息附件',
    evidence: 'app/files/routes.py、app/chat/routes.py',
    actions: {
      SELECT: '读取消息附件',
      INSERT: '保存消息附件',
      UPDATE: '更新消息附件',
      DELETE: '删除消息附件',
    },
  },
  checkpoint_cleanup_outbox: {
    module: 'Agent 持久化生命周期',
    subject: 'PostgreSQL 父子图 checkpoint 后台清理任务',
    evidence: 'app/agent/persistence_cleanup.py、Database/agent_persistence_cleanup_worker.py',
    actions: {
      SELECT: '读取 checkpoint 清理任务',
      INSERT: '登记 checkpoint 清理任务',
      UPDATE: '更新 checkpoint 清理状态或租约',
      DELETE: '移除 checkpoint 清理任务',
    },
  },
  user_memory_cleanup_outbox: {
    module: 'Agent 持久化生命周期',
    subject: '用户长期记忆 Store 后台清理任务',
    evidence: 'app/agent/persistence_cleanup.py、Database/agent_persistence_cleanup_worker.py',
    actions: {
      SELECT: '读取长期记忆清理任务',
      INSERT: '登记长期记忆清理任务',
      UPDATE: '更新长期记忆清理状态或租约',
      DELETE: '移除长期记忆清理任务',
    },
  },
  database_monitor_snapshots: {
    module: '数据库监控',
    subject: '共享监控快照',
    evidence: 'Database/monitoring.py · _read_snapshot_records() / collect_snapshot()',
    actions: {
      SELECT: '读取管理员看板监控快照',
      INSERT: '创建共享监控快照',
      UPDATE: '更新共享监控快照',
      DELETE: '清理共享监控快照',
    },
  },
  database_monitor_settings: {
    module: '监控配置',
    subject: '数据库监控配置',
    evidence: 'Database/monitor_settings.py · _read_settings_row() / save_monitor_settings()',
    actions: {
      SELECT: '读取数据库监控配置',
      INSERT: '创建数据库监控配置',
      UPDATE: '保存数据库监控配置',
      DELETE: '重置数据库监控配置',
    },
  },
  admin_audit_events: {
    module: '管理员审计',
    subject: '管理员操作审计',
    evidence: 'app/admin/audit_service.py · record_admin_audit_event() / list_admin_audit_events()',
    actions: {
      SELECT: '读取管理员操作记录',
      INSERT: '记录管理员操作结果',
    },
  },
  events_statements_summary_by_digest: {
    module: '数据库监控',
    subject: 'Performance Schema SQL 摘要',
    evidence: 'Database/inspection.py · inspect_slow_queries()',
    actions: {
      SELECT: '读取数据库 SQL 活动摘要',
    },
  },
}

/** 将未知 Digest 值转换为稳定字符串，并兼容后端缺失或异常类型。 */
export function digestText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : String(value)
}

/** 统一 Digest 的大小写、空白和反引号，供关键词规则稳定匹配。 */
export function normalizeSqlDigest(value: unknown): string {
  return digestText(value)
    .replace(/`/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toUpperCase()
}

/** 从规范化 SQL 首词识别数据库操作类型，未知语句安全回退。 */
export function sqlOperation(normalizedSql: string): SqlOperation {
  const operation = normalizedSql.match(
    /^(SELECT|INSERT|UPDATE|DELETE|REPLACE|COMMIT|ROLLBACK|START|SET|SHOW|CALL|ALTER|CREATE|DROP|TRUNCATE)\b/,
  )?.[1]
  return (operation || 'UNKNOWN') as SqlOperation
}

/** 从常见 SQL 结构中提取第一个表名，去除库名前缀并转为小写。 */
export function firstSqlTable(normalizedSql: string): string | null {
  const match = normalizedSql.match(
    /\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+([A-Z0-9_$]+)(?:\s*\.\s*([A-Z0-9_$]+))?/,
  )
  return (match?.[2] || match?.[1] || '').toLowerCase() || null
}

/** 生成已知业务表的操作语义；未配置具体动作时仍保留模块和表用途。 */
function meaningForKnownTable(table: string, operation: SqlOperation, rule: TableRule): SqlBusinessMeaning {
  const action = rule.actions?.[operation] || `${OPERATION_LABELS[operation]}${rule.subject}`
  return {
    module: rule.module,
    action,
    description: `该摘要表示应用正在${action}，匹配数据表 ${table}。`,
    confidence: 'confirmed',
    evidence: rule.evidence,
  }
}

/** 构造带仓库代码证据的确认语义，统一“代码确认”结果结构。 */
function confirmedMeaning(
  module: string,
  action: string,
  description: string,
  evidence: string,
): SqlBusinessMeaning {
  return { module, action, description, confidence: 'confirmed', evidence }
}

/** 优先识别任务调度、心跳和事件流等仅靠表名无法区分的工作流语义。 */
function meaningForAgentJobs(normalizedSql: string, operation: SqlOperation): SqlBusinessMeaning | null {
  if (normalizedSql.includes('ANALYSIS_JOBS')) {
    if (operation === 'SELECT' && normalizedSql.includes('FOR UPDATE SKIP LOCKED')) {
      return {
        module: '任务调度',
        action: 'Worker 领取待执行或失联任务',
        description: 'Worker 从主库锁定一条可执行任务，避免多个 Worker 重复领取。',
        confidence: 'confirmed',
        evidence: 'app/agent/job_service.py · claim_next_job()',
      }
    }
    if (operation === 'INSERT') {
      return {
        module: '分析任务',
        action: '创建分析任务',
        description: '把用户提交的分析请求写入任务队列，等待 Worker 领取。',
        confidence: 'confirmed',
        evidence: 'app/agent/job_service.py · create_job()',
      }
    }
    if (operation === 'UPDATE' && normalizedSql.includes('HEARTBEAT_AT')) {
      return {
        module: '任务调度',
        action: '更新 Worker 心跳或任务租约',
        description: 'Worker 汇报任务仍在运行，防止任务被误判为失联。',
        confidence: 'confirmed',
        evidence: 'app/agent/job_service.py · update_heartbeat() / claim_next_job()',
      }
    }
    if (operation === 'UPDATE' && normalizedSql.includes('STATUS')) {
      return {
        module: '分析任务',
        action: '更新分析任务状态',
        description: '更新任务的排队、运行、完成、失败或取消状态。',
        confidence: 'confirmed',
        evidence: 'app/agent/job_service.py · complete_job() / fail_job() / claim_next_job()',
      }
    }
    if (operation === 'SELECT') {
      return {
        module: '分析任务',
        action: '查询分析任务状态',
        description: '读取任务的状态、执行进度或当前 Worker 信息。',
        confidence: 'confirmed',
        evidence: 'app/agent/job_service.py · get_active_job() / get_job_for_user() / get_job_by_id()',
      }
    }
    return {
      module: '分析任务',
      action: `${OPERATION_LABELS[operation]}分析任务记录`,
      description: '维护分析任务队列及其运行状态。',
      confidence: 'confirmed',
      evidence: 'app/agent/job_service.py · analysis_jobs 数据访问函数',
    }
  }

  if (normalizedSql.includes('ANALYSIS_JOB_EVENTS')) {
    const isRead = operation === 'SELECT'
    return {
      module: '任务事件流',
      action: isRead ? '读取任务进度事件' : '记录任务进度事件',
      description: isRead
        ? '读取分析任务的事件日志，用于 SSE 进度和断线续传。'
        : '保存分析任务产生的进度、结果或错误事件。',
      confidence: 'confirmed',
      evidence: isRead
        ? 'app/agent/job_service.py · read_events_after()'
        : 'app/agent/job_service.py · write_event()',
    }
  }
  return null
}

/** 识别事务控制和连接初始化语句，避免把数据库基础动作误标为业务查询。 */
function meaningForDatabaseControl(normalizedSql: string): SqlBusinessMeaning | null {
  if (normalizedSql.startsWith('COMMIT')) {
    return confirmedMeaning(
      '数据库事务',
      '提交事务',
      '确认本次事务中的数据库修改并结束事务；该 Digest 会聚合仓库内多个 commit 调用点。',
      '仓库多处 conn.commit()；Performance Schema Digest 无法把 COMMIT 反推到唯一业务函数',
    )
  }
  if (normalizedSql.startsWith('ROLLBACK')) {
    return confirmedMeaning(
      '数据库事务',
      '回滚事务',
      '撤销本次事务中尚未提交的数据库修改；该 Digest 会聚合仓库内多个 rollback 调用点。',
      '仓库多处 conn.rollback()；Performance Schema Digest 无法把 ROLLBACK 反推到唯一业务函数',
    )
  }
  if (normalizedSql.startsWith('START TRANSACTION')) {
    return confirmedMeaning(
      '数据库事务',
      '开始显式事务',
      '应用为任务创建、任务领取或会话删除等原子操作开启事务。',
      'app/agent/job_service.py · create_job() / claim_next_job()；app/chat/routes.py · start_transaction() 调用点',
    )
  }
  if (normalizedSql.startsWith('SET NAMES')) {
    return confirmedMeaning(
      '数据库连接',
      '设置连接字符集',
      'mysql-connector 在建立或重置连接时把仓库配置的 utf8mb4 字符集应用到当前会话。',
      'app/db.py · _base_connection_config(charset="utf8mb4")；mysql.connector.MySQLConnection._post_connection()',
    )
  }
  if (normalizedSql.includes('AUTOCOMMIT')) {
    return confirmedMeaning(
      '数据库连接池',
      '恢复连接的自动提交模式',
      'mysql-connector 在连接初始化或归还连接池后恢复当前会话的 autocommit 状态。',
      'app/db.py · MySQLConnectionPool(pool_reset_session=True)；mysql.connector.MySQLConnection.autocommit',
    )
  }
  if (normalizedSql.includes('SQL_MODE')) {
    return confirmedMeaning(
      '数据库连接池',
      '恢复连接的 SQL 模式',
      'mysql-connector 在连接初始化或重置后恢复当前会话的 sql_mode。',
      'app/db.py · MySQLConnectionPool(pool_reset_session=True)；mysql.connector.MySQLConnection.sql_mode',
    )
  }
  if (normalizedSql.startsWith('SET ')) {
    return {
      module: '数据库连接',
      action: '推断：设置连接会话参数',
      description: '该语句在调整当前连接参数，但没有命中仓库或 mysql-connector 的已登记签名。',
      confidence: 'inferred',
      evidence: '仅匹配到 SET 语句类型，未找到唯一代码调用点',
    }
  }
  return null
}

/** 按工作流、已知表和基础语句优先级，把 Digest 映射为可读业务语义。 */
export function mapSqlBusinessMeaning(value: unknown): SqlBusinessMeaning {
  const normalizedSql = normalizeSqlDigest(value)
  if (!normalizedSql) {
    return {
      module: '未知模块',
      action: '无法识别 SQL',
      description: '监控快照没有返回可识别的 Digest 文本，请查看原始详情。',
      confidence: 'inferred',
      evidence: '监控快照未提供 digest_text / digest',
    }
  }

  const operation = sqlOperation(normalizedSql)
  const repositoryMeaning = meaningForRepositoryQuery(normalizedSql)
  if (repositoryMeaning) return repositoryMeaning

  const jobMeaning = meaningForAgentJobs(normalizedSql, operation)
  if (jobMeaning) return jobMeaning

  const controlMeaning = meaningForDatabaseControl(normalizedSql)
  if (controlMeaning) return controlMeaning

  const table = firstSqlTable(normalizedSql)
  const tableRule = table ? TABLE_RULES[table] : undefined
  if (table && tableRule) return meaningForKnownTable(table, operation, tableRule)

  if (table) {
    return {
      module: '其他数据库表',
      action: `推断：${OPERATION_LABELS[operation]} ${table} 数据`,
      description: `仅根据 ${operation} 操作和表名 ${table} 推断，具体业务用途请查看原始 SQL。`,
      confidence: 'inferred',
      evidence: `未命中仓库已登记 SQL 签名，仅提取到 ${operation} 操作和 ${table} 表名`,
    }
  }

  return {
    module: '数据库基础操作',
    action: operation === 'UNKNOWN' ? '其他数据库操作' : `推断：${OPERATION_LABELS[operation]}数据库信息`,
    description: '没有匹配到已知业务表或工作流，具体用途请查看原始 SQL。',
    confidence: 'inferred',
    evidence: '未命中仓库已登记 SQL 签名，且未提取到已知业务表',
  }
}

/** 将后端新旧字段别名收敛为详情组件使用的统一只读视图。 */
export function toSqlDigestView(raw: Record<string, unknown>): SqlDigestView {
  const rawDigest = raw.digest_text ?? raw.digest
  return {
    raw,
    digestText: digestText(rawDigest),
    countStar: raw.count_star ?? raw.execution_count,
    totalSeconds: raw.total_seconds,
    averageSeconds: raw.avg_seconds,
    rowsExamined: raw.rows_examined,
    rowsSent: raw.rows_sent,
    meaning: mapSqlBusinessMeaning(rawDigest),
  }
}
