# sandbox_controller/AGENTS.md

生效目录：`sandbox_controller/` 及其子目录。

负责约束的修改类型：内部鉴权、受限沙盒 API、容量准入、生命周期与 OpenSandbox 适配。

- 修改前阅读 [控制服务契约](../Document/architecture/sandbox-controller.md)、[Job 生命周期](../Document/architecture/job-file-lifecycle.md)、[数据库约束](../Database/AGENTS.md) 和 [测试入口](../Document/development/testing.md)。
- 必须同时检查 Worker 客户端、真实 Job fencing、生命周期迁移和全部部署入口；控制服务不能依赖应用模型配置。
- 只能开放固定输入、脚本、预览、执行和输出操作，禁止开放宿主命令或通过 API 返回管理凭据。
- Worker、控制服务和 OpenSandbox 管理服务共享只读 `causalagent_env`，容器路径必须统一为 `CAUSALAGENT_SECRET_FILE=/run/secrets/causalagent.env`；不得重新引入独立凭据来源或旧文件读取兼容逻辑。执行用户脚本的临时沙盒容器禁止挂载密钥文件或接收应用、数据库及沙盒服务密钥。
- Python 组件必须通过 `config/secret_file.py` 读取各自必需的键；控制服务读取同一文件中的数据库密码，不得依赖模型配置。两个沙盒认证值必须不同，均为 32 至 4096 个无空白字符；OpenSandbox 启动适配器只向官方 server 导出 `OPENSANDBOX_SERVER_API_KEY`。
- 容量必须统计全部非 closed 状态；创建或执行结果未知时禁止重复操作，关闭前必须明确确认远端结束。
- 远端 API 调用不得留在数据库长事务中；状态回执必须核对版本和操作身份，心跳不能覆盖执行状态。
- 修改单实例边界、清理或网络策略必须使用真实数据库和远端组件验证；缺少依赖时明确列出未验收场景，不得使用替代实现。
- 不把脚本、文件、凭据、上游异常正文或连接参数写入日志；公开错误只返回稳定代码。
