# staging 压测客户端

文档职责：记录对 staging 入口执行真实 HTTP/SSE 压测的本地客户端、账号边界、分级负载与报告产物。

适用范围：在本地电脑对一个 staging 入口执行分级负载、CDFM 固定量或到达率场景、取消与恢复专项，并读取新旧报告产物；服务端接口见[Job API](../Document/api/agent-jobs.md)、[会话与文件 API](../Document/api/chat-files.md)，部署事实见[部署说明](../Document/development/deployment.md)。

## 边界

- 客户端只在本地电脑运行，不占用服务器 CPU 与内存；服务器侧指标需要单独的只读采集入口。
- 只使用测试账号与测试文件。明文 HTTP 入口下不允许出现真实用户密码或真实业务文件。
- 账号密码只写入 loadtest/local/accounts.local.json，不进入报告、日志或版本库。
- 运行产物写入 loadtest/local/runs/<date>/；整理后的报告与逐档核查写入 loadtest/local/evidence/<date>/，默认归档到本地和团队知识库，不进入版本库。日期取 run_id 的 YYYYMMDD 前缀，缺前缀时取开始时刻的本地日期。
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

# CDFM：固定规模、种子和任务总量；到达率是整档请求数/秒
.venv\Scripts\python.exe -m runner stage --stage cdfm_fixed --rows 200 --seed 20261020 --tasks 4
.venv\Scripts\python.exe -m runner stage --stage cdfm_rate --rows 200 --seed 20261020 --tasks 20 --arrival-rate 0.1 --duration 200

# 第五阶段：取消与 SSE 恢复专项
.venv\Scripts\python.exe -m runner recovery

# 沙盒数据处理：先跑单任务真实流程，再跑重复与并发阶梯
.venv\Scripts\python.exe -m runner stage --stage sandbox_baseline
.venv\Scripts\python.exe -m runner stage --stage sandbox_repeat
.venv\Scripts\python.exe -m runner stage --stage sandbox_ladder

# 导出本轮 job_id 清单，供服务器上逐 Job 主库核对
# 默认写入最近一次运行所在的日期目录 local/runs/<date>/job-ids/
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
| cdfm_fixed | 1 × 1 | 不限 | 8 | 固定 4 项任务 | 不限 |
| cdfm_rate | 1 × 1 | 不限 | 8 | 200 s，计划 20 次到达、0.1 次/s | 不限 |
| sandbox_baseline | 1 × 1 | 1 | 1 | flows 1 | 不限 |
| sandbox_repeat | 1 × 1 | 1 | 1 | flows 6 | 不限 |
| sandbox_ladder | 8 × 1 | 按档位 1/2/4/8 | 8 | 每档 180 s | 不限 |
| sandbox_rate | 4 × 1 | 不限 | 16 | 240 s，0.05 次/s，12 项任务 | 不限 |
| sandbox_mixed | 4 × 1 | 4 | 8 | 300 s | 不限 |
| sandbox_stability | 4 × 1 | 4 | 8 | 1800 s | 不限 |

沙盒档位的并发起点是 1、2、4、8 个活动 Job，不复用旧档位的 20/30 账号规模。
`sandbox_ladder` 在单次运行内逐档放宽活动 Job 上限，每档结束后等待已提交任务完成；
数据规模在小、中、大三组之间单独调整，比较并发时保持同一组。

同一账号的不同会话允许同时运行任务，同一会话仍遵守应用现有的活动 Job 约束。并发数量由
服务器默认 worker 能力形成，客户端不提前限流；burst_ungated 与 queue_pressure 的 active_jobs
为“不限”正是为了观察服务器自己的排队行为。

CDFM 输入为相同规模、相同 seed 的确定性数值 CSV，各账号使用相同内容。`cdfm_fixed`
按固定任务量执行，不接受 `--duration`；`cdfm_rate` 按时间独立安排到达，不等待前一个
Job 完成才安排下一个。客户端线程和待观察额度仍有限，调度延迟和未发送请求必须保留，
不能把客户端阻塞后的实际提交节奏称为完整计划到达率。场景参数写入 `scenario`。

指定 CDFM 场景和收到成功报告都不能证明 `causal.cdfm` 实际执行。只有本轮 Job 对应的
真实 invocation、attempt/retry 和最终结果才能形成调用证据；缺少这些记录时，报告保留
CDFM 未采集或证据不足状态。调用成功率、模型准确率和进程终止验收是不同结论。

