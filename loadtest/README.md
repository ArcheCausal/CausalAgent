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

# 第二阶段：创建测试账号并记录本地凭据
.venv\Scripts\python.exe -m runner accounts --count 10

# 第四阶段：按档位执行负载
.venv\Scripts\python.exe -m runner stage --stage baseline
.venv\Scripts\python.exe -m runner stage --stage low

# 第五阶段：取消与 SSE 恢复专项
.venv\Scripts\python.exe -m runner recovery

# 整理证据包与核查定位清单
.venv\Scripts\python.exe -m runner evidence
.venv\Scripts\python.exe -m runner verify-packet
~~~

## 服务器侧指标

本客户端只在本地运行，不读取服务器资源。需要服务器侧 CPU、内存、磁盘、swap、
OOM 与容器重启、MySQL 连接数和复制状态时，在服务器上执行只读采集并把结果带回：

~~~
sudo bash loadtest/server_side_collect.sh > /tmp/loadtest-server-<run_id>.json
scp ubuntu@<host>:/tmp/loadtest-server-<run_id>.json loadtest/local/server-side-<date>.json
~~~

采集脚本只读取状态，不修改服务、数据或配置，也不输出任何密钥。它在服务器上通过
bash -n 与 JSON 解析校验后，把结果写入各档运行摘要，再由 evidence 命令渲染进
“服务器侧”一节：

~~~
.venv\Scripts\python.exe -m runner attach-server --from loadtest/local/server-side-<date>.json
.venv\Scripts\python.exe -m runner evidence
~~~

没有执行 attach-server 时，报告把服务器侧各项如实写成“未采集”。

## 负载窗口资源采样

服务器侧的单次快照覆盖不到整个负载窗口，因此每档负载都配一份主机资源采样。
采样脚本只读 /proc，把 CPU jiffies、负载均值、内存与 swap 水位按固定间隔写入 JSON。
采样时长要大于负载时长，因为停止提交后 Job 还会继续执行一段时间。

~~~
scp -i <key> stage_resource_sample.sh ubuntu@<host>:/tmp/_sr.sh
ssh ubuntu@<host> "bash /tmp/_sr.sh 690 10 mid > /tmp/_sr_mid.json"
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
.venv\Scripts\python.exe -m runner attach-server --from loadtest/local/server-side-<date>.json
.venv\Scripts\python.exe -m runner attach-after --from loadtest/local/server-side-<date>-final.json
.venv\Scripts\python.exe -m runner evidence
~~~

报告据此给出“负载前 Job 分布”与“停止后 Job 分布”，可以直接看到队列在停止提交后
是否清空。

## 一键执行一档（负载 + 采样）

ops/ 下的两个脚本把“启动采样、跑负载、等采样收尾、取回并写入摘要”串成一档：

~~~
# 跑一个档位（先确保 stage_resource_sample.sh 已上传为服务器上的 /tmp/_sr.sh）
.venv\Scripts\python.exe ops/stage_sampling.py mid

# 按顺序跑多个档位；不传参数时跑 low 起的全部档位
.venv\Scripts\python.exe ops/run_stages.py low mid_low mid stability recovery burst
~~~

每档的采样秒数在 ops/stage_sampling.py 的 SPECS 中配置，必须大于负载持续时间。
逐档日志写入 local/runs/_orch.log 与 local/runs/_driver.log。运行完成后仍需执行 evidence
与 attach-server 才会把结果反映进报告。

## 主库 Job 终态核对

客户端只记录自己发起的请求；为了独立确认 Job 的最终状态，另有一个只读脚本直接
查询 staging 主库，输出各状态的计数、最近三小时的分布和失败明细：

~~~
sudo bash loadtest/job_db_check.sh > loadtest/local/job-db-<date>.json
~~~

脚本只执行 SELECT，凭据从部署环境文件读取且不写入输出。核对结果由 evidence 命令
渲染进合并报告的“主库 Job 终态核对”一节。这是数据库层证据，与管理员端网页界面
核对相互独立。

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
| local/ | 全部本地产物根目录，整体加入 .gitignore，方便整目录汇总与清理 |
| local/data/ | 生成的测试 CSV |
| local/accounts.local.json | 测试账号与密码 |
| local/runs/ | 每次运行的原始产物 |
| local/evidence/ | 整理后的报告与核查结果，归档到本地和团队知识库 |
| evidence/index.md | 唯一进入版本库的证据链索引：run_id、时间、镜像 digest、归档位置与结论 |
| local/evidence/loadtest-report-<date>.md | 全部档位与专项的合并报告 |
| local/evidence/log-verification.md | 按档位的 job_id、时间窗口与 Request ID 定位清单 |
| local/evidence/logcheck-<档位>.json | 每档的日志侧核查原始结果 |
| local/evidence/cdfm-failure-analysis.md | causal.cdfm 全部失败的定位与仍需在服务器完成的一步 |
| local/evidence/source-catalog-500-analysis.md | GET /api/rag_eval/isolated/source-catalog 返回 500 的定位 |
| local/server-side-<date>.json | 服务器侧只读采集结果 |
| local/server-side-<date>-final.json | 负载结束后的服务器只读采集结果 |
| local/job-db-<date>.json | 主库 Job 终态只读核对结果 |
| loadtest/server_side_collect.sh | 服务器侧单次只读采集脚本 |
| loadtest/stage_resource_sample.sh | 负载窗口主机资源采样脚本 |
| loadtest/job_db_check.sh | 主库 Job 终态只读核对脚本 |
| ops/stage_sampling.py | 一档负载与资源采样串联执行 |
| ops/run_stages.py | 按顺序执行多个档位 |
