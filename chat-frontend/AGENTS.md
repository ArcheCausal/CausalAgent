# 普通用户 Vue 前端局部约束

生效目录：`chat-frontend/` 及其子目录。

- 该目录是普通用户应用的独立 Vue 3 工程；不得在普通组件任务中顺带修改 Flask、数据库、worker、Docker、官网或管理员端。
- 本工程只提供登录后的工作区：入口是 `/dashboard`、`/dashboard/session/<id>` 和 `/dashboard/settings`，资源前缀是 `/dashboard-assets/`；未登录或 Session 失效时跳转 `/auth/sign-in?next=/dashboard`，不得在本工程内重新实现登录、注册或未登录公开预览。
- 地址是当前会话的唯一来源：路径解析集中在 `src/runtime/navigation/app-route.ts`，选中、创建或删除会话必须通过该模块的链接同步地址，不得只用组件内部状态记录当前会话。
- 所有后端响应先按 `unknown` 接收，再通过 Zod schema 解析；禁止使用 `response.json() as SomeDto`。
- `Pinia` 只能保存可序列化的领域状态。`AbortController`、ReadableStream reader、定时器、DOM 节点和 vis-network 实例只能由组件生命周期或 runtime controller 持有。
- Job SSE 必须使用 `fetch()` 和 `ReadableStream`，不得使用原生 `EventSource`。未知但结构合法的命名事件推进传输游标；坏包不推进游标，并进入有界、可观察的错误路径。
- Markdown 只通过 `src/renderers/markdown-adapter.ts` 调用 vendored marked；不得在组件中访问全局 marked。当前原始 HTML 行为是有意保留的兼容风险，不在本轮加入清洗策略。
- 图形库必须延迟加载，并在组件卸载时销毁实例；初始入口不得静态包含 vis-network。
- 公开决策必须落在对应 `step_id` 的步骤详情里：`decision_delta` 只推进该决策的展示文本，`decision_kind` 决定公开前缀，同一工具的 `tool_call_start`/`tool_call_result` 在该决策完成前只能暂存；`node_start` 未到的明细先暂存，父阶段出现后按原顺序补绘一次。
- 逐字推进、滚动跟随和定时器只能由组件或 runtime controller 持有：Pinia 只保存完整缓冲区、步骤明细和待补绘事件。用户主动向上滚动后必须停止自动跟随，`prefers-reduced-motion` 和终态直接展示完整文本。
- 修改后至少运行 `npm ci`、`npm run lint`、`npm run typecheck`、单元测试、组件测试、Mock Playwright E2E 和 `npm run build`（在环境可行范围内）。Mock E2E 不得表述为真实 Flask、worker 或模型验收。
