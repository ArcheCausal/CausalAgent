import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StreamingDraft from '../../src/components/StreamingDraft.vue'

describe('StreamingDraft', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('advances the visible text and shows the full buffer once animation stops', async () => {
    const wrapper = mount(StreamingDraft, { props: { text: '因果分析', animate: true } })
    expect(wrapper.text()).toBe('')

    await vi.advanceTimersByTimeAsync(25)
    expect(wrapper.text()).toBe('因')

    await vi.advanceTimersByTimeAsync(75)
    expect(wrapper.text()).toBe('因果分析')
    expect(wrapper.get('article').classes()).toContain('streaming-draft')

    await wrapper.setProps({ animate: false })
    expect(wrapper.text()).toBe('因果分析')
    expect(wrapper.get('article').classes()).not.toContain('streaming-draft')
  })

  it('restarts the presentation cursor when the received buffer is replaced', async () => {
    const wrapper = mount(StreamingDraft, { props: { text: 'abc', animate: true } })
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toBe('abc')

    await wrapper.setProps({ text: 'xyz' })
    expect(wrapper.text()).toBe('')

    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toBe('xyz')
  })
})
