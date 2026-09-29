# 前端设计系统

文档职责：记录 CausalAgent 四个前端共用的视觉决策、设计 token、共享组件契约和迁移状态，作为前端视觉统一的唯一权威入口。

适用范围：`packages/design-system/`、`website-frontend/`、`chat-frontend/`、`admin-frontend/` 和 `app/rag_eval/frontend/` 的视觉与共享组件工作；不覆盖各前端的业务逻辑、路由、API 契约、数据库或部署事实。

## 文档导航

- [`design-decision.md`](design-decision.md)：视觉方向的来源、必须统一的规则、允许各端保留的差异，以及对比度和动效边界。
- [`tokens.md`](tokens.md)：token 的命名、数值、与原型变量的对应关系和使用规则。
- [`components.md`](components.md)：共享组件的清单、属性、允许的变体、必须覆盖的状态和使用场景。
- [`current-state.md`](current-state.md)：四个前端的样式现状、接入状态、重复实现和迁移顺序。

## 代码位置

共享设计系统的源码是 `packages/design-system/`，它在仓库里以源码形式被各前端引入，不单独发布。组件的样式和规则写在 `packages/design-system/src/`，本目录不重复实现细节。

## 归属原则

- 视觉方向、颜色对比度、动效边界和“哪些差异必须保留”的决策只在 [`design-decision.md`](design-decision.md) 维护。
- token 的名称、数值、原型变量映射和使用规则只在 [`tokens.md`](tokens.md) 维护。
- 组件的属性、变体、状态要求和使用场景只在 [`components.md`](components.md) 维护。
- 四个前端的样式现状、重复实现和迁移顺序只在 [`current-state.md`](current-state.md) 维护。
- 各前端的业务页面结构、路由、API 契约、构建和部署事实仍归 `Document/development/`、`Document/admin/` 和各自的局部 `AGENTS.md`。

文档与代码冲突时以当前代码为准，并先修正文档，再报告实现偏移。
