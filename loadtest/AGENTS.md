# loadtest AGENTS.md

生效目录：loadtest/ 及其未被更近 AGENTS.md 覆盖的子目录。

职责：约束压测客户端的目录用途、凭据与产物落盘位置、入库边界，以及报告归档与清理方式。

## 目录布局

压测客户端分两类内容：入库的客户端程序与文档，以及不入库的本地产物。全部本地产物收在 `local/` 一个目录下，方便整目录汇总、清理和排除。

```text
loadtest/
├── runner/            # 压测程序：HTTP/SSE 客户端、账号、场景、指标、日志核查、报告与证据合并
├── ops/               # 一档或多档的编排脚本：服务器采样 + 跑负载 + 回写摘要
 ├── selftest.py        # 本地桩服务上的真实 HTTP 自检，不接触 staging
├── verify_report.py   # 用已有产物验证报告与逐 Job 比对能消费服务器采集格式
 ├── *.sh               # 在服务器上执行的只读脚本：单次采集、窗口采样、逐 Job 主库核对
├── evidence/
│   └── index.md       # 唯一入库的压测证据链索引
├── local/             # 全部本地产物，整体加入 .gitignore
│   ├── runs/          # 每次运行的原始产物：逐档 summary.json、控制台输出、编排日志
 │   ├── runs/job-ids/  # 按档位导出的本轮 job_id 清单，供服务器逐 Job 核对
│   ├── data/          # 运行时生成的测试 CSV
│   ├── evidence/      # 整理后的报告、逐档 logcheck、缺陷分析与服务器侧采集结果
│   ├── accounts.local.json      # 测试账号与密码
 │   ├── server-side-<run_id>-before.json  # 负载前的服务器只读采集结果
 │   ├── server-side-<run_id>-after.json   # 停止提交后的服务器只读采集结果
 │   └── job-db-<run_id>.json              # 逐 Job 主库终态只读核对结果
├── README.md          # 使用说明
└── requirements.txt   # 本地压测客户端依赖，不进入服务器镜像
```

## 服务器只读脚本

三个脚本都在服务器上执行，只读、按 run_id 分轮保存，并输出带 schema_version 的 JSON。缺失数据必须写成 missing/error 状态，禁止用 0 冒充采集成功，也不输出密钥、Prompt、模型正文或用户文件内容。

| 脚本 | 采集内容 |
| --- | --- |
| server_side_collect.sh | 单次快照：主机 CPU、内存、swap、磁盘与 inode、容器重启/OOM、镜像版本、MySQL 状态与复制、Job 分布、出网探测 |
| stage_resource_sample.sh | 覆盖负载与排空阶段的定时采样：主机负载与内存、容器资源、数据库连接数、queued/running/draining 数量 |
| job_db_check.sh | 按本轮 job_id 清单逐 Job 核对主库终态、会话归属、创建/开始/结束时间、attempt 与执行释放状态 |

三个脚本的认证信息都从受控凭据文件读取，默认 /opt/causalagent-staging/.env.staging，可用 CAUSALAGENT_ENV_FILE 覆盖；容器名默认按 COMPOSE_PROJECT_NAME 推导，默认 causalagent-staging。没有采集或核对成功时，报告必须把对应项写成未采集，不得标记为通过。

stage_resource_sample.sh 的 samples 数组每一项包含 host、containers、queue、connections 四层；runner/resource.py 同时兼容早期的扁平采样格式。为避免拖长采样间隔，采样循环不逐容器调用 docker inspect，OOMKilled 结论由 server_side_collect.sh 的单次快照给出。

## 凭据与产物

- 账号密码只写入 `local/accounts.local.json`；不得出现在报告、日志、索引或提交中。
- 原始产物写入 `local/runs/` 与 `local/data/`；整理后的报告与核查写入 `local/evidence/`。
- 三类内容都只在本地保留，用于重跑、排查与归档到团队知识库。

## 入库边界

- 进入版本库的压测内容只有客户端程序、脚本、README、AGENTS.md、requirements.txt 与 `evidence/index.md`。
- 本地产物已经集中到 `local/`，`.gitignore` 只需要一条 `/loadtest/local/` 规则即可整体排除，不再逐目录或逐文件名添加规则。
- 新增本地产物时放进 `local/` 即已被覆盖，不需要改 `.gitignore`；只有当新增内容需要入库时，才在 `evidence/` 下登记并在本文件说明用途。
- 产物文件名沿用如下命名：逐档报告 `<档位>-report.md`、日志核查 `logcheck-<档位>.json`、合并报告 `loadtest-report-<date>.md`、服务器侧采集 `server-side-<date>.json`、主库核对 `job-db-<date>.json`。

## 归档与清理

- 每次输出报告后立即归档，并把 run_id、测试时间、被测镜像 digest、归档位置与结论追加到 `evidence/index.md`；索引是跨轮次累积的持久记录。
- 清理本轮产物时直接对 `local/` 整目录操作：`local/runs/` 与 `local/data/` 可在确认不再需要重跑时清空，`local/evidence/` 下本轮报告与 logcheck 可删。
- `evidence/index.md` 不在 `local/` 内，清理 `local/` 不会影响索引。
- 索引里“归档位置”为“待归档”时不得删除对应报告；先补上归档链接再清理。

## 与其他目录的边界

- `loadtest/` 不属于 `tests/`，不进入默认 pytest 与 CI 执行范围；测试与压测的分工见 `tests/AGENTS.md`。
- 压测客户端只在本地运行，不占用服务器 CPU 与内存；服务器侧指标必须通过只读采集脚本取得，不以客户端观测代替。
- 需要把某项压测结论固化为回归检查时，另写 `tests/integration/` 或 `tests/e2e/` 测试，不复用压测脚本。
