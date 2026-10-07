import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import CaBadge from '../src/components/CaBadge.vue'
import CaButton from '../src/components/CaButton.vue'
import CaCard from '../src/components/CaCard.vue'
import CaEmptyState from '../src/components/CaEmptyState.vue'
import CaErrorState from '../src/components/CaErrorState.vue'
import CaInput from '../src/components/CaInput.vue'
import CaLoadingState from '../src/components/CaLoadingState.vue'
import CaTabs from '../src/components/CaTabs.vue'

describe('CaButton', () => {
  it('默认渲染主操作按钮并把点击抛给使用方', async () => {
    const wrapper = mount(CaButton, { slots: { default: '运行任务' } })

    expect(wrapper.element.tagName).toBe('BUTTON')
    expect(wrapper.classes()).toContain('ca-btn--primary')
    expect(wrapper.classes()).toContain('ca-btn--md')

    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })

  it('传入 href 时渲染链接并保留地址', () => {
    const wrapper = mount(CaButton, {
      props: { href: '/changelog', variant: 'secondary' },
      slots: { default: '查看更新日志' },
    })

    expect(wrapper.element.tagName).toBe('A')
    expect(wrapper.attributes('href')).toBe('/changelog')
    expect(wrapper.attributes('type')).toBeUndefined()
    expect(wrapper.classes()).toContain('ca-btn--secondary')
  })

  it('禁用时不抛点击事件', async () => {
    const wrapper = mount(CaButton, { props: { disabled: true }, slots: { default: '保存' } })

    expect(wrapper.attributes('disabled')).toBeDefined()
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })

  it('加载时保留标签宽度、声明忙碌状态且不接受点击', async () => {
    const wrapper = mount(CaButton, { props: { loading: true }, slots: { default: '运行任务' } })

    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.classes()).toContain('is-loading')
    expect(wrapper.text()).toContain('运行任务')
    expect(wrapper.find('.ca-btn__progress').exists()).toBe(true)
    expect(wrapper.text()).toContain('加载中')

    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
  })
})

describe('CaCard', () => {
  it('按变体和内边距输出样式类', () => {
    const wrapper = mount(CaCard, { props: { variant: 'raised', padding: 'lg' } })

    expect(wrapper.classes()).toContain('ca-card--raised')
    expect(wrapper.classes()).toContain('ca-card--pad-lg')
  })
})

describe('CaBadge', () => {
  it('默认是细描边标记，可切换为反色标记', () => {
    const neutral = mount(CaBadge, { slots: { default: '演示数据' } })
    const strong = mount(CaBadge, { props: { tone: 'strong' }, slots: { default: '失败' } })

    expect(neutral.classes()).toContain('ca-badge--neutral')
    expect(strong.classes()).toContain('ca-badge--strong')
  })
})

