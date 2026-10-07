# 文档维护规则

文档职责：定义 `AGENTS.md`、`Document/`、日志和遗留政策文档的职责边界，以及本仓库文档重构的维护约束。

适用范围：新增、重构或核对项目文档时使用；它不替代根目录和局部 `AGENTS.md` 的执行规则。

## 两类文档边界

| 文件 | 负责回答的问题 | 内容要求 |
| --- | --- | --- |
| `AGENTS.md` | 修改某模块时必须怎么做 | 使用“必须、禁止、修改后核对”等可执行约束；根文件负责全局规则，局部文件只补充目录风险 |
| `Document/` | 系统当前是什么、如何工作、为什么这样设计 | 记录可由当前代码、迁移、配置、Compose 和测试核验的技术事实，每项事实只设一个权威归属 |

根和局部 `AGENTS.md` 顶部必须声明生效目录和负责约束的修改类型。每个新建或重构的 `Document/` 文档都必须在标题后立即声明“文档职责”和“适用范围”。

## 主题归属

- 系统边界、进程和数据流归 `architecture/overview.md`。
- Agent、worker、LangGraph、MCP、RAG 和事件脱敏归 `architecture/agent-runtime.md`。
- Session、Job、文件冻结和 checkpoint 生命周期归 `architecture/job-file-lifecycle.md`。
- 普通用户 HTTP/SSE 契约归 `api/`；管理员完整接口归 `admin/api.md`。
- MySQL/PostgreSQL、主从、迁移、checkpoint、cleanup 和 monitor 内部机制归 `database/`。
- 启动、测试、日志系统和构建部署归 `development/`；文档维护规则由本文件统一维护。
- 管理员页面、后端授权和管理员 API 如何消费共享能力归 `admin/`，不复制数据库 worker 内部实现。
- 前端视觉方向、设计 token、共享组件契约和四个前端的样式现状归 `design-system/`；各前端的路由、API 契约和部署事实仍归 `development/`、`admin/` 和各自的局部 `AGENTS.md`。

`Document/operations/` 不创建。原计划中的部署、监控和运行维护内容分别归 `development/deployment.md`、`database/monitoring.md` 和 `admin/`。

## 维护流程

修改路由、表结构、Compose 服务、配置默认值或测试入口后，必须搜索其文档引用并更新唯一权威页面；跨主题页面只链接和说明消费关系，不复制完整实现。新文档中的相对链接必须指向现存文件；命令必须能从当前仓库结构推导，不能照搬历史日志。

根 `README.md` 是项目入口；只有要求入口、部署或运行命令变化时才同步修改，其他文档重构不改根 README。`CHANGELOG.md` 是由原 `README/开发日志.md` 纯重命名得到的历史日志，正文只允许在末尾追加新记录，不修改历史段落。

`setting/Userprivacy.md` 是隐私政策，属于独立政策材料而非架构事实库；其安全边界与 `Document/api/`、`Document/admin/` 的事实必须保持一致。

## 提交前静态检查

至少检查：

```powershell
git diff --check
git diff -- README.md
git diff --find-renames --summary
```

确认每个 `Document/` Markdown 文件都有顶部职责声明。