## 计时与报告 v2

摘要版本为 `schema_version='loadtest.run.v2'`。`requests` 保存逻辑请求字典，
`job.samples` 保存本轮逐 Job 全量结果，未完成 Job 也必须保留。每个逻辑请求保存
`job_id`、`admission`、`created_request_at`、`terminal_received_at`、`user_wait_ms`、
`wait_lower_bound_ms`、`history_check_ms`、`received_event_ids` 和 `group`。发送前将
请求身份写入本轮 `jobs.jsonl`，便于客户端中断后按原幂等键只读核对；不得记录提示词、
密码或文件正文。重用已有产物的 run_id 会被拒绝，不能覆盖或混入历史记录。

| 时间或指标 | 口径 |
| --- | --- |
| created_request_at → terminal_received_at | 首次创建请求发出到客户端收到终态；user_wait_ms 包含创建请求、排队、执行与传输 |
| wait_lower_bound_ms | 观察结束仍未收到终态时的等待下界，不作为已完成耗时 |
| history_check_ms | 读取会话历史核对所花时间，单独计量，不追加到用户等待 |
| initial_queue_ms | 主库创建到明确标记的首次开始；缺首次标记时未采集 |
| execution_span_ms | 首次开始到结束的跨度；可能包含恢复、重试或等待输入，不等于纯算法 CPU 时间 |
| server_total_ms | 主库创建到结束，与客户端用户等待分别报告 |
| stop_submitting_at | 停止提交时间，区分 load 和 drain |
| observation_ended_at | 客户端观察结束；不能用事后主库终态回填实时收到时间 |
| db_checked_at | 同 run_id 的主库核对时间；为空时最终主库摘要待核对 |

普通 HTTP 默认超时 30 秒，SSE 空闲读取默认 45 秒，单 Job 总观察默认 1800 秒。
停止提交后的 300 秒是排空检查点，最终排空观察默认最多 1800 秒，不能在检查点就停止
全部观察。覆盖参数见 `runner/config.py`；专项流使用同样的总期限，持续心跳不会延长它。

`phases.load` 和 `phases.drain` 分别保存 `http_requests`、`http_rps`、
`client_completions`、`client_completion_throughput`，并独立显示主库
`server_completions` 与 `server_completion_throughput`。HTTP RPS 的分母是相应阶段时长，
HTTP 样本按发起时间归属阶段；客户端完成按终态收到时间归属，主库完成按 finished_at
归属。主库覆盖不足时必须连同覆盖率阅读，不能把已核对子集当成服务器全量吞吐。
阶段排队分位按 `first_started_at` 归属，只统计已完成且有首次排队计时的样本。

`drain.checkpoint_ms` 与 `drain.checkpoint.drained` 展示 300 秒检查点是否排空；
`finally_drained`、`drain_ms` 表示主库确认的最终排空与耗时；`final_state`、`observed_ms`
保留最终状态和实际观察时长。`client_observers_cleared`、`client_drain_ms` 单列客户端
待观察集合清空与耗时。检查失败、
未排空与未采集必须分别显示，不以 0 秒或空队列冒充核查成功。
`business_active_cleared` 与 `execution_released` 分别表示业务活动清空和执行释放；
客户端待观察集合为空只表示客户端观察结束。最终服务器排空须依据同轮主库核对覆盖
与释放证据，证据不足时保持 unknown。

`capacity` 由 `runner/capacity.py` 的纯函数 `enrich_summary(summary)` 计算。报告分别给出
接纳分母与接纳/重放/冲突/未知数量、主库成功率与核对覆盖率、120/300 秒完成率及未知数、
实时终态覆盖和事后主库终态数量、SSE 全量事件集合核对覆盖，以及按 group 分开的延迟。
新 Job 接纳使用有效逻辑请求分母和唯一 accepted 数；`admission.request_receipt_rate` 单列请求回执率，
不能把幂等重放当成新 Job 接纳。
所有只统计完成请求的分位均标注“仅完成样本 p95”，不能代替包含未完成请求的等待分布。

