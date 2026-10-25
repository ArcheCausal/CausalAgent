# sandbox_controller/AGENTS.md

生效目录：`sandbox_controller/` 及其子目录。

负责约束的修改类型：内部鉴权、受限沙盒 API、容量准入、生命周期与 OpenSandbox 适配。

- 修改前阅读 [控制服务契约](../Document/architecture/sandbox-controller.md)、[Job 生命周期](../Document/architecture/job-file-lifecycle.md)、[数据库约束](../Database/AGENTS.md) 和 [测试入口](../Document/development/testing.md)。
- 必须同时检查 Worker 客户端、真实 Job fencing、生命周期迁移和全部部署入口；控制服务不能依赖应用模型配置。
- 只能开放固定输入、脚本、预览、执行和输出操作，禁止开放宿主命令或向 Worker 交付上游管理凭据。
- 容量必须统计全部非 closed 状态；创建或执行结果未知时禁止重复操作，关闭前必须明确确认远端结束。
- 远端 API 调用不得留在数据库长事务中；状态回执必须核对版本和操作身份，心跳不能覆盖执行状态。
- 修改单实例边界、清理或网络策略必须使用真实数据库和远端组件验证；缺少依赖时明确列出未验收场景，不得使用替代实现。
- 不把脚本、文件、凭据、上游异常正文或连接参数写入日志；公开错误只返回稳定代码。
