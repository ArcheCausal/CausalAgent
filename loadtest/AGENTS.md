# loadtest AGENTS.md

文档职责：约束压测客户端的目录用途、凭据与产物位置、证据判定、入库与归档方式。

适用范围：loadtest/ 及其未被更近 AGENTS.md 覆盖的子目录；技术口径见 [README.md](README.md)，真实接口契约见 [Job API](../Document/api/agent-jobs.md) 和[会话与文件 API](../Document/api/chat-files.md)。

## 目录布局

压测客户端分两类内容：入库的客户端程序与文档，以及不入库的本地产物。全部本地产物收在 `local/` 一个目录下，方便整目录汇总、清理和排除。

```text
loadtest/
├── runner/            # 压测程序：HTTP/SSE 客户端、账号、场景、指标、日志核查、报告与证据合并
├── ops/               # 一档或多档的编排脚本：服务器采样 + 跑负载 + 回写摘要
├── selftest.py        # 接口桩工具；结果不作为真实系统验收证据
├── verify_report.py   # 只读取真实运行摘要和同轮真实采集，检查报告兼容性
├── *.sh              # 在服务器上执行的只读脚本：单次采集、窗口采样、逐 Job 主库核对
├── evidence/
│   └── index.md       # 唯一入库的压测证据链索引
├── local/             # 全部本地产物，整体加入 .gitignore
│   ├── runs/          # 原始产物根目录，按运行日期分组
│   │   └── <date>/    # 同一天的全部运行：<run_id>/ 与编排、驱动日志
│   │       ├── <run_id>/  # 逐档 summary.json、jobs.jsonl、控制台输出与服务器采集
│   │       └── job-ids/   # 按档位导出的本轮 job_id 清单，供服务器逐 Job 核对
│   ├── data/          # 运行时生成的测试 CSV
│   ├── evidence/      # 整理后的产物根目录，按运行日期分组
│   │   └── <date>/    # 同一天的合并报告、逐档报告、logcheck 与缺陷分析
│   ├── accounts.local.json      # 测试账号与密码
│   ├── server-side-<run_id>-before.json  # 手工取回的负载前采集（历史路径）
│   ├── server-side-<run_id>-after.json   # 手工取回的停止后采集（历史路径）
│   └── job-db-<run_id>.json              # 手工取回的主库核对（历史路径）
├── README.md          # 使用说明
└── requirements.txt   # 本地压测客户端依赖，不进入服务器镜像
```

日期目录取 run_id 的 YYYYMMDD 前缀，没有合法前缀时取运行开始时刻的本地日期。同一天的全部档位与补跑共用同一个日期目录；跨午夜的运行整体归入起始日期。运行摘要、报告、logcheck 与服务器采集必须落在同一天的两个目录下，跨日期合并直接报错。

## 服务器只读脚本

三个 shell 入口都在服务器上执行，只读、按 run_id 分轮保存，并输出带 schema_version 的 JSON。job_db_check.sh 与 stage_resource_sample.sh 依赖相同相对路径下的 ops/server_evidence.py，必须一起部署，不能只上传 shell 文件。缺失数据必须写成 missing/error 状态，禁止用 0 冒充采集成功，也不输出密钥、Prompt、模型正文或用户文件内容。

| 脚本 | 采集内容 |
| --- | --- |
| server_side_collect.sh | 单次快照：主机 CPU、内存、swap、磁盘与 inode、容器重启/OOM、镜像版本、MySQL 状态与复制、Job 分布、出网探测 |
| stage_resource_sample.sh | 覆盖负载与排空阶段的定时采样：主机负载与内存、容器资源、数据库连接数、queued/running/draining 数量 |
| job_db_check.sh | 按同轮请求身份 JSON 或旧 Job ID 清单核对主库，包含未知接纳、首次开始、事件集合与 CDFM 调用证据 |

三个脚本只从部署配置读取数据库账号和库名，默认 /opt/causalagent-staging/.env.staging，可用 CAUSALAGENT_ENV_FILE 覆盖；数据库密码必须由容器内的 /opt/causalagent/secret-env.sh 从统一密钥文件读取，不得放入宿主命令参数。容器名默认按 COMPOSE_PROJECT_NAME 推导，默认 causalagent-staging。没有采集或核对成功时，报告必须把对应项写成未采集，不得标记为通过。

采样器只启动一次，并保存实际采样进程 PID；确认启动时必须核验该 PID 的精确命令行与本轮 run_id，禁止仅用 pgrep 文本命中认定启动。读取采样退出码与 JSON 的传输错误需要在有界窗口内重试；窗口耗尽后保留 unknown，并继续独立的收尾快照与主库核对。

stage_resource_sample.sh 的 samples 数组每一项包含 host、containers、queue、connections 四层；runner/resource.py 同时兼容早期的扁平采样格式。为避免拖长采样间隔，采样循环不逐容器调用 docker inspect，OOMKilled 结论由 server_side_collect.sh 的单次快照给出。

## 凭据与产物

- 账号密码只写入 `local/accounts.local.json`；不得出现在报告、日志、索引或提交中。
- 原始产物写入 `local/runs/<date>/` 与 `local/data/`；整理后的报告与核查写入 `local/evidence/<date>/`。
- 三类内容都只在本地保留，用于重跑、排查与归档到团队知识库。

## 入库边界

