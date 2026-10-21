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


## 2026-10-20 至 2026-10-21 第三轮（镜像 76f6a63f）

入口 http://203.195.194.205:8088；app digest 为 ghcr.io/archecausal/causalagent-app@sha256:b70b70521f9900b89c0d933226db3bdbe4070c8ea129e1ea8f59ea21e78f4d2c（revision 76f6a63f），causal-mcp 为 ghcr.io/archecausal/causalagent-mcp@sha256:db36e31e301ac399abd66ad995856935494847f95931c834c1093230771ebe47。12 个档位全部执行完毕，主库核对覆盖率均为 100%，全部排空。

| 档位 | run_id | 测试时间 | 归档位置 | 结论 |
| --- | --- | --- | --- | --- |
| smoke | 20261020T225813-smoke | 2026-10-20 22:58 | local/evidence/loadtest-report-20261021.md | 1/1 成功，无 4xx/5xx |
| baseline | 20261020T232946-baseline | 2026-10-20 23:29 | local/evidence/loadtest-report-20261021.md | 4/4 成功，120 秒完成率 100% |
| low | 20261021T000344-low | 2026-10-21 00:03 | local/evidence/loadtest-report-20261021.md | 9/9 成功，无 4xx/5xx |
| mid_low | 20261021T003927-mid_low | 2026-10-21 00:39 | local/evidence/loadtest-report-20261021.md | 46 个 Job，45 成功，120 秒完成率 100% |
| mid | 20261021T012025-mid | 2026-10-21 01:20 | local/evidence/loadtest-report-20261021.md | 96 个 Job，95 成功，1 次 load_session 连接超时 |
| stability | 20261021T020132-stability | 2026-10-21 02:01 | local/evidence/loadtest-report-20261021.md | 30 分钟 410 个 Job，407 成功，CPU 峰值 63.4% |
| recovery | 20261021T111044-recovery | 2026-10-21 11:10 | local/evidence/loadtest-report-20261021.md | API 专项通过；waiting_input、进程终止与前端证据缺口保留 |
| burst | 20261021T114930-burst | 2026-10-21 11:49 | local/evidence/loadtest-report-20261021.md | 67 个 Job，63 成功，活动 Job 峰值 4 |
| burst_ungated | 20261021T123023-burst_ungated | 2026-10-21 12:30 | local/evidence/loadtest-report-20261021.md | 78 个 Job，活动峰值 20，120 秒完成率 50%，6 个只读接口 502 |
| queue_pressure | 20261021T131741-queue_pressure | 2026-10-21 13:17 | local/evidence/loadtest-report-20261021.md | 45 个 Job，排队 p95 313 秒，120 秒完成率 8.9% |
| multi_session_smoke | 20261021T142148-multi_session_smoke | 2026-10-21 14:21 | local/evidence/loadtest-report-20261021.md | 同账号两会话并发可用，2 个 Job 均接纳 |
| multi_session_pressure | 20261021T153757-multi_session_pressure | 2026-10-21 15:37 | local/evidence/loadtest-report-20261021.md | 82 个 Job，78 成功，120 秒完成率 62.2% |

本轮共同结论：全部档位 HTTP 429 为 0；主库 19 个 Job 失败，15 次 node_error/internal_error、2 次 unexpected_error、2 次 provider_error，没有限流类失败；SSE 断流 34 次全部重连恢复；三个因果能力 causal.cdfm、causal.pc、causal.direct_lingam 共 181/176/46 次调用全部完成。cdfm_fixed 与 cdfm_rate 专项未执行。

压测采集器修复三项：主库核对 SQL 排序规则冲突（D1）、SSH 回执超时误判（D2）、从库复制状态查询缺密码（D6），均在分支 fix(loadtest)--fix-loadtest-bugs 的提交 252005c3 及后续补丁中处理。
