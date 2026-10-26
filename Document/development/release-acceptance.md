# 发布验收清单

文档职责：给出部署包发布后在预发和生产服务器执行的检查步骤，并区分配置检查与实际运行验收。

适用范围：部署包已经下载、解压并由部署脚本启动，需要确认密钥、镜像、卷持久化、RAG 和数据库行为时使用；本地静态检查见 [`testing.md`](testing.md)，发布步骤见 [`deployment.md`](deployment.md)。

这些检查需要真实服务器、Docker Compose、宿主 Python 3.11 或更新版本、MySQL/PostgreSQL、模型服务和测试账号。单元测试或 Compose 配置解析不能替代真实验收。故障注入、容器停止重建和评测数据清理仅在具有独立 project 与数据卷的预发环境进行；生产检查现有运行状态及已保存的预发证据。

## 通用前提

普通配置、统一密钥文件和服务器数据目录应已准备好。`production.env` 只保存普通参数与密钥路径，`causalagent.env` 保存全部应用与数据库密钥及两个沙盒认证值；发布生成的 `images.env` 和 OpenSandbox 结构化配置 `config.toml` 仍需保留。以下命令在同一个 Bash 会话执行；预发将 Compose 文件改为 `docker-compose.staging.yml`，并使用独立的普通配置和数据卷。

`CAUSALAGENT_COMPOSE_FILE` 由部署、校验和回滚脚本读取；手工 Compose 命令仍需传 `-f`。以下共用函数显式选择同一文件。

`COMPOSE_PROJECT_NAME` 必须与部署时一致。不要在调用 shell 中保留旧的 `APP_IMAGE`、`STAGING_APP_IMAGE` 等镜像变量，避免覆盖 `images.env`。

`release_compose` 只是下方命令共用的 Bash 函数，不是部署包内的另一个脚本。

`release_dir` 使用真实发布目录，避免验收期间另一次发布切换 `current` 后检查到不同版本。

```bash
export CAUSALAGENT_CONFIG_FILE=/etc/causalagent/config/production.env
export CAUSALAGENT_SECRET_FILE=/etc/causalagent/secrets/causalagent.env
export CAUSALAGENT_COMPOSE_FILE=docker-compose.prod.yml
release_dir="$(readlink -f /opt/causalagent/releases/current)"
cd "$release_dir"
export COMPOSE_FILE="$release_dir/$CAUSALAGENT_COMPOSE_FILE"
test -f release.manifest.json
test -f images.env
release_compose() {
  docker compose --env-file "$CAUSALAGENT_CONFIG_FILE" \
    --env-file "$release_dir/images.env" \
    -f "$release_dir/$CAUSALAGENT_COMPOSE_FILE" "$@"
}
release_compose config --quiet
```

## 一 密钥文件与实际读取

1. 用 `stat -c '%a:%u:%g' "$CAUSALAGENT_SECRET_FILE"` 检查服务器文件，必须为 `640:0:3000`。检查 `release_compose exec -T app ls -l /run/secrets/causalagent.env`，并分别对 `worker`、`sandbox-controller`、`opensandbox` 检查同一路径。四个 Compose 入口均应使用 `causalagent_env`，容器内 `CAUSALAGENT_SECRET_FILE` 必须为 `/run/secrets/causalagent.env`，挂载必须只读；数据库、Grafana 和 SearXNG 等所选拓扑中的读取服务也必须可读。
2. 在 app 容器执行真实加载器，确认全部必需键和文件格式通过；只输出键名或判定，不打印值：

   ```bash
   release_compose exec -T app python -c 'from config.secret_file import REQUIRED_SECRET_KEYS, load_secrets; load_secrets(reload=True, required_keys=REQUIRED_SECRET_KEYS); print("secret file parsed and required keys verified")'
   ```

