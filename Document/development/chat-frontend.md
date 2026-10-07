# 普通用户应用 Vue 前端

文档职责：记录普通用户应用 Vue 3 工程的目录边界、唯一入口、地址与会话的映射、Session—Job—SSE 状态约束、构建、测试、发布与回退事实。

适用范围：`chat-frontend/`、Flask 的 `/dashboard` 页面入口及其 Docker 构建集成；不覆盖官网前端、管理员前端、RAG 评测台、数据库 schema 或 worker 业务规则。

## 当前状态与入口


普通用户应用由三个地址组成：`/dashboard`、`/dashboard/session/<session_id>` 和 `/dashboard/settings`。`app/chat/page_routes.py` 的页面入口统一要求 `dashboard.access`：未登录访客跳转 `/auth/sign-in?next=<原地址>`，已登录但缺少该权限的请求返回受控 403 页面；页面资源 `/dashboard-assets/<path:filename>` 使用同一权限边界。

入口 HTML 使用不缓存策略，`assets/` 下的带 hash 资源使用长期 `public, immutable` 缓存。构建目录默认是 `chat-frontend/dist/`；Docker 运行时使用 `/opt/causalagent-chat`，也可由 `CHAT_FRONTEND_DIST_DIR` 指定。缺少 `index.html` 时页面入口和资源路径统一返回带 request ID 的 503 与 `chat_frontend_missing`，不回退到旧版或返回半成品资源。

本地 Vite 开发服务器使用 5174 端口和 `/dashboard-assets/` base。设置 `CHAT_VITE_DEV_SERVER_URL=http://127.0.0.1:5174` 后，Flask 的 `/dashboard*` 会跳转到 `http://127.0.0.1:5174/dashboard-assets<原路径>`。

## 地址、会话与退出

地址是当前会话的唯一来源：`src/runtime/navigation/app-route.ts` 从 `location.pathname` 解析工作区、设置页和会话标识，生产路径是 `/dashboard*`，开发服务器 `/dashboard-assets*` 会归一成同一套路径；非法会话标识回落到工作区。选中、创建或删除会话时通过 `history.pushState` 更新地址，浏览器前进后退会重新加载对应会话。

应用不再提供登录与注册界面，也不再展示未登录公开预览：`auth.check()` 确认未登录时，`App.vue` 用 `location.replace` 跳转到 `/auth/sign-in?next=/dashboard`，登录和注册页面由官网前端提供。退出登录调用 `/api/logout`，清空会话、文件、任务与草稿状态后回到官网首页 `/`。

用户菜单按 `/api/check_auth` 返回的权限显示入口：拥有 `admin.access` 显示“管理后台”（`/admin/database`），拥有 `rag_eval.access` 显示“RAG 评测台”（`/rag-eval`）。

公开预览与内部登录面板相关的 `src/components/PublicPreview.vue`、`src/components/AuthPanel.vue`、`src/preview/public-preview-data.ts`、`src/runtime/analytics/analytics-client.ts` 和对应测试已随入口拆分删除，普通应用不再上报匿名预览事件；`POST /api/analytics/events` 的接口与事件目录保持不变，见 [`observability.md`](observability.md)。

## 工程边界

`chat-frontend/src/` 按 API schema、Pinia store、Job runtime、组件、渲染器和设计样式分域。后端响应先以 `unknown` 接收，再由 Zod schema 解析；Store 只保存可序列化领域状态。`AbortController`、ReadableStream reader、定时器和 vis-network 实例由 controller 或 Vue 生命周期持有，不放入 Pinia。

普通端 Vue 化不改变 Flask 业务 API、Session/文件/Job 数据规则、数据库、worker 或 SSE 路径。旧版静态文件已经退出路由、配置和部署契约，并已按用户授权从仓库物理删除；它们在旧页面上新增的行为已经在本 Vue 工程按等价语义重新实现。

## SSE 与 Job 状态

Vue 的 Job 传输层使用 `fetch()` 和 `ReadableStream`，不使用原生 `EventSource`。`src/api/events.schemas.ts` 自行解析标准 `id`、`event`、`data` 字段，支持分块、CRLF/LF、多行 `data`、注释和 UTF-8 边界；Flask SSE 路径仍为 `/api/agent/jobs/<job_id>/events`，公共事件内容不变。

首个订阅从历史阶段的 `last_event_id` 使用查询参数 `last_event_id` 开始；断线重连通过 `Last-Event-ID` 请求头显式传递当前 `transport_cursor`。传输游标、可见渲染游标和活动接口的 `last_event_id` 观测值分别保存，活动 Job 查询不能覆盖历史恢复起点。未知但结构合法且带有效 ID 的事件只推进传输游标，不进入可见投影；非法 JSON、字段不完整、ID 无效或 `event` 与 `data.type` 不一致会进入稳定协议错误路径，不推进坏包游标，也不会无限重试。网络错误和 5xx 使用有界退避重连，4xx 和协议错误不重试。

