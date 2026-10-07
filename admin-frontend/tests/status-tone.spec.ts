import { describe, expect, it } from 'vitest'
import { statusTone } from '../src/lib/statusTone'

describe('statusTone', () => {
  it('maps ordinary healthy states to the muted shared tone', () => {
    expect(statusTone('running')).toBe('muted')
    expect(statusTone('正常')).toBe('muted')
    expect(statusTone('confirmed')).toBe('muted')
  })

  it('maps failures and disabled states to the strong tone', () => {
    expect(statusTone('failed')).toBe('strong')
    expect(statusTone('disabled')).toBe('strong')
  })

  it('keeps unknown values neutral', () => {
    expect(statusTone('future_state')).toBe('neutral')
    expect(statusTone(undefined)).toBe('neutral')
  })
})
