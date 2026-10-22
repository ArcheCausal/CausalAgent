# staging 压测证据链索引

文档职责：登记每一轮 staging 压测报告的 run_id、时间、被测镜像版本和结论，作为进入版本库的唯一压测证据文件。

适用范围：需要按结论回溯某一次压测、或核对某份报告对应哪个服务版本时使用；完整报告正文与原始 logcheck 不在本仓库，见“归档位置”一列。

完整报告正文、逐档 logcheck JSON 与缺陷分析在 loadtest/local/evidence/ 生成并同步到团队知识库，不进入版本库；本表只保留可追溯的最小索引。归档位置确定后手工填写，未归档时留“待归档”。

| 档位 | run_id | 测试时间 | 被测镜像 digest | 归档位置 | 结论 |
| --- | --- | --- | --- | --- | --- |
| smoke | 20261012T001500-smoke | 2026-10-12 00:15 | app@sha256:00786479…61947a | 待归档 | 单用户链路可用，causal.cdfm 完成 0 / 失败 1 |
| baseline | 20261012T003816-baseline | 2026-10-12 00:38 | app@sha256:00786479…61947a | 待归档 | 单用户基线通过，无 4xx/5xx |
| low | 20261012T005116-low | 2026-10-12 00:51 | app@sha256:00786479…61947a | 待归档 | 低并发通过 |
| mid_low | 20261012T010307-mid_low | 2026-10-12 01:03 | app@sha256:00786479…61947a | 待归档 | 中低并发通过 |
| mid | 20261012T011719-mid | 2026-10-12 01:17 | app@sha256:00786479…61947a | 待归档 | 中并发通过 |
| stability | 20261012T013706-stability | 2026-10-12 01:37 | app@sha256:00786479…61947a | 待归档 | 30 分钟稳定性通过 |
| burst | 20261012T025527-burst | 2026-10-12 02:55 | app@sha256:00786479…61947a | 待归档 | 20 用户突发通过，活动 Job 峰值 4 |
| recovery | 20261012T035937-recovery | 2026-10-12 03:59 | app@sha256:00786479…61947a | 待归档 | SSE 断线重连、取消与不反转通过 |

## 2026-10-16 第二轮（镜像 0b796765）

入口 http://203.195.194.205:8088；全部档位被测版本一致。本节逐档结论如下。

| 档位 | run_id | 测试时间 | 被测镜像 digest | 归档位置 | 结论 |
| --- | --- | --- | --- | --- | --- |
| smoke | 20261017T012210-smoke | 2026-10-17 01:22 | app@sha256:da62b3ce…45c72d | 待归档 | 补跑的单用户单流程检查通过，1/1 成功，无 4xx/5xx |
| baseline | 20261017T015433-baseline | 2026-10-17 01:54 | app@sha256:da62b3ce…45c72d | 待归档 | 单用户基线按档位表的 full_flow 4 轮运行，4/4 成功，无 4xx/5xx |
| low | 20261016T193952-low | 2026-10-16 19:40 | app@sha256:da62b3ce…45c72d | 待归档 | 通过，8/8 成功；p95 78 ms |
| mid_low | 20261016T194654-mid_low | 2026-10-16 19:47 | app@sha256:da62b3ce…45c72d | 待归档 | 通过，44/44 成功；SSE 断线 1 次且重连后无漏事件 |
| mid | 20261016T195902-mid | 2026-10-16 19:59 | app@sha256:da62b3ce…45c72d | 待归档 | 通过，39/39 成功；排队 p95 75.5 s |
| stability | 20261016T201101-stability | 2026-10-16 20:11 | app@sha256:da62b3ce…45c72d | 待归档 | 30 分钟稳定性通过，206/206 成功，CPU 均值 31.2% 峰值 57.5% |
| recovery | 20261016T221524-recovery | 2026-10-16 22:15 | app@sha256:da62b3ce…45c72d | 待归档 | 断线恢复、幂等、取消排队与运行中 Job、取消后不反转、跨账号隔离均通过；waiting_input 分支未触发 |
| burst | 20261016T223059-burst | 2026-10-16 22:32 | app@sha256:da62b3ce…45c72d | 待归档 | 20 账号突发通过，35/35 成功；活动 Job 峰值 4 |
| burst_ungated | 20261016T225147-burst_ungated | 2026-10-16 22:52 | app@sha256:da62b3ce…45c72d | 待归档 | 不限创建时活动 Job 峰值 20，服务端排队长度峰值 18；5 个 Job 超出 300 s 排空期限，补查主库全部成功 |
| queue_pressure | 20261016T231739-queue_pressure | 2026-10-16 23:19 | app@sha256:da62b3ce…45c72d | 待归档 | 30 账号压力下网关出现 21 个 504 与 6 个 502；1 个 Job 因事件流 504 未收到终态，主库记为成功；4 个 Job 超出排空期限，补查全部成功 |
| multi_session_smoke | 20261016T234326-multi_session_smoke | 2026-10-16 23:43 | app@sha256:da62b3ce…45c72d | 待归档 | 单账号两会话可同时运行 Job，通过 |
| multi_session_pressure | 20261016T235120-multi_session_pressure | 2026-10-16 23:52 | app@sha256:da62b3ce…45c72d | 待归档 | 10 账号 × 2 会话通过，活动 Job 峰值 19；排空用时 184 s；出现 3 个 502，均由重试与主库核对确认不影响 Job 终态 |

