import type { CaBadgeTone } from '@causalagent/design-system'

/** 把管理员页面的业务状态统一映射为共享标记色调。 */
export function statusTone(value: unknown): CaBadgeTone {
  const normalized = String(value ?? '').trim().toLowerCase()
  if (['failed', 'error', 'disabled', 'unavailable', 'canceled', 'cancelled', 'rejected', '异常'].includes(normalized)) {
    return 'strong'
  }
  if (['running', 'processing', 'succeeded', 'success', 'healthy', 'enabled', 'active', 'completed', 'confirmed', '处理中', '空闲', '正常'].includes(normalized)) {
    return 'muted'
  }
  return 'neutral'
}