SSE 峰值只计成功收到 HTTP 200 响应头的连接，等待额度、等待响应头和关闭分别计数。
`connection_attempts` 是建连尝试数，`error_events_count` 与 `error_events` 保留错误次数
和类型；后来恢复成功不会抹去先前错误。
实际订阅中的错误数由 `subscriptions_with_errors` 保存，`subscription_error_rate` 以
全部 `subscriptions` 为分母；同一订阅多次出错只增加一次出错订阅数，恢复也不清零。
整数事件 ID 的跳号不是漏事件证据，只有同 Job
实际收到的完整 ID 集合与持久化集合核对才可计算漏事件；未核对时为 null/未采集。

旧 summary 无需改写即可渲染。没有 requests 时接纳分母和实时终态覆盖率为空；缺少
时间点、首次开始标记或事件集合时显示未采集。旧主库 created_at/finished_at 可以计算
服务器总耗时，但不能据旧 started_at 推算首次排队。旧 SSE 峰值可能包含等待建连，
不能与 v2 的成功建连峰值直接比较。

## 真实产物报告检查

`verify_report.py` 必须读取真实运行摘要，服务器和主库文件可使用摘要中已有的内容，
或显式指定同 run_id 的真实文件。`--logcheck-json` 只接受同 run_id 且 scope=run 的真实
日志核查。它不构造合成运行，不修改原始 JSON；通过只表示
报告结构检查完成，不表示系统、CDFM、执行释放或前端验收通过。

~~~
# 以下命令在 loadtest/ 内执行；输出独立文件，保留原始产物
.venv\Scripts\python.exe verify_report.py --run-summary local/runs/<date>/<run_id>/summary.json --output local/evidence/<date>/<run_id>-report-v2.md
.venv\Scripts\python.exe verify_report.py --run-summary local/runs/<date>/<run_id>/summary.json --server-json local/server-side-<run_id>-before.json --after-json local/server-side-<run_id>-after.json --job-db-json local/job-db-<run_id>.json
.venv\Scripts\python.exe verify_report.py --run-summary local/runs/<date>/<run_id>/summary.json --logcheck-json local/evidence/<date>/logcheck-<stage>.json --output local/evidence/<date>/<run_id>-report-v2.md
~~~

旧的 server/db 两个位置参数仍可使用，但必须另加 `--run-summary`。跨 run_id 的证据
不能合并。`selftest.py` 使用桩服务，不作为此流程的验收入口；真实证据验证禁止使用
桩、mock/fake 或合成运行替代。真实接口验证需要可用的测试账号、数据库、worker 与模型环境。

## 专项证据边界

### 沙盒数据处理专项

沙盒档位继承普通档位的 HTTP、SSE、账号、多会话和证据归档能力，只替换业务载荷与
交互答复。`sandbox_baseline`、`sandbox_repeat`、`sandbox_ladder`、`sandbox_rate`、
`sandbox_mixed` 与 `sandbox_stability` 使用固定种子的五列连续数据，场景预先声明观察
单位（每行一台设备的一次独立测量）和允许的处理方式（保留全部行与 x1 至 x5）。

交互答复由 `interaction_type` 决定：算法选项提交 `option_id`，研究语义问题提交场景
文本，方案确认提交 `{"action":"confirm","plan_version":"<当前事件版本>"}`。未识别
类型只记录缺口，不发送默认答复；方案的转换说明含聚合、筛选、删除或重命名，或变量
范围与场景声明不一致时，记录为偏离并拒绝自动确认。每个 Job 的交互轮数与总时间分别
受 `LOADTEST_MAX_INTERACTIONS` 和 `LOADTEST_INTERACTION_DEADLINE_SECONDS` 限制。
同一问题重试复用同一幂等键，新问题使用新键。

阶段的取消检查按真实前置条件触发：排队中、等待方案确认、等待沙盒容量、脚本执行中
和取消与自然完成竞争。等待容量一项目前没有独立计时事件，因此保持未验证；执行中取消
必须由主库沙盒会话的 `execute_started` 阶段时间确认，不能只凭创建成功推断。

沙盒会话证据来自主库 `analysis_sandbox_sessions`：按本轮 Job 读取请求 ID、远端 ID、
状态、执行状态、创建与关闭时间，并读取控制服务写入的 `reserve_committed`、
`remote_created`、`initialized`、`execute_started`、`execute_finished` 阶段时间。
一个 Job 可以创建多个沙盒会话，因此报告同时给出 Job 覆盖率与每 Job 会话数。动态容器
按 Docker 标签 `application=causalagent` 与 `request` 归属；没有本轮会话映射的容器
单列为其他请求或未归属，不按名称猜测。