本轮各档共同结论：12 个档位全部执行完毕，无“未执行”项；MCP 三个算法能力 causal.cdfm、causal.pc、causal.direct_lingam 全部按调用成功完成，未再出现能力级失败；请求日志的 request_id 在 web 与 worker 之间沿用；逐 Job 主库核对 430 条记录，其中 429 条与客户端终态一致，1 条因客户端事件流收到 504 未观测到终态（主库记为成功）；另有 9 条创建后超出排空期限、客户端没有观测结果的 Job 由补充核对确认全部成功。smoke 与 baseline 为收尾按档位定义补跑，单独成档。

## 2026-10-12 第一轮（镜像 94c6374c）

缺陷单列（第一轮）：

| 缺陷 | 现象 | 归档位置 |
| --- | --- | --- |
| causal.cdfm 全部失败 | 全部档位完成数为 0，单条耗时约 139 秒，属服务实现与模型依赖 | 待归档 |
| source-catalog 返回 500 | GET /api/rag_eval/isolated/source-catalog 返回 500，知识源清单与 .dockerignore 口径不一致 | 待归档 |

压测观察到的待修复项（未计入上表档位结论，暂按 P2 记录）：

| 缺陷 | 现象 | 优先级 |
| --- | --- | --- |
| 取消后阶段列表仍显示进行中 | Job 终态已是“任务已取消”，阶段列表仍保留“执行 Deep Agent 分析 进行中”字样，状态理解存在歧义 | P2 |

第一轮被测版本的完整 digest 为 ghcr.io/archecausal/causalagent-app@sha256:00786479a432cf60a892b4a3a351b14f87b26d29c2fc18d0208789d86861947a（app_image_revision_label 94c6374ce70ef9f715a771b9ba21a22b78b9321b）。

第二轮被测版本的完整 digest 为 ghcr.io/archecausal/causalagent-app@sha256:da62b3cedc367704f4c45538d15f3b2cf5bf0b314dad891143ce36902945c72d（app_image_revision_label 0b796765bdf2992d4d597fd2f21a73ecd872dde6），causal-mcp 为 ghcr.io/archecausal/causalagent-mcp@sha256:1161cc93035c46db02e1425a8dcf376c62e4b9d8a913efe25bdb52d5bd0cb24c，各档一致。

第二轮对第一轮两项缺陷的复核：causal.cdfm 已修复，连续 11 个档位均成功完成；source-catalog 对测试账号返回 403 权限不足，该接口要求 RAG 评测台访问权限，测试账号无此权限，因此本轮无法复现或排除原来的 500，仍需有管理员权限的人复核。

第二轮复现的待修复项：

| 缺陷 | 现象 | 优先级 |
| --- | --- | --- |
| 取消后阶段列表仍显示进行中 | 复测确认：Job 终态与 thinking_after.status 均为 canceled，但被取消时正在执行的阶段只有 node_start、没有 node_end，前端按“时长缺失即进行中”渲染，因此该阶段仍显示“进行中”。典型样本：阶段“预处理数据” | P2 |


## 2026-10-20 至 2026-10-21 第三轮重算（镜像 76f6a63f）

本轮清单为 `loadtest/local/evidence/history-20261020-21-recalculated/round-manifest.json`。其中 12 次采用、4 次作废、3 次中断，原始 238 个文件已复制归档并逐文件核对 SHA256。目录名的原时区未核验，以下时间直接由真实 epoch 展示为 UTC。

