# 普通用户 Vue 前端实现基线

文档职责：记录普通用户 Vue 前端的唯一入口、API 形状、验收夹具和当前未覆盖风险。

适用范围：`chat-frontend/` 独立构建及 Flask 普通端入口；不修改管理员端、RAG 工作台、数据库或 worker。

## 入口与行为矩阵

| 能力 | 新前端入口 | 当前契约 | 本阶段证据 |
| --- | --- | --- | --- |
| 认证 | `/dashboard`（工作区入口）；未登录跳转 `/auth/sign-in?next=<原地址>`，登录页由官网前端提供 | `/api/check_auth`、`/api/login`、`/api/register`、`/api/logout` | 组件测试、Mock E2E、Flask 页面入口契约 |
| 会话 | `ChatWorkspace` | `/api/sessions` 返回 `[session_id, info]` 元组；加载/新建/改名/删除沿用旧路径 | API schema、store 单元测试 |
| 文件 | `ChatWorkspace` / `Composer` | `/api/files`、`/api/upload_file`、`/api/delete_file` | API schema；完整上传 E2E 待后续阶段 |
| 任务 | `JobController` | `/api/agent/jobs`、`active`、`resume`、`cancel` | reducer、contract、Mock E2E |
| SSE | `JobSseTransport` | `/api/agent/jobs/<job_id>/events`；`fetch()` + `ReadableStream`；不使用 EventSource | 分块/未知事件/坏包/游标/重连单元测试 |
| 阶段明细 | `ThinkingStepDetails` | `progress`、`decision`、`tool_call_start`、`tool_call_result`、`node_retry`、`node_end` 落在同一 `step_id`；明细早于父 `node_start` 时先暂存，父阶段出现后补绘一次 | reducer 单测、组件测试 |
| 公开决策 | `ThinkingStepDetails` | `decision_delta` 按 `stream_id` 与批次序号增量推进，`decision_kind` 映射`算法决策：`/`检索决策：`/`最终决策：`；完整 `decision` 只结束该决策流，同一工具的工具事件等展示追平后按顺序出现，异常与终态强制放行 | reducer 单测、组件测试、Mock E2E |
| 假流式 | `StreamingDraft` | 40 字/秒、25ms 步进；终态校正同一草稿（报告布局同样复用）；`prefers-reduced-motion` 直接展示完整文本 | 纯函数单测、组件测试 |
| 滚动跟随 | `ChatWorkspace` | 80px 阈值；用户主动上滑后停止自动跟随，回到底部恢复；发送、加载会话、创建会话和新增内容后跟随最新 | 纯函数单测；浏览器人工验收待执行 |
| 设置 | `SettingsDialog` | `/api/setting?topic=userAgreement|userManual` | API client/schema；真实内容验收待后续阶段 |

## SSE 夹具语义

`tests/fixtures/sse.ts` 只生成标准 SSE 文本流。单元测试覆盖 CRLF/LF、注释、空行、多行 `data`、UTF-8 分块、未知合法事件、`event` 与 `data.type` 不一致、非法 ID、坏 JSON、首连查询游标、重连 `Last-Event-ID` 和有界重连。未知合法事件只推进传输游标，不进入可见领域投影；协议坏包不推进游标并进入稳定错误路径。

## 当前阶段边界

- `npm run check` 是本目录的代码级门槛，Mock Playwright 只证明前端状态机与模拟后端的连接，不证明真实模型、worker、数据库或 Flask 部署。
- 应用入口收敛为 `/dashboard`、`/dashboard/session/<id>` 和 `/dashboard/settings`，资源前缀是 `/dashboard-assets/`；`CHAT_FRONTEND_ENTRY`、`/chat-next` 和 `/chat-assets/` 已退出运行时契约，回退需要恢复上一版本的代码和构建产物。
- 公开预览、内部登录面板和匿名预览上报代码已经删除，对应职责由官网前端承担；本工程不再包含登录与注册界面。旧版普通聊天静态文件也已从仓库删除。
- 地址路由使用 `src/runtime/navigation/app-route.ts` 的轻量路径解析，未引入 vue-router；真实浏览器认证、真实文件后端、真实模型/worker 和管理员端迁移仍不属于当前已验证范围；普通端文档系统同步记录在 `Document/`。