JobController 为每个 Job 管理订阅 generation，切换 Session、取消、登出或卸载时使旧回调失效。`waiting_input` 是可恢复暂停态；`final_result`、`error` 和 `canceled` 进入终态并停止订阅，终态保留已渲染结果但释放原始文本拼接和语义去重缓冲。创建、恢复、取消分别使用独立幂等键，取消的 409 终态冲突按后端状态对账。运行态不显示独立取消按钮；发送键变为带旋转进度环和中心方块的停止键，再次点击调用相同的取消 API。

## 阶段明细、公开决策与展示节奏

阶段明细只挂在同一个 `step_id` 下：`node_start` 创建阶段，`progress`、`decision`、`tool_call_start`、`tool_call_result` 和 `decision_delta` 作为明细追加，`node_end` 结束阶段。明细早于父 `node_start` 到达时先按 `step_id` 暂存，父阶段出现后按原顺序补绘一次，避免把明细挂到错误的阶段或改变阶段顺序。

公开决策由两类事件表达。`decision_delta` 只携带同一 `stream_id` 的批次增量和 `decision_kind`，页面按 `decision_kind` 显示`算法决策：`、`检索决策：`或`最终决策：`前缀，并按码点逐字推进；完整 `decision` 到达时只结束该决策流，不会重复插入文本。同一工具的 `tool_call_start`/`tool_call_result` 在该决策的逐字展示追平之前只能暂存，追平后按原顺序补在决策文本后面；如果缺少完整 `decision` 结束事件，工具结果会强制放行，任务进入终态时同样放行所有未结束的决策，不允许永久挂起。历史回放只有完整 `decision`，因此直接显示带前缀的完整文本。

执行记录只挂在发起它的那条用户消息下面。历史阶段由 `thinking_after` 静态投影；答案已经作为独立消息存在时不再复用运行态记录，答案消息本身也不显示执行记录，只有本页新发送的提问消息才直接消费运行态记录。加载会话时只为仍在执行的 Job 建立运行态记录，`JobRecord.phaseInputId` 记录它代表的分析输入，同一 Job 更早的输入继续按历史阶段展示，避免一份记录在两条消息下重复出现。追问恢复会把上一阶段的执行记录固定到发起它的用户消息上（`ChatMessage.frozenThinking`），运行态记录从空投影和原游标继续。

聊天草稿和公开决策共用同一套展示节奏：40 字/秒、25ms 步进推进展示游标，完整缓冲区始终以服务端内容为准，展示速度不影响工具执行。`final_result` 到达时如果已有文字草稿，终态文本校正到同一草稿；没有草稿或结果不是文字时才新增一条结果消息。`prefers-reduced-motion` 和所有终态直接展示完整文本，暂停等待输入时展示继续推进。

聊天区滚动按 80px 阈值跟随最新内容：用户在阈值外主动上滑后停止自动跟随，回到底部后恢复；发送、切换或创建会话、新增消息和展示推进都会在跟随状态下滚动到最新内容。逐字推进、定时器和滚动容器由组件或 runtime controller 持有，不进入 Pinia。

## 结构化报告渲染

报告终态由 `type=report`、`render_mode=structured` 和 `document` 组成，`MessageBody.vue` 把它交给 `ReportRenderer.vue`；报告块的 HTML 不在 `MessageBody.vue` 里拼接。渲染器按块类型分发到 `ReportSection.vue`、`MarkdownBlock.vue`、`ChartBlock.vue` 和 `CausalGraphBlock.vue`：章节递归渲染子块，Markdown 块继续调用 `renderers/markdown-adapter.ts`，因此列表、标题、表格、引用、代码块和链接与普通聊天共用同一套解析。来源页脚按来源分组展示证据，知识库来源的长引用默认收起，来源标题旁只显示一个折叠箭头，点击箭头展开或收起证据；证据被正文块引用时提供“定位正文”按钮并滚动到对应块；前端也兼容历史报告正文中已存在但未填入结构化字段的合法 `ev_...` ID。报告文档在渲染前由 `renderers/report-document.ts` 用 Zod 防御性解析一次：未知块类型降级成占位块，缺失或非法的图表、因果图资源引用降级成受控提示，顶层载荷不合法时显示报告不可用提示，都不能让整页崩溃。