describe('CaTabs', () => {
  const items = [
    { id: 'dag', label: '因果图' },
    { id: 'table', label: '指标表' },
    { id: 'report', label: '报告' },
  ]

  it('只让选中项进入键盘顺序，并关联面板', () => {
    const wrapper = mount(CaTabs, { props: { modelValue: 'table', items, ariaLabel: '结论文档形态' } })
    const tablist = wrapper.find('[role="tablist"]')
    const tabs = tablist.findAll('[role="tab"]')

    expect(tablist.exists()).toBe(true)
    expect(tablist.attributes('aria-label')).toBe('结论文档形态')
    expect(tabs).toHaveLength(3)
    expect(tabs[1]?.attributes('aria-selected')).toBe('true')
    expect(tabs[1]?.attributes('tabindex')).toBe('0')
    expect(tabs[0]?.attributes('tabindex')).toBe('-1')
    expect(tabs[0]?.attributes('aria-controls')).toContain('panel-dag')
    expect(tabs[1]?.attributes('id')).toContain('tab-table')
  })

  it('点击和方向键都通过 update:modelValue 请求切换，重复选择不抛出事件', async () => {
    const wrapper = mount(CaTabs, { props: { modelValue: 'table', items } })
    const tabs = wrapper.findAll('[role="tab"]')

    await tabs[1]?.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    await tabs[2]?.trigger('click')
    await tabs[0]?.trigger('keydown', { key: 'ArrowLeft' })
    await tabs[0]?.trigger('keydown', { key: 'Home' })
    await tabs[1]?.trigger('keydown', { key: 'ArrowRight' })
    await tabs[1]?.trigger('keydown', { key: 'End' })

    expect(wrapper.emitted('update:modelValue')).toEqual([
      ['report'],
      ['dag'],
      ['dag'],
      ['report'],
      ['report'],
    ])
  })

  it('方向键把焦点移到新的标签上', async () => {
    const wrapper = mount(CaTabs, { props: { modelValue: 'dag', items }, attachTo: document.body })

    await wrapper.findAll('[role="tab"]')[0]?.trigger('keydown', { key: 'ArrowRight' })
    await nextTick()
    await nextTick()

    expect(document.activeElement?.textContent?.trim()).toBe('指标表')
    wrapper.unmount()
  })

  it('切换键不会在只有一个选项时越界', async () => {
    const wrapper = mount(CaTabs, { props: { modelValue: 'only', items: [{ id: 'only', label: '唯一' }] } })

    await wrapper.find('[role="tab"]').trigger('keydown', { key: 'ArrowLeft' })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})

describe('CaInput', () => {
  it('标签绑定到控件，错误信息进入无障碍描述', () => {
    const wrapper = mount(CaInput, {
      props: { label: '任务名称', error: '不能为空', hint: '最多 40 个字' },
    })

    const control = wrapper.find('input')
    const label = wrapper.find('label')
    const describedBy = control.attributes('aria-describedby')?.split(' ') ?? []

    expect(label.attributes('for')).toBe(control.attributes('id'))
    expect(control.attributes('aria-invalid')).toBe('true')
    expect(describedBy).toHaveLength(2)
    expect(describedBy).toContain(wrapper.find('.ca-field__error').attributes('id'))
    expect(describedBy).toContain(wrapper.find('.ca-field__hint').attributes('id'))
  })

  it('输入内容通过 update:modelValue 抛出，不直接改属性', async () => {
    const wrapper = mount(CaInput, { props: { modelValue: '' } })

    await wrapper.find('input').setValue('门店促销')
    expect(wrapper.emitted('update:modelValue')).toEqual([['门店促销']])
  })

  it('multiline 渲染文本域', () => {
    const wrapper = mount(CaInput, { props: { multiline: true, rows: 6 } })
    const textarea = wrapper.find('textarea')

    expect(textarea.exists()).toBe(true)
    expect(textarea.attributes('rows')).toBe('6')
  })
})

describe('CaLoadingState', () => {
  it('骨架态声明忙碌并给出隐藏说明', () => {
    const wrapper = mount(CaLoadingState, { props: { lines: 2 } })

    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.findAll('.ca-loading__rule')).toHaveLength(3)
    expect(wrapper.find('.ca-loading__rule--short').exists()).toBe(true)
    expect(wrapper.text()).toContain('加载中')
  })

  it('确定进度线暴露进度值', () => {
    const wrapper = mount(CaLoadingState, { props: { variant: 'line', value: 0.42, label: '正在上传' } })

    expect(wrapper.attributes('role')).toBe('progressbar')
    expect(wrapper.attributes('aria-valuenow')).toBe('42')
    expect(wrapper.find('.ca-loading__fill--indeterminate').exists()).toBe(false)
    expect(wrapper.text()).toContain('正在上传')
  })

  it('未给进度值时是来回推进的不确定状态', () => {
    const wrapper = mount(CaLoadingState, { props: { variant: 'line' } })

    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.attributes('aria-valuenow')).toBeUndefined()
    expect(wrapper.find('.ca-loading__fill--indeterminate').exists()).toBe(true)
  })
})

describe('CaEmptyState', () => {
  it('默认居中，start 还原原型里的左对齐空态', () => {
    const centered = mount(CaEmptyState, { props: { title: '还没有会话', description: '上传材料后开始分析' } })
    const inline = mount(CaEmptyState, { props: { description: '已选择 2 类材料，可以开始。', align: 'start' } })

    expect(centered.classes()).toContain('ca-empty--center')
    expect(centered.text()).toContain('还没有会话')
    expect(centered.text()).toContain('上传材料后开始分析')
    expect(inline.classes()).toContain('ca-empty--start')
  })
})

describe('CaErrorState', () => {
  it('作为警告区域呈现标题、说明和错误码', () => {
    const wrapper = mount(CaErrorState, {
      props: { title: '这次分析没有完成', description: '可以重新运行。', code: 'request_id=8f3c' },
      slots: { actions: '<button type="button">重新运行</button>' },
    })

    expect(wrapper.attributes('role')).toBe('alert')
    expect(wrapper.find('.ca-error__title').text()).toBe('这次分析没有完成')
    expect(wrapper.find('.ca-error__description').text()).toBe('可以重新运行。')
    expect(wrapper.find('.ca-error__code').text()).toBe('request_id=8f3c')
    expect(wrapper.find('.ca-error__actions').exists()).toBe(true)
  })
})
