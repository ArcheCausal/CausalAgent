import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CausalGraph from '../../src/components/CausalGraph.vue'
import CausalGraphBlock from '../../src/components/CausalGraphBlock.vue'
import { projectCausalGraphForVis } from '../../src/renderers/graph-renderer'
import type { CausalGraphData, CausalGraphModel } from '../../src/types/domain'

/*
 * jsdom 没有 canvas，vis-network 无法真实实例化；这里替换的是第三方图形库本身，
 * 用于验证组件生命周期、数据更新和选择事件的接线，不当作真实图形渲染的验收。
 */
const visMock = vi.hoisted(() => {
  type GraphData = { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }

  class MockNetwork {
    static instances: MockNetwork[] = []
    data: GraphData
    options: Record<string, unknown>
    destroyed = false
    readonly handlers = new Map<string, (params: Record<string, unknown>) => void>()

    constructor(_container: HTMLElement, data: GraphData, options: Record<string, unknown>) {
      this.data = data
      this.options = options
      MockNetwork.instances.push(this)
    }

    on(event: string, callback: (params: Record<string, unknown>) => void): void {
      this.handlers.set(event, callback)
    }

    setOptions(): void {}

    setData(data: GraphData): void {
      this.data = data
    }

    destroy(): void {
      this.destroyed = true
    }

    emit(event: string, params: Record<string, unknown>): void {
      this.handlers.get(event)?.(params)
    }
  }

  return { MockNetwork }
})

vi.mock('vis-network/standalone', () => ({ Network: visMock.MockNetwork }))

const model: CausalGraphModel = {
  graph_id: 'graph_main',
  schema_version: 1,
  nodes: [
    { id: 'node_age', variable: 'age', label: 'age' },
    { id: 'node_income', variable: 'income', label: 'income' },
  ],
  edges: [
    { id: 'edge_age_income', source: 'node_age', target: 'node_income', edge_type: 'directed', weight: 0.42 },
    { id: 'edge_undirected', source: 'node_income', target: 'node_age', edge_type: 'undirected', weight: null },
  ],
}

const graphData: CausalGraphData = {
  nodes: [{ id: 'node_age', label: 'age' }],
  edges: [{ id: 'edge_age_income', from: 'node_age', to: 'node_income' }],
}

/** vis-network 是动态 import，组件挂载后要等实例真正创建出来再断言。 */
async function networkInstance(): Promise<InstanceType<typeof visMock.MockNetwork>> {
  await vi.waitFor(() => {
    expect(visMock.MockNetwork.instances).toHaveLength(1)
  })
  const [instance] = visMock.MockNetwork.instances
  if (!instance) throw new Error('vis-network 实例未创建')
  return instance
}

describe('projectCausalGraphForVis', () => {
  it('projects the business model into vis-network nodes and edges', () => {
    const projected = projectCausalGraphForVis(model)

    expect(projected.nodes).toEqual([
      { id: 'node_age', label: 'age' },
      { id: 'node_income', label: 'income' },
    ])
    expect(projected.edges[0]).toMatchObject({
      id: 'edge_age_income',
      from: 'node_age',
      to: 'node_income',
      arrows: 'to',
      dashes: false,
      label: '0.42',
    })
  })

  it('renders undirected edges dashed and without arrows', () => {
    const projected = projectCausalGraphForVis(model)

    expect(projected.edges[1]).toMatchObject({ arrows: '', dashes: true })
    expect(projected.edges[1]?.label).toBeUndefined()
  })
})

describe('CausalGraph', () => {
  beforeEach(() => {
    visMock.MockNetwork.instances.length = 0
  })

  it('creates one instance, updates it on data change and destroys it on unmount', async () => {
    const wrapper = mount(CausalGraph, { props: { graph: graphData } })
    const instance = await networkInstance()

    const next: CausalGraphData = {
      nodes: [{ id: 'node_a', label: 'A' }, { id: 'node_b', label: 'B' }],
      edges: [{ id: 'edge_a_b', from: 'node_a', to: 'node_b' }],
    }
    await wrapper.setProps({ graph: next })

    expect(visMock.MockNetwork.instances).toHaveLength(1)
    expect(instance.data.nodes).toHaveLength(2)
    expect(instance.data.edges[0]?.id).toBe('edge_a_b')

    wrapper.unmount()
    expect(instance.destroyed).toBe(true)
  })

  it('emits node and edge selections in select mode', async () => {
    const wrapper = mount(CausalGraph, { props: { graph: graphData, mode: 'select' } })
    const instance = await networkInstance()

    instance.emit('selectNode', { nodes: ['node_age'] })
    instance.emit('selectEdge', { edges: ['edge_age_income'] })

    expect(wrapper.emitted('selectNode')?.[0]?.[0]).toEqual({ id: 'node_age', label: 'age' })
    expect(wrapper.emitted('selectEdge')?.[0]?.[0]).toMatchObject({
      id: 'edge_age_income',
      from: 'node_age',
      to: 'node_income',
    })
  })

  it('does not emit selections in view mode', async () => {
    const wrapper = mount(CausalGraph, { props: { graph: graphData } })
    const instance = await networkInstance()

    expect(wrapper.get('.causal-graph-container')).toBeTruthy()
    expect(instance.handlers.has('selectNode')).toBe(false)
    expect(instance.options.interaction).toMatchObject({ selectable: false })
  })
})

describe('CausalGraphBlock', () => {
  beforeEach(() => {
    visMock.MockNetwork.instances.length = 0
  })

  it('projects the model and forwards selections upward', async () => {
    const wrapper = mount(CausalGraphBlock, {
      props: { title: '主要因果关系', graph: model, error: null },
    })
    const instance = await networkInstance()

    expect(instance.data.nodes).toHaveLength(2)
    instance.emit('selectNode', { nodes: ['node_age'] })
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('selectNode')?.[0]?.[0]).toEqual({ id: 'node_age', label: 'age' })
    expect(wrapper.get('.report-graph-selection').text()).toContain('age')
  })

  it('shows a controlled message when the graph asset is unavailable', () => {
    const wrapper = mount(CausalGraphBlock, {
      props: { title: '主要因果关系', graph: null, error: '因果图数据不可用。' },
    })

    expect(wrapper.get('.report-chart-state').text()).toBe('因果图数据不可用。')
  })
})
