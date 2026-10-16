# staging 压测客户端

文档职责：记录对 staging 入口执行真实 HTTP/SSE 压测的本地客户端、账号边界、分级负载与报告产物。

适用范围：在本地电脑对一个 staging 入口执行分级负载、取消与恢复专项、稳定性观察时使用；服务端接口契约见 Document/api/，部署事实见 Document/development/deployment.md。

## 边界

- 客户端只在本地电脑运行，不占用服务器 CPU 与内存；服务器侧指标需要单独的只读采集入口。
- 只使用测试账号与测试文件。明文 HTTP 入口下不允许出现真实用户密码或真实业务文件。
- 账号密码只写入 loadtest/local/accounts.local.json，不进入报告、日志或版本库。
- 运行产物写入 loadtest/local/runs/；整理后的报告与逐档核查写入 loadtest/local/evidence/，默认归档到本地和团队知识库，不进入版本库。
- 进入版本库的压测证据只有 loadtest/evidence/index.md 一份索引表；报告正文、logcheck JSON、缺陷分析和服务器侧采集结果都不提交。
- 每次输出报告后立即归档，并把 run_id、测试时间、被测镜像 digest、归档位置和结论追加到 index.md。

## 安装

~~~
cd loadtest
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

## 常用命令

~~~
# 第一阶段：网络与入口预检
.venv\Scripts\python.exe -m runner preflight

# 第二阶段：创建测试账号并记录本地凭据（新档位最多 30 个账号）
.venv\Scripts\python.exe -m runner accounts --count 30

# 第四阶段：按档位执行负载
.venv\Scripts\python.exe -m runner stage --stage baseline
.venv\Scripts\python.exe -m runner stage --stage low
.venv\Scripts\python.exe -m runner stage --stage burst_ungated
.venv\Scripts\python.exe -m runner stage --stage queue_pressure
.venv\Scripts\python.exe -m runner stage --stage multi_session_smoke
.venv\Scripts\python.exe -m runner stage --stage multi_session_pressure

# 第五阶段：取消与 SSE 恢复专项
.venv\Scripts\python.exe -m runner recovery

# 导出本轮 job_id 清单，供服务器上逐 Job 主库核对
.venv\Scripts\python.exe -m runner export-job-ids

# 整理证据包与核查定位清单
.venv\Scripts\python.exe -m runner evidence
.venv\Scripts\python.exe -m runner verify-packet
~~~

## 档位表

档位定义在 runner/config.py 的 STAGES。字段含义：users 是逻辑账号数，sessions_per_user 是
每个账号同时持有的真实会话数，active_jobs 是客户端创建 Job 的并发上限（None 表示不限），
sse 是客户端 SSE 订阅并发上限，create_budget 是每个账号累计成功创建的唯一新 Job 上限。

| 档位 | 账号 × 会话 | active_jobs | sse | 时长 | 每账号创建上限 |
| --- | --- | --- | --- | --- | --- |
| smoke / baseline | 1 × 1 | 1 | 1 | flows 1 / 4 | 不限 |
| low | 2 × 1 | 1 | 2 | 300 s | 不限 |
| mid_low | 5 × 1 | 2 | 4 | 600 s | 不限 |
| mid / stability | 10 × 1 | 4 | 8 | 600 s / 1800 s | 不限 |
| burst | 20 × 1 | 4 | 8 | 600 s | 不限 |
| burst_ungated | 20 × 1 | 不限 | 20 | 600 s | 不限 |
| queue_pressure | 30 × 1 | 不限 | 30 | 300 s | 20 |
| multi_session_smoke | 1 × 2 | 2 | 2 | 180 s | 4 |
| multi_session_pressure | 10 × 2 | 20 | 20 | 600 s | 不限 |

同一账号的不同会话允许同时运行任务，同一会话仍遵守应用现有的活动 Job 约束。并发数量由
服务器默认 worker 能力形成，客户端不提前限流；burst_ungated 与 queue_pressure 的 active_jobs
为“不限”正是为了观察服务器自己的排队行为。

## 自检

selftest.py 用一个本地桩服务做真实 HTTP 自检，覆盖多会话并发、会话级活动 Job 约束、
账号级创建额度、active_jobs=None 不阻塞创建，以及排队超过观测窗口不算 SSE 断线：

~~~
.venv\Scripts\python.exe loadtest/selftest.py
~~~

桩服务只承载接口形状与并发约束，不是被测系统，也不能替代 staging 真实运行。

## 服务器侧指标

本客户端只在本地运行，不读取服务器资源。需要服务器侧 CPU、内存、磁盘、swap、
OOM 与容器重启、MySQL 连接数和复制状态时，在服务器上执行只读采集并把结果带回：

