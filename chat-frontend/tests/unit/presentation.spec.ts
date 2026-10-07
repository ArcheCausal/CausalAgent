import { describe, expect, it } from 'vitest'
import {
  PRESENTATION_CHARS_PER_TICK,
  advancePresentationText,
  isPresentationActive,
  isNearBottom,
  textLength,
} from '../../src/runtime/chat/presentation'

describe('presentation helpers', () => {
  it('advances the visible text without dropping the received buffer', () => {
    expect(PRESENTATION_CHARS_PER_TICK).toBe(1)
    expect(advancePresentationText('', '你好世界', 1)).toBe('你')
    expect(advancePresentationText('你', '你好世界', 2)).toBe('你好世')
    expect(advancePresentationText('你好世', '你好世界', 4)).toBe('你好世界')
    expect(textLength('你好世界')).toBe(4)
  })

  it('treats only the near-bottom band as following the latest content', () => {
    expect(isNearBottom(900, 100, 1000, 80)).toBe(true)
    expect(isNearBottom(800, 100, 1000, 80)).toBe(false)
  })

  it('keeps presenting while the job is paused for input and stops on terminal states', () => {
    expect(isPresentationActive('active')).toBe(true)
    expect(isPresentationActive('waiting_input')).toBe(true)
    expect(isPresentationActive('completed')).toBe(false)
    expect(isPresentationActive('failed')).toBe(false)
    expect(isPresentationActive('canceled')).toBe(false)
  })
})