3. 检查 `release_compose config` 中的服务 `environment` 不直接包含密码、API key、服务令牌或签名密钥，也不再声明独立沙盒凭据来源或 token/key 文件路径。启动命令和健康检查中引用键名不代表包含密钥值，不能用搜索 `PASSWORD` 等字样代替这项检查。官方镜像适配入口会向自身进程导出所需变量；OpenSandbox 适配器只导出 `OPENSANDBOX_SERVER_API_KEY` 给官方 server，可选 LangSmith 也会导出 SDK 所需键。
4. 检查发布包没有真实密钥文件，应用镜像也没有预置 `causalagent.env`。镜像检查可使用不挂载 secret 的临时容器；将 `image` 替换为清单中的 app 引用：

   ```bash
   image="$(python3 -c 'import json; print(json.load(open("release.manifest.json"))["images"]["app"])')"
   docker run --rm --entrypoint sh "$image" -c 'find / -name causalagent.env 2>/dev/null'
   ```

   不应找到真实密钥文件。不得输出密钥正文。
5. 在隔离预发中，对一次性 app 容器指定不存在的路径并调用真实加载器：

   ```bash
   release_compose run --rm --no-deps \
     -e CAUSALAGENT_SECRET_FILE=/run/secrets/absent-causalagent.env \
     app python -c 'from config.secret_file import load_secrets; load_secrets()'
   ```

   必须以非零状态退出，并报告文件不存在；不要删除或改名服务器真实密钥文件来执行此检查。

## 二 清单、Compose 和运行中镜像

1. 执行 `bash scripts/verify.sh "$release_dir"`。它检查镜像锁与清单、Compose 实际镜像、沙盒文件摘要、统一密钥权限和格式、两个沙盒认证值及上游配置引用；不能证明服务健康、真实沙盒网络隔离或完整 Job 已通过。
2. 将 `release.manifest.json`、`images.lock.json` 与 `release_compose config` 中实际使用的镜像逐项比较。app、worker、sandbox-controller、monitor、cleanup、rag-eval-worker 和 db-bootstrap 对应清单的 `app`；`causal-mcp` 对应 `mcp`；生产 `mysql` 或预发 `mysql-primary` 对应 `mysql_primary`；`opensandbox` 对应 `opensandbox_server`。控制服务的处理镜像须等于 `sandbox_processing`，execd 与 egress 配置须等于各自清单引用。只比较所选拓扑实际使用的镜像，生产不会启动清单中的全部服务。
3. 配置输出不能证明运行中的容器已经更新。读取实际容器的镜像引用并与同一清单比较：

   ```bash
   for service in $(release_compose config --services); do
     for container_id in $(release_compose ps --all --quiet "$service"); do
       docker inspect --format '{{.Name}} {{.Config.Image}}' "$container_id"
     done
   done
   release_compose ps --all
   ```

   长期运行服务必须存在且健康；一次性 bootstrap 必须成功退出。若容器不存在或引用不同，不能判定本节通过。

## 三 服务器不挂载应用源码

检查 `release_compose config` 中 app、worker、sandbox-controller、causal-mcp、monitor、cleanup 和 rag-eval-worker 的挂载，只包含部署包配置、命名卷和服务器数据目录，不包含宿主仓库中的 `./Agent`、`./app` 或完整源码。OpenSandbox 的只读 `server_entrypoint.py` 是部署适配文件。确认服务器知识库与资产目录包含实际数据，不能仅检查目录存在。

## 四 命名卷持久化

在隔离预发中记录实际卷名和数据摘要，执行 `release_compose down`，再执行 `release_compose up -d`。不传 `-v`。确认数据库、checkpoint、正式 RAG、评测和模型卷仍在，数据与停止前一致。卷名应从当前 Compose project 的实际挂载取得，不能将逻辑卷名当成实际 Docker 卷名。生产仅做卷挂载和备份状态检查。

## 五 容器重建后的 active release

在 app、worker 和 rag-eval-worker 分别读取当前 active release，并保存结果：

```bash
for service in app worker rag-eval-worker; do
  release_compose exec -T "$service" python -c 'from Agent.knowledge_base.rag_runtime import RagRuntimeConfig; print(RagRuntimeConfig.from_environment().release_id)'
done
```

