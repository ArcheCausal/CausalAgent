import { mount } from '@vue/test-utils'
import { beforeAll, describe, expect, it } from 'vitest'
import MessageBody from '../../src/components/MessageBody.vue'
import markedSource from '../../public/vendor/marked.min.js?raw'

beforeAll(() => {
  new Function(markedSource).call(window)
})

describe('MessageBody', () => {
  it('keeps rendering plain chat messages with the Markdown adapter', () => {
    const wrapper = mount(MessageBody, { props: { text: '# 标题\n\n- 列表项' } })

    expect(wrapper.get('.markdown-content h1').text()).toBe('标题')
    expect(wrapper.findAll('.markdown-content li').map((item) => item.text())).toEqual(['列表项'])
  })

  it('dispatches structured reports to the report renderer', () => {
    const wrapper = mount(MessageBody, {
      props: {
        text: {
          type: 'report',
          layout: 'report',
          render_mode: 'structured',
          document: {
            schema_version: 1,
            report_id: 'report_test',
            title: '因果分析报告',
            blocks: [{ id: 'm', type: 'markdown', content: '结论正文', evidence_refs: [] }],
            assets: {},
            sources: [],
            evidence_refs: [],
          },
        },
      },
    })

    expect(wrapper.get('.report-document .report-title').text()).toBe('因果分析报告')
    expect(wrapper.find('.markdown-content.causal-report').exists()).toBe(false)
  })

  it('renders an unavailable notice for a broken report document', () => {
    const wrapper = mount(MessageBody, {
      props: {
        text: {
          type: 'report',
          layout: 'report',
          render_mode: 'structured',
          document: { report_id: 5 },
        },
      },
    })

    expect(wrapper.get('.report-document-error').text()).toContain('报告内容不可用')
  })
})