图表只读取经过校验的资源数据，使用 SVG 和 CSS 绘制直方图、分类柱状图和相关性热力图，第一阶段不引入第三方图表库，也不支持缩放、拖拽和导出。因果图只接受业务模型，由 `projectCausalGraphForVis()` 投影成 vis-network 载荷；`CausalGraph.vue` 支持 `view` 和 `select` 两种模式，数据变化时原地更新 `setData`，卸载时销毁实例，`select` 模式下向上抛出 `selectNode`/`selectEdge`。图形库仍然动态加载，初始入口不静态包含 vis-network。报告正文与普通聊天和追问消息共用同一条消息盒子，`.report-document` 只保留块间距与文字颜色，不再自带底色、边框和内边距；图表和因果图块仍各自使用白色卡片。报告的颜色、间距、标题层级、表格和移动端布局由 `.report-document` 上的 CSS 变量与各组件作用域样式控制，LLM 不返回类名或样式。

## 开发与构建

在仓库根目录执行：

```powershell
Push-Location chat-frontend
npm ci
npm run dev
```

`npm run dev` 只启动 Vite；API 由 Vite 代理到 `http://127.0.0.1:5001`。完整代码质量门和构建命令为：

```powershell
npm run lint
npm run typecheck
npm run test:unit
npm run test:components
npm run test:e2e:mock
npm run build
npm run check
Pop-Location
```

`npm run check` 当前串联上述检查。构建时图形库通过动态 import 延迟加载，初始入口不静态包含 vis-network；`public/vendor/marked.min.js` 通过 `markdown-adapter.ts` 兼容现有 Markdown 行为。当前原始 HTML 输出行为是已接受的兼容风险，本阶段不偷偷增加清洗策略或 CSP 语义变化。

## Docker 与启动失败边界

`Dockerfile` 的 `chat-builder` 使用 Node 24 Alpine 执行 `npm ci` 和 `npm run build`，将产物复制到 `/opt/causalagent-chat`；最终 Python runtime 不包含 Node、npm 或 Vite。三套 Compose 向 `app` 传递四个前端的产物目录和开发服务器变量，不再传递入口选择变量。

构建缺失属于运行期请求边界：`/dashboard*` 和 `/dashboard-assets/...` 返回 503、`chat_frontend_missing` 与 request ID，不回退到旧静态入口或返回半成品资源。

## 验收、发布与回退

自动化检查只证明代码、协议夹具、组件、Mock 浏览器和部署静态契约；不证明真实 Flask、Cookie Session、MySQL/PostgreSQL、worker、文件上传、模型、Chrome/Edge 双浏览器或桌面壳。发布前必须由人工在官网登录页与 `/dashboard` 工作区完成注册、登录/刷新/登出、未登录跳转与回跳、会话地址刷新与前进后退、空 Session、消息成功/失败、Session 管理、文件、Web Search、普通 Job、Thinking、图、Markdown/图片、SSE 断线、active Job 恢复、waiting_input/resume、运行态停止对账、错误恢复、快速切换、四视口、Chrome、Edge 和桌面壳检查，并保留记录。

当前部署产物只包含 Vue 构建链。回退通过部署上一份经过验证的代码与镜像完成，不触碰 Session、Job、消息或数据库；不再依赖同一运行版本中的旧前端路由。

## 旧静态文件清理

旧版普通端静态页面及其样式、脚本已经删除：`app/static/chat.html`、`app/static/css/style.css`，以及 `app/static/js/` 下的 `script.js`、`chat_layout_state.js`、`execution_phase_state.js`、`job_subscription_state.js`、`stream_state.js` 和 `marked.min.js`。这些脚本原先承载 develop 在旧页面上的公开决策、假流式展示和滚动跟随改动，对应行为已经在本 Vue 工程实现，删除后普通应用只在 `/dashboard` 提供 Vue 构建产物。

只验证这些静态文件的测试随实现一起删除：`tests/unit/frontend/` 的四个 Node 测试，以及 `admin-frontend/tests/e2e-mock/chat-auth.spec.ts`。后者直接读取旧页面文件并用旧页面的元素 id 断言管理员入口与越权提示；管理员端 Mock E2E 仍由 `admin-frontend/tests/e2e-mock/admin-ui.spec.ts` 覆盖，管理员入口与越权回跳的真实浏览器覆盖在 `admin-frontend/tests/e2e/admin.spec.ts`，该文件目前使用的仍是旧页面元素 id，需要按 Vue 普通端选择器更新后才能作为有效证据。

`app/static/rag_eval_app/` 后来随 RAG 评测台入口拆分移动到 `app/rag_eval/frontend_dist/`，`app/static/` 目录已随之移除，应用不再暴露 Flask 默认的 `/static` 路由。已用 `rg` 复查，仓库中不再存在指向这些路径的代码、配置或测试引用。