- 进入版本库的压测内容只有客户端程序、脚本、README、AGENTS.md、requirements.txt 与 `evidence/index.md`。
- 本地产物已经集中到 `local/`，`.gitignore` 只需要一条 `/loadtest/local/` 规则即可整体排除，不再逐目录或逐文件名添加规则。
- 新增本地产物时放进 `local/` 即已被覆盖，不需要改 `.gitignore`；只有当新增内容需要入库时，才在 `evidence/` 下登记并在本文件说明用途。
- 产物文件名沿用如下命名：逐次运行报告 `<run_id>-report.md`、日志核查 `logcheck-<档位>.json`、合并报告 `loadtest-report-<date>.md`、服务器侧采集 `server-side-<date>.json`、主库核对 `job-db-<date>.json`；客户端未观测到终态的那些 Job 的补充核对用 `job-db-<date>-extras.json`，由报告单独成节，不并入逐档比对的一致条数。
- 编排脚本在负载开始前固定 run_id，三个 attach 命令都带 `--stage`；在跑多个档位时不要省略该参数，否则同一份采集会被写进每一档的运行摘要。附加、日志与整理命令按 run_id 定位日期目录，未指定时取最近一个有运行的日期目录。

## 归档与清理

- 每次输出报告后立即归档，并把 run_id、测试时间、被测镜像 digest、归档位置与结论追加到 `evidence/index.md`；索引是跨轮次累积的持久记录。旧版平铺产物用 `python -m runner migrate-artifacts` 预览，确认后再加 `--apply` 归入日期目录。
- `evidence/index.md` 的归档位置写成 `local/evidence/<date>/<文件>`，与日期目录一致。
- 清理本轮产物必须先归档并由用户自行删除；代理不得删除真实运行摘要、JSONL、数据、报告或其他重要产物。
- `evidence/index.md` 不在 `local/` 内，清理 `local/` 不会影响索引。
- 索引里“归档位置”为“待归档”时不得删除对应报告；先补上归档链接再清理。

## 报告 v2 与真实证据约束

- 沙盒档位必须按 `interaction_type` 答复：研究语义问题使用场景声明的研究语义，方案确认使用当前事件中的 `plan_version`，未识别类型只记录缺口。禁止用“默认继续”或固定等待时间代替真实答复；方案超出场景允许范围时记录偏离，不自动确认。
- 沙盒创建数、执行状态与清理必须来自本轮 Job 对应的 `analysis_sandbox_sessions`；Job 数不能代替沙盒会话数。动态容器按 `application=causalagent` 与 `request` 标签归属，没有本轮映射的容器单列为其他请求或未归属，不按名称猜测。
- 排空结论分业务结束、执行释放、沙盒释放与完整排空四层；完整排空缺少身份核对、终态、执行释放、全部会话关闭或远端结束证据时保持未知，不沿用旧结论。
- 阶段计时只记录固定阶段名与时间，不记录脚本、文件正文或凭据；Worker 本地与全局容量等待在没有独立计时事件时必须标为未采集。

- 修改报告前核对 `metrics.py`、`stages.py` 与 `capacity.py` 的当前字段；渲染可调用纯函数 `enrich_summary`，不得执行网络或数据库请求来补齐结论。
- 新摘要使用 `loadtest.run.v2`，保留全量逻辑请求、逐 Job 样本、真实时间点与事件 ID 集合。创建前保存 `jobs.jsonl`；创建结果未知时保留账号、会话、原幂等键和 payload 变体指纹，按同键有界重试，不记录正文。拒绝复用已有产物的 run_id。
- 必须分别报告接纳分母、主库成功率与覆盖率、120/300 秒完成率和未知数、实时/事后终态、SSE 集合核对覆盖以及分组延迟。仅完成请求的分位必须标为“仅完成样本 p95”；未完成等待另给下界。
- 负载和排空阶段分别保留 HTTP、客户端完成及主库完成吞吐。300 秒排空检查点与最终观察结果分别报告；主库事后结果不得覆盖实时终态时间。业务活动清空和执行释放分别判定，客户端待观察集合为空不能证明服务器执行释放；主库覆盖不足时保留 unknown。
- SSE 峰值以成功 HTTP 200 响应头为准；必须保留建连尝试、连接状态和全部错误次数/类型，不能因重连成功而清零。流读取必须有空闲和总期限，不能让心跳无限延长订阅。
- 兼容旧产物时，缺逻辑请求时间点、首次开始标记或完整事件集合的项目必须保留 null/未采集，不能补零。旧主库创建/结束时间只能用于有证据的服务器总耗时，不能推算首次排队。
- CDFM 固定量场景必须记录规模、seed 和任务量；到达率场景必须独立调度并记录延迟和未发送请求。没有本轮 Job 对应的 invocation/attempt/retry 证据时，不得由场景名称或 Job 成功推断 CDFM 通过。
- 请求归属必须来自本轮身份、原幂等键或真实 Job 清单，不得按时间窗口补认。日志须按本轮 Job 过滤并保留覆盖与未知状态，不使用全局累计计算本轮率。整份 logcheck 必须 run_id 与摘要一致且 scope=run 才能用于本轮 MCP 错误、成功数量与 CDFM；同 stage 的旧日志不能替代。
- 取消业务状态、执行释放、算法进程终止与真实前端阶段必须独立判定。取消 API 成功不得替代主库/worker 释放、PID 终止或浏览器证据；未触发分支和未验证项必须单列。
- 真实证据验证禁止执行 `selftest.py` 或使用任何 mock/fake。报告检查只消费真实运行 summary 与同 run_id 的真实服务器/主库产物；不得构造合成运行。语法检查、历史产物渲染与新版本真实联调必须分别说明。

## 与其他目录的边界

- `loadtest/` 不属于 `tests/`，不进入默认 pytest 与 CI 执行范围；测试与压测的分工见 `tests/AGENTS.md`。
- 压测客户端只在本地运行，不占用服务器 CPU 与内存；服务器侧指标必须通过只读采集脚本取得，不以客户端观测代替。
- 需要把某项压测结论固化为回归检查时，另写 `tests/integration/` 或 `tests/e2e/` 测试，不复用压测脚本。
