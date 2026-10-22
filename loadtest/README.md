# staging 压测客户端

文档职责：记录对 staging 入口执行真实 HTTP/SSE 压测的本地客户端、账号边界、分级负载与报告产物。

适用范围：在本地电脑对一个 staging 入口执行分级负载、CDFM 固定量或到达率场景、取消与恢复专项，并读取新旧报告产物；服务端接口见[Job API](../Document/api/agent-jobs.md)、[会话与文件 API](../Document/api/chat-files.md)，部署事实见[部署说明](../Document/development/deployment.md)。

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

### 固定轮次与客户端版本

新一轮必须先保存部署白名单标识，并建立 `loadtest.round.v1` 清单。清单逐项声明
`stage`、独立 `run_id`、`parameters` 和 `disposition`（planned/adopted/failed/interrupted/invalidated）。
失败、中断和作废项继续保留，不按最近一次同名档位替换。重复档位逐 run_id 出报告。
时间字段使用 UTC epoch 或带偏移的 ISO 时间，报告明确使用 UTC；历史目录名时区不据名称推算。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
# 可复用现有本地凭据文件；不复制到证据包。
$env:LOADTEST_ACCOUNTS_PATH = 'D:/Computer/Projects/CausalAgent/loadtest/local/accounts.local.json'
$python = (Resolve-Path .venv/Scripts/python.exe).Path
$frozen = & $python ops/prepare_round.py --scope targeted --manifest local/round-manifest.json
# 开始运行前核对清单中的任务量、到达率、观察期限和资源保护参数。
$env:LOADTEST_ROUND_MANIFEST = (Resolve-Path local/round-manifest.json).Path
# 在源码副本运行编排，产物仍写入原 local/。
$env:LOADTEST_LOCAL_DIR = (Resolve-Path local).Path
Push-Location $frozen
& $python ops/run_stages.py multi_session_smoke recovery
Pop-Location
& $python -m runner.rounds render --manifest local/round-manifest.json --output local/evidence/round-report
```

冻结客户端副本包含逐文件 SHA256、聚合源码 SHA256、Git 基点、Python 与依赖版本；
未提交的源码也参与 hash。部署标识独立记录 app/worker/MCP/gateway 镜像 digest、
revision、worker/Web/MCP 参数与模型名称；不采集密钥、连接串、提示词或文件正文。
每次实际上传的 CSV 另外记录行列数、seed、字节数和 SHA256。历史缺失 hash 保持未知。
整轮采用同一源码副本，服务器部署在每档前后核对，发生漂移则停止后续运行。

证据、日志、导出与 attach 命令须设置 `LOADTEST_ROUND_MANIFEST` 或
`LOADTEST_RUN_ID`，也可用全局 `--round-manifest` / `--selected-run-id`。
未指定身份时不会自动选择最近档位。附加服务器采集必须与摘要的 run_id 相同。
`runner.rounds render` 只读原摘要，写独立 derived JSON、逐 run_id 报告、
`round-result.json`、`round-report.md` 和直接从结构化结果生成的 `analysis-summary.md`。
旧报告重算应先复制归档并保存原始 hash；新报表不能覆盖旧摘要。

`ops/prepare_round.py` 默认只声明双会话和 Recovery，准备命令本身不提交业务请求。
完整轮次须显式使用 `ops/prepare_round.py --scope full --manifest local/<本轮清单>.json`，一次声明定向专项、
原有十二档、CDFM 固定量和五级到达率，并返回冻结副本目录。默认保护条件为 CPU 连续
三个采样区间达到 98%、可用内存不高于 512 MiB、queued 不低于 128 或数据库连接数
不低于 200；开始前可在清单中明确修改。启动方式与上例相同，不传档位时按清单顺序续跑
planned 项。已有文件不覆盖。定向验证完成后使用同一清单继续，禁止另取历史同名档位。
边界附近需要复测时，先运行 `python -m runner.rounds repeat --manifest <清单>
--source-run-id <已采用运行> --count 2`，再执行编排；追加计划完整复制原档参数，报告按
相同参数比较重复运行，不能把不同到达率合并成一组。

### 观察、专项与诊断

请求发送前声明 `requires_terminal`、`requires_full_stream` 和 `observation_reason`。
全部接纳 Job、声明持续观察的 Job 和声明完整事件流的 Job 分别保存固定分母。
主动取消与提前关闭观察均不移出全部接纳清单。完整订阅的事件集合与主动截断集合分别核对，
截断后未观察到的事件不是“零丢失”。观察结束保存实际时间与原因，区分主动结束、空闲超时、
总期限到达、传输错误和内部异常。客户端终态时间只由客户端接收记录产生。

多会话 smoke 使用同一账号的两个不同会话，在首 Job 活动时提交第二个，分别核对
会话归属、活动交叠和执行区间交叠。两个请求接纳不能代替交叠证据。Recovery 分开记录
主动断开次数、重连尝试/成功次数、重连段事件数、目标终态及同 Job 集合差异；
queued/running 取消先观察真实目标状态，幂等和跨账号专项分别清理实际创建的资源。
未触发 waiting_input 保持未验证。取消 API 回执、主库业务终态、执行释放、算法 PID
终止与浏览器行为分别判断。
空的 `execution_state` 不能证明释放：已执行任务需要 `worker_confirmed` 和有效释放时间；
终态、attempt_count=0 且 started_at 明确为空时单列为从未领取。lease_expired 和缺失释放字段保持未知。

客户端为每个请求生成 `X-Request-ID`，同时保留响应 ID。网关只对白名单只读 API
记录固定路由标签、状态、上游状态及连接/响应/请求耗时，不记录 query、资源正文或凭据。
`ops/enable_gateway_diagnostics.py --output <local/evidence/file.json>` 在实际配置上
增补诊断，先检查候选，备份旧文件，再检查并 reload；该命令不修改并发和超时参数。
没有 Job ID 的读取错误按 request ID 查询；历史没有保存 request ID 时，不能按时间
邻近推定请求关联。异常诊断只保留允许的类型、类别、阶段与状态码，不保存原始异常正文。

CDFM 按 `(job_id, invocation_id)` 识别逻辑调用，再分别核对 worker attempt 和 MCP
retry；缺失任一身份保留未知。日志与持久化事件按身份关联，事件条数不能作为调用次数。
已识别调用成功、全部调用覆盖和算法准确率是三项独立结论；本程序不凭 Job 成功
或场景名称推断算法执行，也不在没有独立标准数据集时给出准确率。

### 分阶段统计与安全续跑

报告同时保存 120/300 秒终态率和成功率，分开普通负载与主动取消专项，提供
提交前调度等待、首次排队、执行跨度、服务器总耗时和客户端等待。吞吐至少保留
三位小数，HTTP、成功 Job 与全部终态 Job 分别统计。资源按负载、排空和额外观察
窗口统计；每个窗口明确起止时间，不使用整轮峰值替代分阶段采样。

编排分别记录场景检查、证据完整性和任务清理。未触发恢复分支可以不完整；只有
主库覆盖充分、接纳未知已解析、业务活动清空且执行释放确认后才允许下一档。
300 秒检查点和最长 1800 秒边界保留目标、实际采集与延迟。300 秒已证实释放时
可以提前结束服务器额外观察，1800 秒明确记为未到达，不声称观察了完整 1800 秒。
客户端可以先关闭，采样和主库核对独立完成。
重启编排时会重新核对清单中所有已执行前档的主库终态和严格释放证据；失败项不会因
只选择后续 planned 项而被跳过。清单中的任务上限、会话、客户端 SSE/活动额度及到达调度
参数全部传入执行器。Recovery 也读取资源停止信号；信号触发后只收尾，不再创建后续任务。
保护指标缺失保持 unknown；开始前等待完整样本，运行期间丢失指标则停止新提交。

到达率场景独立安排每次请求，不等待前任务结束。客户端额度耗尽、到达超时或保护
条件触发后，未发送意图保留原因和调度延迟。每档清单预设任务量或任务保护上限、
计划到达率、观察期限与资源上限；保护触发只停止新提交，继续有界收尾。
没有既定业务性能门槛时，结论只描述成功吞吐、期限内成功率、等待、队列、排空和
重复运行差异，不输出任意的“性能达标”结论。

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
| cdfm_fixed | 1 × 1 | 不限 | 8 | 固定 4 项任务 | 不限 |
| cdfm_rate | 1 × 1 | 不限 | 8 | 200 s，计划 20 次到达、0.1 次/s | 不限 |

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
停止提交后的 300 秒是排空检查点，最终排空观察默认最多 1800 秒；只有主库充分覆盖并确认业务终态和执行释放后才可提前停止
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
.venv\Scripts\python.exe verify_report.py --run-summary local/runs/<run_id>/summary.json --output local/evidence/<run_id>-report-v2.md
.venv\Scripts\python.exe verify_report.py --run-summary local/runs/<run_id>/summary.json --server-json local/server-side-<run_id>-before.json --after-json local/server-side-<run_id>-after.json --job-db-json local/job-db-<run_id>.json
.venv\Scripts\python.exe verify_report.py --run-summary local/runs/<run_id>/summary.json --logcheck-json local/evidence/logcheck-<stage>.json --output local/evidence/<run_id>-report-v2.md
~~~

旧的 server/db 两个位置参数仍可使用，但必须另加 `--run-summary`。跨 run_id 的证据
不能合并。`selftest.py` 使用桩服务，不作为此流程的验收入口；真实证据验证禁止使用
桩、mock/fake 或合成运行替代。真实接口验证需要可用的测试账号、数据库、worker 与模型环境。

## 专项证据边界

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
独立专项执行。local/runs/<run_id>/orchestration.json 保存负载退出码和检查点；
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
.venv\Scripts\python.exe -m runner attach-server --from local/runs/<run_id>/server-before.json --stage mid
.venv\Scripts\python.exe -m runner attach-after --from local/runs/<run_id>/server-after.json --stage mid
.venv\Scripts\python.exe -m runner attach-sample --from local/runs/<run_id>/stage-sample.json --stage mid
.venv\Scripts\python.exe -m runner evidence
~~~

使用 attach 前设置精确的 `LOADTEST_RUN_ID`，并核对采集文件的 run_id；不同轮次的证据不得混入同一摘要。
采集只读，不输出密钥、提示词或文件正文。缺少数据时保留 missing/error/unknown 和
null，不能用 0 冒充采集成功。

## 逐请求与主库 Job 核对

runner export-job-ids 同时输出 local/runs/job-ids/job-ids-<stage>.txt 和
requests-<stage>.json。JSON 身份清单包含 run_id、逻辑请求、账号、会话和原幂等键，
既包括已知 Job，也包括创建结果未知的请求；服务器只读核对，不重新发送创建请求。
旧文本清单只覆盖已知 Job，无法核对没有拿到 job_id 的请求。

~~~
# 本地 loadtest/ 内执行；确认清单属于指定 run_id
.venv\Scripts\python.exe -m runner export-job-ids
scp local/runs/job-ids/requests-mid.json ubuntu@<host>:/tmp/_lt/<run_id>/requests.json

# 服务器执行；已部署完整采集器目录
sudo bash /tmp/_lt/<run_id>/job_db_check.sh <run_id> /tmp/_lt/<run_id>/requests.json > /tmp/job-db-<run_id>.json

# 将结果取回本地后，在 loadtest/ 内执行
.venv\Scripts\python.exe -m runner attach-job-db --from local/runs/<run_id>/job-db-final.json --stage mid
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
local/evidence/logcheck-<stage>.json；只有 run_id 与摘要一致且 scope=run 时，才展示
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
| loadtest/job_db_check.sh | 按请求身份 JSON 或旧 Job ID 清单只读核对主库 |
| ops/server_evidence.py | 服务器采样与主库核对实现，必须随 shell 入口按相对路径部署 |
| ops/stage_sampling.py | 一档负载与资源采样串联执行 |
| ops/run_stages.py | 按顺序执行多个档位 |