排空结论分四层：业务活动结束、Job 执行释放、沙盒资源释放与完整排空。完整排空要求
身份核对完整、Job 已终结、执行已释放、全部会话已关闭并取得远端结束证据；缺少证据时
显示未知，不沿用旧结论。旧产物没有这些字段时，报告显示未采集。

Worker 本地并发等待与全局容量等待尚无独立计时，报告把“控制服务拒绝容量请求的次数”
与“等待时长”分开表述；这两项不能互相替代。

`runner recovery` 分别记录断线续传与同 Job 完整事件重放、同键幂等、确实观察到
queued/running 后的业务取消、5 秒短窗口内状态不反转、实际触发 waiting_input 后恢复
到 final_result，以及真实存在资源的跨账号隔离。未触发的分支标为未验证；恢复后出现
error/canceled/再次 interrupt 不能当成恢复成功。创建超时按原键有界重试，仍不确定时
保存账号、原键和会话供主库只读核对，不能当成未创建。

取消 API 与历史 canceled 只证明业务状态。执行释放还需主库 execution_state、
execution_released_at、释放原因和 worker 槽位证据；算法进程终止还需 invocation、
目标 PID、取消响应与进程消失证据；前端阶段还需真实浏览器中的刷新恢复、等待输入
展示和迟到事件不反转。报告单列三类缺口，不能因 API 子项通过而给专项全通过。

专项命令退出码：1 表示已执行检查失败，3 表示无已知失败但仍有未触发分支或证据缺口，
0 才表示没有未完成项。当前 API 专项保留上述服务器和前端缺口，因此不能给出完整验收
通过。现有编排脚本遇到非零会停止，后续档位需按独立计划执行。

## 服务器采集与编排

服务器单次快照由 server_side_collect.sh 采集；持续资源观察由
stage_resource_sample.sh 采集；逐请求与 Job 主库核对由 job_db_check.sh 完成。
后两者调用 ops/server_evidence.py，服务器必须有 Python 3，并保持相对布局：

~~~text
/tmp/_lt/<run_id>/
├── server_side_collect.sh
├── stage_resource_sample.sh
├── job_db_check.sh
└── ops/server_evidence.py
~~~

ops/stage_sampling.py 自动为每个 run_id 创建独立远端目录并上传这四个文件，运行
负载前快照、真实负载、持续采样、300 秒与最终主库检查、收尾快照，再将证据写回
该轮摘要。停止提交边界取自客户端 phases.json，与观察结束、主库核对时间分别保存；
采集失败、范围不明或缺少停止边界时保留 unknown，不宣称已排空。
采样器只启动一次，记录真实 PID 并核验其命令行包含本轮服务器采样入口和 run_id；
SSH 启动回执丢失时只核验进程，不重复启动。停止边界写入和采样结果回收使用有界重试，
回收失败仍继续独立的收尾快照和最终主库核对，整档保留 unknown。

服务器的 `.env.staging` 只提供数据库账号和库名；密码由数据库容器内的
`/opt/causalagent/secret-env.sh` 从挂载的统一密钥文件读取。采集命令不传递密码参数。

~~~
# 本地命令在 loadtest/ 内执行；SSH 配置见 ops/stage_sampling.py
.venv\Scripts\python.exe ops/stage_sampling.py mid
.venv\Scripts\python.exe ops/run_stages.py low mid_low mid stability burst burst_ungated queue_pressure
~~~

编排逐档串行执行，遇到非零退出码停止。recovery 保留专项缺口时退出码为 3，请作为
独立专项执行。local/runs/<date>/<run_id>/orchestration.json 保存负载退出码和检查点；
同目录的 jobs.jsonl、phases.json、原始采集和最终摘要组成该轮证据。负载退出码
不能代替全部取证结果。

手工运行也要上传完整相对目录，以下本地命令在 loadtest/ 内执行：

~~~
ssh ubuntu@<host> "mkdir -p /tmp/_lt/<run_id>/ops"
scp server_side_collect.sh stage_resource_sample.sh job_db_check.sh ubuntu@<host>:/tmp/_lt/<run_id>/
scp ops/server_evidence.py ubuntu@<host>:/tmp/_lt/<run_id>/ops/
~~~