在隔离预发执行 `release_compose up -d --force-recreate app worker rag-eval-worker`，重复读取；三者 release ID 必须一致且与重建前相同。这只证明配置解析与指针一致，还需执行真实 RAG 查询，确认向量库能够打开且返回对应来源。

## 六 RAG 发布与应用镜像独立

在预发通过已授权的 RAG 发布接口发布通过评测门禁的新 release。比较发布前后实际 app 镜像引用，应保持不变；执行 `release_compose restart app worker rag-eval-worker`，确认新 active release 生效，并完成真实 RAG 查询。普通 `up -d` 在配置未变化时不保证重启服务。

## 七 RAG 回退与隔离

仅在有备份的隔离预发中验证故障回退：准备有效 active/fallback release，对 active release 进行受控故障注入，再重启读取索引的服务。检查恢复后的 active 指针、quarantine、worker readiness 和真实查询结果。保留恢复步骤及证据，不在生产破坏指针或索引。运行期回退不等于 `rollback.sh` 回滚应用发布。

## 八 评测与正式存储隔离

在隔离预发中核对评测与正式索引使用不同实际卷。对可丢弃评测数据执行既有受控清理流程，确认正式卷的 manifest、active 指针与索引内容未变化，真实 RAG 查询仍可用。不得为此删除共享卷或生产数据。

## 九 数据库、MCP、SSE 与 worker

按 [`testing.md`](testing.md) 分别验证所选拓扑：预发包括 MySQL 主从；当前生产 Compose 只有单 MySQL，没有副本、SearXNG 或开发观测服务。分别检查数据库 readiness、bootstrap 与 PostgreSQL checkpoint setup、causal-mcp `/ready`、SSE Last-Event-ID 重放、worker lease/fencing 恢复及真实模型调用。外部搜索仅在另行部署并启用时验证。

## 十 沙盒与研究参数

先核对 Worker、控制服务和 OpenSandbox 管理服务均通过 `causalagent_env` 只读挂载同一份完整密钥文件，容器内路径统一为 `/run/secrets/causalagent.env`。这三个服务共享应用信任范围，不提供服务之间的密钥隔离。确认 `CAUSALAGENT_SANDBOX_CONTROLLER_TOKEN` 与 `CAUSALAGENT_SANDBOX_API_KEY` 均为 32 至 4096 个无空白字符且值不同，只输出判定，不输出值。通过各自真实启动入口确认 Worker 使用控制 token、controller 使用数据库密码和两个沙盒认证值、OpenSandbox 适配器只导出上游 API key；controller 不应依赖模型配置。

检查实际创建的用户脚本沙盒容器，确认没有挂载统一密钥文件，环境中也没有应用、数据库或沙盒服务密钥；不要打印完整容器环境或密钥正文。Worker 仍不加入上游网络，也不挂 Docker socket。本轮保留 Alloy 已有日志采集权限。

在独立预发中，通过真实 Worker 客户端验证申请、输入 hash、预览、非 root 执行、原始文件只读、受限日志、产物 hash、销毁与 MySQL 关闭记录。分别保存 IPv4/IPv6 实测和实际 egress/bridge/转发配置证据。健康接口不能代替执行及文件代理验证。

故障场景包括最后一个名额并发竞争、创建响应丢失、执行响应丢失、销毁失败、Worker 撤销或删除、控制服务重启，以及命名锁丢失。确认未知操作不重跑，未确认资源结束前不释放占用；只因时间经过而减少占用应判定失败。未知创建且无法确认远端不存在时，记录可以继续占用，需保留并人工核查，不能直接改库释放。

研究参数必须覆盖两次澄清后重启恢复、方案确认自由文本修订、校验失败重入，以及最终上下文写入成功但父 checkpoint 尚未提交时重放。核对修订来源、版本、候选身份和历史结果上下文，再完成真实上传至报告的全流程。分层入口见 [数据处理验证](testing.md#opensandbox-数据处理验证)。

## 判定

每项检查必须有对应的命令输出或运行证据。清单、部署脚本或静态检查成功都不能代替服务器验收；任一必需项未执行或失败时，不得宣告该发布通过验收。