app digest：`ghcr.io/archecausal/causalagent-app@sha256:b70b70521f9900b89c0d933226db3bdbe4070c8ea129e1ea8f59ea21e78f4d2c`；MCP digest：`ghcr.io/archecausal/causalagent-mcp@sha256:db36e31e301ac399abd66ad995856935494847f95931c834c1093230771ebe47`。历史客户端源码 hash 与输入 hash 缺失，保持未知。

| 档位 | run_id | 开始时间（UTC） | 唯一接纳 | 成功 / 失败 / 取消 | 120 秒终态率 / 成功率 | 归档位置 |
| --- | --- | --- | --- | --- | --- | --- |
| smoke | 20261020T225813-smoke | 2026-10-20 14:58:29 | 1 | 1 / 0 / 0 | 100.00% / 100.00% | local/evidence/history-20261020-21-recalculated/reports/20261020T225813-smoke-report.md |
| baseline | 20261020T232946-baseline | 2026-10-20 15:30:02 | 4 | 4 / 0 / 0 | 100.00% / 100.00% | local/evidence/history-20261020-21-recalculated/reports/20261020T232946-baseline-report.md |
| low | 20261021T000344-low | 2026-10-20 16:04:00 | 9 | 9 / 0 / 0 | 100.00% / 100.00% | local/evidence/history-20261020-21-recalculated/reports/20261021T000344-low-report.md |
| mid_low | 20261021T003927-mid_low | 2026-10-20 16:39:51 | 46 | 45 / 1 / 0 | 100.00% / 97.83% | local/evidence/history-20261020-21-recalculated/reports/20261021T003927-mid_low-report.md |
| mid | 20261021T012025-mid | 2026-10-20 17:20:48 | 96 | 95 / 1 / 0 | 100.00% / 98.96% | local/evidence/history-20261020-21-recalculated/reports/20261021T012025-mid-report.md |
| stability | 20261021T020132-stability | 2026-10-20 18:01:40 | 410 | 407 / 3 / 0 | 100.00% / 99.27% | local/evidence/history-20261020-21-recalculated/reports/20261021T020132-stability-report.md |
| recovery | 20261021T111044-recovery | 2026-10-21 03:10:59 | 7 | 2 / 0 / 5 | 100.00% / 28.57% | local/evidence/history-20261020-21-recalculated/reports/20261021T111044-recovery-report.md |
| burst | 20261021T114930-burst | 2026-10-21 03:49:45 | 67 | 63 / 4 / 0 | 100.00% / 94.03% | local/evidence/history-20261020-21-recalculated/reports/20261021T114930-burst-report.md |
| burst_ungated | 20261021T123023-burst_ungated | 2026-10-21 04:30:39 | 78 | 75 / 3 / 0 | 50.00% / 50.00% | local/evidence/history-20261020-21-recalculated/reports/20261021T123023-burst_ungated-report.md |
| queue_pressure | 20261021T131741-queue_pressure | 2026-10-21 05:17:57 | 45 | 43 / 2 / 0 | 8.89% / 8.89% | local/evidence/history-20261020-21-recalculated/reports/20261021T131741-queue_pressure-report.md |
| multi_session_smoke | 20261021T142148-multi_session_smoke | 2026-10-21 06:22:04 | 2 | 1 / 1 / 0 | 100.00% / 50.00% | local/evidence/history-20261020-21-recalculated/reports/20261021T142148-multi_session_smoke-report.md |
| multi_session_pressure | 20261021T153757-multi_session_pressure | 2026-10-21 07:38:14 | 82 | 78 / 4 / 0 | 62.20% / 59.76% | local/evidence/history-20261020-21-recalculated/reports/20261021T153757-multi_session_pressure-report.md |

主库业务分类合计：成功 823、失败 19、取消 5；取消专项与普通负载在逐档报告中分别统计。已确认 19 个失败 Job 和 6 个只读接口 502，旧客户端缺少对应 request ID，不能给出完整请求关联结论。

多会话 smoke 缺少不同会话的活动交叠记录，专项保持未验证。历史观察策略缺失，完整流与主动截断不能按新分母追认，SSE 不作零丢失结论。CDFM 部分调用缺少 worker attempt 或其他身份，不能将事件条数等同逻辑调用，也不能判定全部调用覆盖或算法准确率。CDFM 固定量和独立到达率在该历史轮均未执行。

派生总报告：`loadtest/local/evidence/history-20261020-21-recalculated/reports/round-report.md`；结构化结果：同目录 `round-result.json`；直接生成的统计摘要：`analysis-summary.md`；完整原始副本：归档下 `raw/`；原始 hash：`original-hashes.json`。原运行摘要与原始采集均未改写。

