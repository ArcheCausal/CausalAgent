// 普通端展示层的时间与滚动纯函数。这里不持有 DOM 或定时器，便于单元测试，
// 真正的定时器由组件或 runtime controller 持有。

export const PRESENTATION_CHARS_PER_SECOND = 40
export const PRESENTATION_TICK_MS = 25
export const PRESENTATION_CHARS_PER_TICK = Math.max(
  1,
  Math.round((PRESENTATION_CHARS_PER_SECOND * PRESENTATION_TICK_MS) / 1000),
)
export const CHAT_AUTO_SCROLL_THRESHOLD_PX = 80

/** 按 Unicode 码点计数，避免中文和多字节字符被拆成半个字。 */
export function textLength(text: string | null | undefined): number {
  return Array.from(text ?? '').length
}

/** 假流式只推进展示游标，不改变已经接收的完整缓冲区。 */
export function advancePresentationText(
  visible: string | null | undefined,
  target: string | null | undefined,
  maxCharacters: number,
): string {
  const targetCharacters = Array.from(target ?? '')
  const visibleLength = textLength(visible)
  const step = Math.max(1, Number(maxCharacters) || 1)
  return targetCharacters
    .slice(0, Math.min(targetCharacters.length, visibleLength + step))
    .join('')
}

/** 距离底部在阈值内时，认为用户仍在跟随最新内容。 */
export function isNearBottom(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  threshold: number = CHAT_AUTO_SCROLL_THRESHOLD_PX,
): boolean {
  const distance = Number(scrollHeight) - Number(scrollTop) - Number(clientHeight)
  return distance <= Math.max(0, Number(threshold) || 0)
}

/** 减少动态效果偏好下直接展示完整文本，不做逐字推进。 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false
  const query = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)')
    : null
  return Boolean(query?.matches)
}

/** 暂停等待输入时展示继续推进；只有终态才直接展示完整文本。 */
export function isPresentationActive(status: string): boolean {
  return status === 'active' || status === 'waiting_input'
}
