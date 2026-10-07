import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import Composer from '../../src/components/Composer.vue'
import type { JobRecord } from '../../src/types/domain'

describe('Composer', () => {
  it('emits draft, web-search and send actions without storing DOM resources', async () => {
    const wrapper = mount(Composer, {
      props: {
        draft: '',
        webSearchEnabled: false,
        selectedFile: null,
        activeJob: null,
        sending: false,
      },
    })
    await wrapper.get('textarea').setValue('hello')
    await wrapper.get('.web-search-button').trigger('click')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter', shiftKey: false })

    expect(wrapper.emitted('update:draft')).toEqual([['hello']])
    expect(wrapper.emitted('update:web-search')).toEqual([[true]])
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('turns the send control into the running stop action', async () => {
    const wrapper = mount(Composer, {
      props: {
        draft: '',
        webSearchEnabled: false,
        selectedFile: null,
        activeJob: { uiState: 'running' } as JobRecord,
        sending: false,
      },
    })

    const stopButton = wrapper.get('.send-button.is-running')
    expect(wrapper.find('.cancel-button').exists()).toBe(false)
    expect(stopButton.attributes('aria-label')).toBe('取消任务')
    expect(stopButton.find('.send-button-stop').exists()).toBe(true)

    await stopButton.trigger('click')

    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('send')).toBeUndefined()
  })
})