~~~
sudo bash loadtest/server_side_collect.sh <run_id> before > /tmp/server-before-<run_id>.json
scp ubuntu@<host>:/tmp/server-before-<run_id>.json loadtest/local/server-side-<run_id>-before.json
~~~

采集脚本只读取状态，不修改服务、数据或配置，也不输出任何密钥。它在服务器上通过
bash -n 与 JSON 解析校验后，把结果写入各档运行摘要，再由 evidence 命令渲染进
“服务器侧”一节：

~~~
.venv\Scripts\python.exe -m runner attach-server --from loadtest/local/server-side-<run_id>-before.json
.venv\Scripts\python.exe -m runner attach-after --from loadtest/local/server-side-<run_id>-after.json
.venv\Scripts\python.exe -m runner evidence
~~~

采集结果带 schema_version 与 run_id，缺失数据写成 missing/error 状态而不写成 0。没有执行
attach-server 时，报告把服务器侧各项如实写成“未采集”。

## 负载窗口资源采样

服务器侧的单次快照覆盖不到整个负载窗口，因此每档负载都配一份主机资源采样。
采样脚本只读 /proc、docker stats 与 SHOW GLOBAL STATUS，按固定间隔把主机 CPU jiffies、
负载均值、内存与 swap 水位、容器资源、容器重启与 OOM、数据库连接数和 queued/running/
draining 数量写入 JSON。采样时长要大于负载时长，因为停止提交后 Job 还会继续执行一段时间。

~~~
scp -i <key> loadtest/stage_resource_sample.sh ubuntu@<host>:/tmp/_lt/stage_resource_sample.sh
ssh ubuntu@<host> "bash /tmp/_lt/stage_resource_sample.sh <run_id> 690 10 mid > /tmp/_sr_mid.json"
scp ubuntu@<host>:/tmp/_sr_mid.json loadtest/local/runs/stage-sample-mid.json
.venv\Scripts\python.exe -m runner attach-sample --stage mid --from loadtest/local/runs/stage-sample-mid.json
~~~

采样进程在收尾前不会写出 finished_at 字段，因此必须在进程结束后再取回文件，否则
JSON 会被截断；同一个档位也不要同时启动两个采样进程写同一个文件。取回后写入对应
档位的运行摘要，再由 evidence 命令渲染进“负载窗口资源曲线”一节。

## 停止后快照

单次采集区分两个时刻：负载开始前作为基线，全部负载结束后作为收尾。收尾采集用
attach-after 写入各档摘要，报告会把两份采集分列并标注采集时刻：

~~~
.venv\Scripts\python.exe -m runner attach-server --from loadtest/local/server-side-<run_id>-before.json
.venv\Scripts\python.exe -m runner attach-after --from loadtest/local/server-side-<run_id>-after.json
.venv\Scripts\python.exe -m runner evidence
~~~

报告据此给出“负载前 Job 分布”与“停止后 Job 分布”，可以直接看到队列在停止提交后
是否清空。

## 一键执行一档（负载 + 采样）

ops/ 下的两个脚本把“启动采样、跑负载、等采样收尾、取回并写入摘要”串成一档：

~~~
# 跑一个档位（先把三个只读脚本上传到服务器上的 /tmp/_lt/）
.venv\Scripts\python.exe ops/stage_sampling.py mid

# 按顺序跑多个档位；不传参数时跑 low 起的全部档位，任一档失败即停止后续档位
.venv\Scripts\python.exe ops/run_stages.py low mid_low mid stability recovery burst burst_ungated queue_pressure multi_session_smoke multi_session_pressure
~~~

每档除了负载命令，还固定执行：负载前的服务器单次采集、负载窗口资源采样、停止提交后的
服务器单次采集、以及逐 Job 主库核对。采样秒数在 ops/stage_sampling.py 的 SPECS 中配置，
必须大于负载持续时间。逐档日志写入 local/runs/_orch.log 与 local/runs/_driver.log。
服务器的采集或核对失败、负载进程失败都会让该档返回非零，driver 随即停止后续档位，
因此上一档未排空或核对失败不会污染下一档。

## 主库 Job 终态核对

客户端只记录自己发起的请求；为了独立确认 Job 的最终状态，另有只读脚本直接查询
staging 主库，按本轮真实 job_id 清单逐 Job 核对终态、会话归属、创建/开始/结束时间和
执行释放状态，并输出全库分布与失败明细：

~~~
# 先导出本轮 job_id 清单
.venv\Scripts\python.exe -m runner export-job-ids

# 上传清单后在服务器上执行
sudo bash loadtest/job_db_check.sh <run_id> /tmp/_lt/job-ids-<stage>.txt > /tmp/job-db-<run_id>.json

