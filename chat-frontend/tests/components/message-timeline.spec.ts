import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import MessageTimeline from '../../src/components/MessageTimeline.vue'
import { useJobsStore } from '../../src/stores/jobs.store'

function phase(
  inputId: number,
  status: string,
  events: Array<Record<string, unknown>>,
): { phaseSequence: number; status: string; elapsedSeconds: number; lastEventId: number; analysisJobId: string; analysisJobInputId: number; events: Array<Record<string, unknown>> } {
  return {
    phaseSequence: inputId,
    status,
    elapsedSeconds: 12.4,
    lastEventId: 100 + events.length,
    analysisJobId: 'job-1',
    analysisJobInputId: inputId,
    events,
  }
}

describe('MessageTimeline', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('keeps a live final result visible outside the collapsible thinking panel', () => {
    const jobs = useJobsStore()
    const job = jobs.startFromResponse('job-1', 'session-1', 'running')
    job.thinking.status = 'completed'
    job.thinking.finalResult = { type: 'text', summary: '实时分析结果' }

    const wrapper = mount(MessageTimeline, {
      props: {
        messages: [{
          localId: 'message-1',
          sender: 'user',
          text: '请开始分析',
          analysisJobId: 'job-1',
        }],
      },
    })

    expect(wrapper.get('.thinking-block')).toBeTruthy()
    expect(wrapper.get('.message.ai-message').text()).toContain('实时分析结果')
  })

  it('shows a public decision with its own prefix inside the step details', async () => {
    const jobs = useJobsStore()
    const job = jobs.startFromResponse('job-1', 'session-1', 'running')
    job.thinking.status = 'completed'
    job.thinking.stepOrder = ['s1']
    job.thinking.steps = {
      s1: {
        stepId: 's1',
        nodeName: 'deep_agent',
        title: '执行 Deep Agent 分析',
        status: 'completed',
        duration: 1.2,
        details: [
          { kind: 'text', text: '调用工具：pc（参数字段：data）', tone: 'default' },
          {
            kind: 'decision',
            key: 's1:algorithm:pc',
            decisionKind: 'algorithm',
            toolName: 'pc',
            streamId: 'decision-1',
            text: '选择 PC，因为样本充足',
            complete: true,
            released: true,
            pending: [],
          },
        ],
      },
    }

    const wrapper = mount(MessageTimeline, {
      props: {
        messages: [{
          localId: 'message-1',
          sender: 'user',
          text: '请开始分析',
          analysisJobId: 'job-1',
        }],
      },
    })

    await wrapper.get('.step-header').trigger('click')
    const details = wrapper.get('.step-details')
    expect(details.isVisible()).toBe(true)
    expect(details.text()).toContain('调用工具：pc（参数字段：data）')
    expect(details.text()).toContain('算法决策：选择 PC，因为样本充足')
  })

  it('历史会话的执行记录只出现在发起它的那条提问下面', () => {
    const jobs = useJobsStore()
    // 上一个页面留下的终态记录：重新加载后不能再挂到答案消息上。
    jobs.startFromResponse('job-1', 'session-1', 'succeeded')
    const historyPhase = phase(21, 'completed', [
      { type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析' },
      { type: 'node_end', event_id: 2, step_id: 's1', node_name: 'deep_agent', title: '执行 Deep Agent 分析', duration: 4, status: 'completed' },
    ])

    const wrapper = mount(MessageTimeline, {
      props: {
        messages: [
          { localId: 'message-1', sender: 'user', text: '请分析这份数据', analysisJobId: 'job-1', analysisJobInputId: 21, thinkingAfter: historyPhase },
          { localId: 'message-2', sender: 'ai', text: '报告正文', analysisJobId: 'job-1', analysisJobInputId: 21 },
        ],
      },
    })

    expect(wrapper.findAll('.thinking-block')).toHaveLength(1)
    expect(wrapper.get('.thinking-header').text()).toContain('已处理')
    expect(wrapper.get('.thinking-block').text()).toContain('执行 Deep Agent 分析')
  })

  it('仍在执行的 Job 只在它当前那条输入上继续推进运行态记录', () => {
    const jobs = useJobsStore()
    // 刷新后运行态记录由当前那条输入的历史阶段建立。
    jobs.seedPhase('session-1', phase(24, 'running', [
      { type: 'node_start', event_id: 9, step_id: 's2', node_name: 'deep_agent', title: '继续分析' },
    ]))
    const record = jobs.get('job-1')
    expect(record).toBeTruthy()
    jobs.applyEvent('job-1', {
      event: 'node_start',
      id: 200,
      known: true,
      data: { type: 'node_start', step_id: 's3', node_name: 'deep_agent', title: '实时追加' },
    }, record?.generation ?? 0)
    const firstPhase = phase(21, 'completed', [
      { type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '第一次分析' },
      { type: 'node_end', event_id: 2, step_id: 's1', node_name: 'deep_agent', title: '第一次分析', duration: 2, status: 'completed' },
    ])
    const runningPhase = phase(24, 'running', [
      { type: 'node_start', event_id: 9, step_id: 's2', node_name: 'deep_agent', title: '继续分析' },
    ])

    const wrapper = mount(MessageTimeline, {
      props: {
        messages: [
          { localId: 'message-1', sender: 'user', text: '第一次提问', analysisJobId: 'job-1', analysisJobInputId: 21, thinkingAfter: firstPhase },
          { localId: 'message-2', sender: 'ai', text: '请补充', analysisJobId: 'job-1', analysisJobInputId: 21 },
          { localId: 'message-3', sender: 'user', text: '继续', analysisJobId: 'job-1', analysisJobInputId: 24, thinkingAfter: runningPhase },
        ],
      },
    })

    const blocks = wrapper.findAll('.thinking-block')
    expect(blocks).toHaveLength(2)
    expect(blocks[0]?.text()).toContain('第一次分析')
    expect(blocks[0]?.text()).not.toContain('继续分析')
    expect(blocks[1]?.text()).toContain('继续分析')
    expect(blocks[1]?.text()).toContain('实时追加')
  })

  it('追问固定下来的记录与运行态记录各自展示自己的阶段', () => {
    const jobs = useJobsStore()
    const record = jobs.startFromResponse('job-1', 'session-1', 'running')
    const step = (id: number, stepId: string, title: string) => ({
      event: 'node_start', id, known: true, data: { type: 'node_start', step_id: stepId, node_name: 'deep_agent', title },
    })
    jobs.applyEvent('job-1', step(1, 's1', '第一次分析'), record.generation)
    jobs.applyEvent('job-1', {
      event: 'interrupt', id: 2, known: true, data: { type: 'interrupt', question_id: 'q-1', message: '请补充' },
    }, record.generation)

    const frozen = jobs.startResume('job-1')
    jobs.applyEvent('job-1', step(3, 's2', '继续分析'), record.generation)

    const wrapper = mount(MessageTimeline, {
      props: {
        messages: [
          { localId: 'message-1', sender: 'user', text: '第一次提问', analysisJobId: 'job-1', frozenThinking: frozen ?? undefined },
          { localId: 'message-2', sender: 'user', text: '继续', analysisJobId: 'job-1' },
        ],
      },
    })

    const blocks = wrapper.findAll('.thinking-block')
    expect(blocks).toHaveLength(2)
    expect(blocks[0]?.text()).toContain('第一次分析')
    expect(blocks[0]?.text()).not.toContain('继续分析')
    expect(blocks[0]?.text()).toContain('已处理')
    expect(blocks[1]?.text()).toContain('继续分析')
    expect(blocks[1]?.text()).not.toContain('第一次分析')
  })

  it('刷新后仍在执行的会话由运行态记录继续展示，答案到达后不会消失', async () => {
    const jobs = useJobsStore()
    const runningPhase = phase(21, 'running', [
      { type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '第一次分析' },
    ])
    jobs.seedPhase('session-1', runningPhase)
    const record = jobs.get('job-1')
    expect(record).toBeTruthy()
    jobs.applyEvent('job-1', {
      event: 'node_start',
      id: 200,
      known: true,
      data: { type: 'node_start', step_id: 's2', node_name: 'deep_agent', title: '实时追加' },
    }, record?.generation ?? 0)

    const wrapper = mount(MessageTimeline, {
      props: {
        messages: [
          { localId: 'message-1', sender: 'user', text: '请分析这份数据', analysisJobId: 'job-1', analysisJobInputId: 21, thinkingAfter: runningPhase },
        ],
      },
    })

    expect(wrapper.findAll('.thinking-block')).toHaveLength(1)
    expect(wrapper.get('.thinking-block').text()).toContain('实时追加')

    jobs.applyEvent('job-1', {
      event: 'final_result',
      id: 300,
      known: true,
      data: { type: 'final_result', data: { type: 'text', summary: '最终答案' } },
    }, record?.generation ?? 0)
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('.thinking-block')).toHaveLength(1)
    expect(wrapper.get('.message.ai-message').text()).toContain('最终答案')
  })

  it('等待补充输入的历史会话不重复展示同一条问题', () => {
    const jobs = useJobsStore()
    jobs.seedPhase('session-1', phase(21, 'waiting_input', [
      { type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '第一次分析' },
    ]))
    jobs.observeActive({
      job_id: 'job-1',
      session_id: 'session-1',
      status: 'waiting_input',
      current_question_id: 'q-1',
      current_waiting_prompt: '请补充样本说明',
    })

    const wrapper = mount(MessageTimeline, {
      props: {
        activeJob: jobs.get('job-1'),
        messages: [
          { localId: 'message-1', sender: 'user', text: '请分析这份数据', analysisJobId: 'job-1', analysisJobInputId: 21, thinkingAfter: phase(21, 'waiting_input', [
            { type: 'node_start', event_id: 1, step_id: 's1', node_name: 'deep_agent', title: '第一次分析' },
          ]) },
          { localId: 'message-2', sender: 'ai', text: '请补充样本说明', analysisJobId: 'job-1', analysisJobInputId: 21 },
        ],
      },
    })

    expect(wrapper.findAll('.thinking-block')).toHaveLength(1)
    expect(wrapper.get('.thinking-header').text()).toContain('等待补充输入')
    expect(wrapper.findAll('.message.ai-message')).toHaveLength(1)
    expect(wrapper.text().split('请补充样本说明')).toHaveLength(2)
  })

  it('历史答案已持久化时仍在末尾恢复唯一方案，切换会话后保留截止时间和身份', async () => {
    const jobs = useJobsStore()
    const planVersion = 'c'.repeat(64)
    const expiresAt = new Date(Date.now() + 240000).toISOString()
    const historyPhase = phase(21, 'completed', [
      { type: 'node_start', event_id: 1, step_id: 's1', title: '准备数据' },
    ])
    jobs.seedPhase('session-1', historyPhase)
    jobs.observeActive({
      job_id: 'job-1', session_id: 'session-1', status: 'waiting_input',
      current_question_id: 'q-plan', current_waiting_prompt: '请确认方案', input_wait_expires_at: expiresAt,
      current_interaction: {
        interaction_type: 'data_processing_plan_confirmation', plan_version: planVersion,
        plan: { summary: '按日期聚合', observation_unit: '日期', transformations: ['统计每日数量'], variable_semantics: { count: '每日数量' }, limitations: ['仅包含已有记录'] },
      },
    })
    const messages = [
      { localId: 'm1', sender: 'user' as const, text: '分析', analysisJobId: 'job-1', analysisJobInputId: 21, thinkingAfter: historyPhase },
      { localId: 'm2', sender: 'ai' as const, text: { type: 'human_input_required', summary: '请确认方案' }, analysisJobId: 'job-1', analysisJobInputId: 21 },
    ]
    const wrapper = mount(MessageTimeline, { props: { messages, activeJob: jobs.get('job-1') } })
    expect(wrapper.findAll('.current-interaction')).toHaveLength(1)
    expect(wrapper.text().split('请确认方案')).toHaveLength(2)
    expect(wrapper.get('.current-interaction').text()).toContain('按日期聚合')
    expect(wrapper.get('.current-interaction').element).toBe(wrapper.element.lastElementChild)
    await wrapper.get('.processing-plan-confirm').trigger('click')
    expect(wrapper.emitted('choose-processing-option')?.[0]).toEqual([{ jobId: 'job-1', questionId: 'q-plan', planVersion, optionId: '__confirm_processing_plan__' }])
    await wrapper.setProps({ activeJob: null, messages: [] })
    expect(wrapper.find('.current-interaction').exists()).toBe(false)
    await wrapper.setProps({ messages, activeJob: jobs.get('job-1') })
    expect(wrapper.findAll('.processing-plan-confirm')).toHaveLength(1)
    expect(jobs.get('job-1')?.thinking.waitingInput?.expiresAt).toBe(expiresAt)
    wrapper.unmount()
  })

  it('连续回答的历史问题只显示已回答，当前问题只在末尾提供入口', async () => {
    const jobs = useJobsStore()
    jobs.startFromResponse('job-1', 'session-1', 'running')
    const messages = []
    for (let index = 1; index <= 3; index += 1) {
      const job = jobs.observeActive({
        job_id: 'job-1', session_id: 'session-1', status: 'waiting_input',
        current_question_id: `q-${index}`, current_waiting_prompt: `问题${index}`,
        current_interaction: { interaction_type: 'data_processing_question', expires_at: new Date(Date.now() + 1000).toISOString() },
      })
      const identity = { jobId: job.jobId, questionId: `q-${index}`, planVersion: null }
      jobs.beginResume(identity)
      const frozenThinking = jobs.acceptResume(identity, 'queued') ?? undefined
      messages.push({ localId: `m${index}`, sender: 'user' as const, text: `回答${index - 1}`, analysisJobId: 'job-1', frozenThinking })
    }
    const active = jobs.observeActive({ job_id: 'job-1', session_id: 'session-1', status: 'waiting_input', current_question_id: 'q-4', current_waiting_prompt: '当前问题' })
    const wrapper = mount(MessageTimeline, { props: { messages, activeJob: active } })
    await new Promise((resolve) => setTimeout(resolve, 1200))
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.processing-options-status').filter((item) => item.text().includes('已回答'))).toHaveLength(3)
    expect(wrapper.find('.processing-options-expired').exists()).toBe(false)
    expect(wrapper.findAll('.processing-option-other')).toHaveLength(1)
    expect(wrapper.get('.current-interaction').text()).toContain('当前问题')
    wrapper.unmount()
  })
})
