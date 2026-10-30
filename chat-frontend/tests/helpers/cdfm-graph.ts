import type { CausalGraphModel } from '../../src/types/domain'

// 纯函数和组件的显式契约输入，不代表真实 CDFM 执行结果。
export function cdfmGraph(): CausalGraphModel {
  return {
    graph_id: 'cdfm_contract_input',
    schema_version: 1,
    // 刻意让节点顺序与边端点顺序不同，检查展示不会交换两个概率。
    nodes: [
      { id: 'node_y', variable: 'Y', label: '结果 Y' },
      { id: 'node_x', variable: 'X', label: '变量 X' },
    ],
    edges: [
      {
        id: 'edge_directed', source: 'node_x', target: 'node_y', edge_type: 'directed', weight: null,
        prediction: { forward_probability: 0.8765432109876543, reverse_probability: 0.1234567890123456 },
      },
      {
        id: 'edge_bidirected', source: 'node_y', target: 'node_x', edge_type: 'bidirected', weight: null,
        prediction: { forward_probability: 0.9876543210987654, reverse_probability: 0.7654321098765432 },
      },
    ],
    metadata: {
      algorithm: 'causal.cdfm',
      diagnostics: { status: 'ok', metrics: { threshold: 0.6123456789012345, retained_metric: 7 } },
    },
  }
}