# 取回并写入各档运行摘要，再由 evidence 渲染进报告
.venv\Scripts\python.exe -m runner attach-job-db --from loadtest/local/job-db-<run_id>.json
~~~

脚本只执行 SELECT，凭据从受控凭据文件读取且不写入输出；清单里查不到的 job_id 写成
not_found，不写成 0。核对结果由 evidence 命令渲染进合并报告的“主库 Job 终态核对”
一节，并逐条比对客户端终态与主库终态。这是数据库层证据，与管理端网页界面核对相互独立。

## 日志侧核查

计划要求通过日志系统和管理员端核对一次测试。定位清单由 verify-packet 生成；
如果本地持有可用的 Grafana 管理员口令（写在部署环境文件的
GRAFANA_ADMIN_USER 与 GRAFANA_ADMIN_PASSWORD），可以用同源 /grafana/ 代理查询 Loki：

~~~
.venv\Scripts\python.exe -m runner logs --stage stability
~~~

该命令只读取数据源结构与标签，不输出日志正文；凭据不可用时给出明确结论，
不会改用猜测或匿名访问。

按每个档位的运行时间窗口做一次完整核查，结果写入 local/evidence/logcheck-<档位>.json：

~~~
.venv\Scripts\python.exe -m runner logcheck --stage stability
~~~

核查内容包括各服务各日志级别的条数、按算法能力拆分的收到/完成/失败/慢调用、
事件码分布、web 请求失败、worker 降级告警、request_id 沿用，以及 MCP 最近七天
的成功与失败累计。凭据优先从环境变量 GRAFANA_ADMIN_USER 与
GRAFANA_ADMIN_PASSWORD 读取，其次才回退到部署环境文件，且不回显、不落盘。

MCP 侧每个算法能力有独立的并发额度，只统计 mcp.tool.* 的总条数会被可并行能力
放大，因此核查按 capability 拆开。逐档结果会写进对应的 <档位>-report.md 的
“模型与 MCP 错误”一节；没有该档 logcheck 时，报告如实写未执行，不用摘要补齐。

## 阶段与命令对应

| 计划阶段 | 命令 |
| --- | --- |
| 一 网络与入口预检 | runner preflight |
| 二 创建测试账号 | runner accounts --count N --verify |
| 三 实现压测程序 | runner/ 目录 |
| 四 分级负载 | runner stage --stage <档位> |
| 五 取消与 SSE 恢复专项 | runner recovery |
| 六 监控与记录 | runner evidence、runner verify-packet |

## 目录

| 路径 | 作用 |
| --- | --- |
| runner/ | 压测程序：HTTP/SSE 客户端、指标、场景、日志核查与报告 |
| selftest.py | 本地桩服务上的真实 HTTP 自检：多会话并发、会话约束、账号额度、长排队 |
| verify_report.py | 用已有的服务器采集与主库核对文件验证报告渲染路径 |
| local/ | 全部本地产物根目录，整体加入 .gitignore，方便整目录汇总与清理 |
| local/data/ | 生成的测试 CSV |
| local/accounts.local.json | 测试账号与密码 |
| local/runs/ | 每次运行的原始产物 |
| local/runs/job-ids/ | 按档位导出的本轮 job_id 清单，供服务器逐 Job 主库核对 |
| local/evidence/ | 整理后的报告与核查结果，归档到本地和团队知识库 |
| evidence/index.md | 唯一进入版本库的证据链索引：run_id、时间、镜像 digest、归档位置与结论 |
| local/evidence/loadtest-report-<date>.md | 全部档位与专项的合并报告 |
| local/evidence/log-verification.md | 按档位的 job_id、时间窗口与 Request ID 定位清单 |
| local/evidence/logcheck-<档位>.json | 每档的日志侧核查原始结果 |
| local/evidence/cdfm-failure-analysis.md | causal.cdfm 全部失败的定位与仍需在服务器完成的一步 |
| local/evidence/source-catalog-500-analysis.md | GET /api/rag_eval/isolated/source-catalog 返回 500 的定位 |
| local/server-side-<run_id>-before.json | 负载前的服务器侧只读采集结果 |
| local/server-side-<run_id>-after.json | 停止提交后的服务器只读采集结果 |
| local/job-db-<run_id>.json | 逐 Job 主库终态只读核对结果 |
| loadtest/server_side_collect.sh | 服务器侧单次只读采集脚本 |
| loadtest/stage_resource_sample.sh | 负载窗口主机、容器、数据库连接与队列长度采样脚本 |
| loadtest/job_db_check.sh | 按本轮 job_id 清单逐 Job 只读核对主库的脚本 |
| ops/stage_sampling.py | 一档负载与资源采样串联执行 |
| ops/run_stages.py | 按顺序执行多个档位 |
