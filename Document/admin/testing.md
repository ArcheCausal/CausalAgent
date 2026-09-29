# 管理员系统测试

文档职责：记录管理员后端、Vue 和部署静态契约的代码级专项验证；浏览器、数据库和隔离环境验收不属于本轮管理员前端设计系统统一交付。

适用范围：修改管理员 API、页面、migration 交界或隔离验收脚本时使用；全仓测试矩阵见 [`../development/testing.md`](../development/testing.md)。

后端测试的统一目录与执行顺序见 [`tests/README.md`](../../tests/README.md)。本轮管理员前端改造只执行 Python 静态契约、Vue 单元/类型检查和生产构建，不把 Mock E2E 或隔离主从 + PostgreSQL checkpoint E2E 作为页面样式交付门禁。

## 前端快速验证

```bash
cd admin-frontend
npm ci
npm run typecheck
npm run test:unit
npm run build
```