服务器快照和资源采样示例：

~~~
sudo bash /tmp/_lt/<run_id>/server_side_collect.sh <run_id> before > /tmp/server-before-<run_id>.json
sudo bash /tmp/_lt/<run_id>/stage_resource_sample.sh <run_id> 2400 10 mid /tmp/_lt/<run_id>/stop-submitting.json 1800 > /tmp/stage-sample-<run_id>.json
sudo bash /tmp/_lt/<run_id>/server_side_collect.sh <run_id> after > /tmp/server-after-<run_id>.json
~~~

stop-submitting.json 必须来自同 run_id 的真实 phases.json 停止边界，包含 run_id 与
stop_submitting_at，不能猜时间。手工运行须并行启动采样和客户端负载，采样读取停止
边界后继续覆盖最终观察上限，300 秒仅记检查点。等待采样完成后再取回 JSON；没有
停止标记时只有固定窗口数据，不能证明排空检查点覆盖完整。after 快照在观察结束后采集。

取回本地后按明确档位合并，命令在 loadtest/ 内执行：

~~~
.venv\Scripts\python.exe -m runner attach-server --from local/runs/<date>/<run_id>/server-before.json --stage mid
.venv\Scripts\python.exe -m runner attach-after --from local/runs/<date>/<run_id>/server-after.json --stage mid
.venv\Scripts\python.exe -m runner attach-sample --from local/runs/<date>/<run_id>/stage-sample.json --stage mid
.venv\Scripts\python.exe -m runner evidence
~~~

使用 attach 前设置精确的 run_id（`$env:LOADTEST_RUN_ID = '<run_id>'` 或全局 `--selected-run-id`），命令据此选择该运行所在的日期目录；不同日期、不同轮次的证据不得混入同一摘要。
采集只读，不输出密钥、提示词或文件正文。缺少数据时保留 missing/error/unknown 和
null，不能用 0 冒充采集成功。

## 逐请求与主库 Job 核对

runner export-job-ids 同时输出 local/runs/<date>/job-ids/job-ids-<stage>.txt 和
requests-<stage>.json。JSON 身份清单包含 run_id、逻辑请求、账号、会话和原幂等键，
既包括已知 Job，也包括创建结果未知的请求；服务器只读核对，不重新发送创建请求。
旧文本清单只覆盖已知 Job，无法核对没有拿到 job_id 的请求。

~~~
# 本地 loadtest/ 内执行；确认清单属于指定 run_id
.venv\Scripts\python.exe -m runner export-job-ids
scp local/runs/<date>/job-ids/requests-mid.json ubuntu@<host>:/tmp/_lt/<run_id>/requests.json

# 服务器执行；已部署完整采集器目录
sudo bash /tmp/_lt/<run_id>/job_db_check.sh <run_id> /tmp/_lt/<run_id>/requests.json > /tmp/job-db-<run_id>.json

# 将结果取回本地后，在 loadtest/ 内执行
.venv\Scripts\python.exe -m runner attach-job-db --from local/runs/<date>/<run_id>/job-db-final.json --stage mid
~~~

正常编排直接使用同轮 JSONL 或 summary 导出的请求清单，核对主库终态、会话归属、
首次开始/结束时间、attempt/recovery/resume、执行释放、事件 ID 与 CDFM 调用。
客户端缺少终态的 Job 仍保留在唯一 ID 清单；接纳未知的请求通过原身份核对补回，
无法核实时保留未知。禁止按相近运行时间或全库分布补认某个 Job 属于本轮。

旧 job-db-*-extras.json 可单独展示，但必须有可追溯的本轮身份来源，不并入逐档一致
数量；旧产物若丢失身份，报告保留范围无法核实。主库核对与管理端网页验证相互独立。

## 日志侧核查

runner verify-packet 生成 Job、请求 ID 与时间定位清单。Grafana 账号从部署配置或显式
环境变量读取，口令从 `CAUSALAGENT_SECRET_FILE` 指定的统一密钥文件读取，未指定时使用
仓库根目录的 `causalagent.env`，按官方容器 Shell 规则保留口令中的引号和空格；
也保留显式进程账号与口令的调用方式。日志核查保留
异常类型、失败阶段、错误分类、原因码和已经脱敏的栈顶位置，不读取异常正文。
凭据不回显、不写入产物；日志源不可用时保留未采集状态。以下命令在 loadtest/ 内执行：

~~~
.venv\Scripts\python.exe -m runner logs --stage stability
.venv\Scripts\python.exe -m runner logcheck --stage stability
~~~

logcheck 用该档真实 Job 清单和同 run_id 主库恢复出的身份，按 Job 与服务查询；
时间窗口只缩小查询范围。结果保存 scope=run、run_id 与逐 Job/服务 coverage，
以及请求 ID 沿用、生命周期和按能力拆分的 MCP 事件数。查询失败、截断或缺少某个
Job 日志时保留未知，不能用空结果声明无错误。

CDFM 统计须关联本轮 Job 和 invocation，并区分实际执行尝试、重试与最终状态。主库
tool payload 未保存完整 invocation_id、retry 和 duration，不能单独算完整 CDFM 指标。
MCP 日志提供真实调用身份、retry_ordinal、duration_ms 与事件状态；只有 logcheck 的
run_id 与摘要一致且 scope=run，报告才在内存中注入 cdfm_log_evidence 并重新汇总，
不改写历史 JSON。身份或计时字段缺失时保持未采集，不补造 attempt=0。
全局累计数量不参与本轮成功率；旧日志未声明准确归属时只保留历史记录。产物写入
local/evidence/<date>/logcheck-<stage>.json；只有 run_id 与摘要一致且 scope=run 时，才展示
本轮 MCP 错误、成功数量和 CDFM 统计。旧产物仍可读取，但无本轮 scope 时标为未采集，
同 stage 不代表同一轮。报告同时展示状态和覆盖范围。没有真实日志或
主库调用记录时，CDFM 与服务端专项仍未验收。

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
| selftest.py | 接口桩工具；结果不提供真实系统验收证据 |
| verify_report.py | 只消费真实运行 summary 与同轮真实采集，验证新旧报告渲染 |
| local/ | 全部本地产物根目录，整体加入 .gitignore，方便整目录汇总与清理 |
| local/data/ | 生成的测试 CSV |
| local/accounts.local.json | 测试账号与密码 |
| local/runs/<date>/ | 同一天的全部运行原始产物 |
| local/runs/<date>/<run_id>/ | 一次运行的摘要、jobs.jsonl、phases.json、控制台输出与服务器采集 |
| local/runs/<date>/job-ids/ | 按档位导出的本轮 job_id 清单，供服务器逐 Job 主库核对 |
| local/evidence/<date>/ | 同一天的合并报告、逐档报告与核查结果，归档到本地和团队知识库 |
| evidence/index.md | 唯一进入版本库的证据链索引：run_id、时间、镜像 digest、归档位置与结论 |
| local/evidence/<date>/loadtest-report-<date>.md | 当天全部档位与专项的合并报告 |
| local/evidence/<date>/log-verification.md | 按档位的 job_id、时间窗口与 Request ID 定位清单 |
| local/evidence/<date>/logcheck-<档位>.json | 每档的日志侧核查原始结果 |
| local/evidence/<date>/cdfm-failure-analysis.md | causal.cdfm 全部失败的定位与仍需在服务器完成的一步 |
| local/evidence/<date>/source-catalog-500-analysis.md | GET /api/rag_eval/isolated/source-catalog 返回 500 的定位 |
| local/server-side-<run_id>-before.json | 负载前的服务器侧只读采集结果 |
| local/server-side-<run_id>-after.json | 停止提交后的服务器只读采集结果 |
| local/job-db-<run_id>.json | 逐 Job 主库终态只读核对结果 |
| loadtest/server_side_collect.sh | 服务器侧单次只读采集脚本 |
| loadtest/stage_resource_sample.sh | 负载窗口主机、容器、数据库连接与队列长度采样脚本 |
| loadtest/job_db_check.sh | 按请求身份 JSON 或旧 Job ID 清单只读核对主库 |
| ops/server_evidence.py | 服务器采样与主库核对实现，必须随 shell 入口按相对路径部署 |
| ops/stage_sampling.py | 一档负载与资源采样串联执行 |
| ops/run_stages.py | 按顺序执行多个档位 |
