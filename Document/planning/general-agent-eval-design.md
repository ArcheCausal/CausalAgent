# 通用 Agent 因果发现评测系统（general-agent-eval）设计

**文档职责**：定义 `general-agent-eval` 子项目的目标、边界、架构、运行流程与验收标准。

**适用范围**：在 CausalAgent3 仓库内新增的通用 Agent 评测工具；不涉及 CausalAgent 服务端、数据库、前端与现有 `evaluation-runner` 的修改。

---

## 1. 背景与目标

现有 `~/agent-test` 已能评测"Claude Code CLI + DeepSeek"在 `cdfm_bench_rep00` 上的因果发现能力，但存在三个移植问题：

1. 依赖宿主机 Python、特定路径（`/home/yuki/...`）、WSL 与 Windows 两套进程模型；
2. 数据集与基线结果存放在 `CausalAgent3/evaluation-runner` 内，换机器不可用；
3. Agent 使用的算法库来自宿主机 Python，无法保证跨机器版本一致，且 PATH 注入可被绝对路径解释器绕过。

目标：做成仓库内自包含的 Python CLI 子项目，一条命令完成"选择机制/D/N → 在隔离环境运行 Agent → 解析评分 → 输出报告"，可在多台机器复现。

## 2. 非目标

- 不提供 Web UI、PDF 或图表报告；
- 不执行 CausalAgent 基线，只读取既有基线 CSV；
- 不修改 CausalAgent3 根 `requirements.txt`、Dockerfile、compose、`evaluation-runner`；
- 不做多 Agent 协作、RAG、MCP 工具扩展；
- v1 只实现 Docker 运行时；native（WSL/Linux）作为后续版本，但运行时接口从 v1 就按可替换设计。

## 3. 关键决策

| 决策 | 结论 |
| --- | --- |
| 交付形态 | 自包含 Python CLI 包，位于 `CausalAgent3/general-agent-eval/` |
| 安装方式 | `pipx install ./general-agent-eval` 或 `uv tool install`；也可 `python -m general_agent_eval` |
| Agent 运行时 | **每集一个 Docker 容器**，容器内只有一套锁定 Python + 算法库 + Linux 版 Claude Code CLI |
| 运行时范围 | v1 仅 Docker；native 运行时预留接口，后续实现 |
| 环境隔离 | 以镜像为基线；运行期 `pip install` 允许，但随容器丢弃，天然无跨集漂移 |
| 数据分发 | 首次运行下载数据集与基线到用户缓存，sha256 校验；URL 可配置 |
| 运行范围 | 无默认；机制、D、N 必须显式多选，非交互环境缺参即报错 |
| 并发 | v1 串行 `jobs=1`；`jobs>1` 为后续高级特性 |
| 报告 | CSV + Markdown，沿用现有 `results/summary/report/trace/judge` 口径 |
| 版本策略 | 镜像 tag/digest、容器内 CLI 版本写入报告；与配置不一致时警告但继续 |
| 进程清理 | 超时 `docker kill`，无需处理 Windows/Linux 两套进程树 |

## 4. 仓库位置与包结构

新增目录 `CausalAgent3/general-agent-eval/`，运行时不 import 仓库内其它模块，可整体拷贝出仓库使用。

```
general-agent-eval/
├─ pyproject.toml            # 依赖：numpy、causal-learn、PyYAML；入口 general-agent-eval
├─ README.md                 # 安装、setup、run、故障排查
├─ config.example.yaml
├─ AGENTS.md                 # 本地实施约束
├─ docker/
│  ├─ Dockerfile
│  ├─ agent-entrypoint.sh
│  └─ toolbox.lock           # 算法库精确版本
├─ src/general_agent_eval/
│  ├─ __init__.py / __main__.py
│  ├─ cli.py                 # 子命令与参数
│  ├─ config.py              # 默认值 + 配置文件 + 环境变量
│  ├─ preflight.py           # Docker/镜像/数据/key 检查
│  ├─ selector.py            # 机制、D、N 多选与校验
│  ├─ data.py                # 下载/解包/扫描/加载/写 CSV/金标准
│  ├─ metrics.py             # F1、SHD、骨架指标（内嵌自 evaluate.py）
│  ├─ container.py           # docker argv 构造、启动、超时终止、清理
│  ├─ runner.py              # 单集编排：工作区、提示词、调用、状态
│  ├─ parse.py               # 矩阵与算法名解析
│  ├─ audit.py               # 泄漏审计
│  ├─ report.py              # summary/report 生成
│  ├─ trace.py               # 工具调用轨迹
│  └─ judge.py               # 可选过程裁判
└─ tests/
```

评测程序自身依赖 `numpy`、`causal-learn`、`PyYAML`，只用于扫描、打分和报告；与容器内 Agent 环境完全分离。

## 5. 运行流程

### 5.1 子命令

```bash
general-agent-eval setup      # 检查 Docker、构建/拉取镜像、下载数据与基线
general-agent-eval doctor     # 自检并输出诊断；可选 --probe-agent 做一次最小真实调用
general-agent-eval run        # 交互多选后运行
general-agent-eval resume     # 续跑最新或 --run 指定目录，跳过成功集
general-agent-eval report     # 从 results.csv 重新生成 summary/report/trace
general-agent-eval shell      # 进入保留的容器调试：shell --run DIR --case NN
general-agent-eval selftest   # 离线纯函数自检
general-agent-eval smoke      # 真实跑 1 集，验证端到端（收费，需显式调用）
```

### 5.2 选择

- `--mechanisms`、`--dims`、`--ns` 接受逗号分隔或重复传参；`all` 表示显式全选。
- TTY 下缺参时进入交互式多选：从数据集索引列出机制、D、N，输入编号多选。
- 非 TTY 且缺参：报错并打印示例，不启动任何运行。
- `--per-cell` 默认 1，`--seed` 默认 `20261030`。
- 选定后打印：集数、预计耗时与成本区间；交互确认后开跑，`--yes` 可跳过确认。

### 5.3 数据流

1. `setup` 下载数据集归档与基线 CSV 到 `~/.general-agent-eval/cache/`（Windows 为 `%USERPROFILE%\.general-agent-eval\cache\`），校验 sha256；支持 `--data-dir` 指向本地已有目录。
2. `run` 扫描数据集索引，按机制/D/N/seed/per_cell 选出 specs。
3. 每集建立工作区 `runs/<run_id>/cases/case-NN/`，写入 `data.csv` 与空 `answer/`、`.harness/`。
4. 启动容器，把该 case 目录挂载到 `/work`；只有 case 目录可见，数据集缓存与基线不进容器。
5. CLI 的 stream-json 输出写到 `raw/<dataset>.jsonl`。
6. 容器退出后，宿主机解析 `answer/adjacency.csv`、`answer/algorithm.txt`，打分并追加 `results.csv`。
7. 全部结束后生成 `summary.csv`、`report.md`、`trace.csv`，可选 `judge.jsonl`。

宿主机**只在容器退出后**读取 case 文件；运行中不读 `answer/`，避免读到半写入的 CSV。默认 `--jobs 1`，并行时每集独立容器与独立 case 目录。

### 5.4 运行目录

```
runs/<run_id>/
├─ run.json          # 元数据（见 8.2）
├─ results.csv
├─ summary.csv
├─ report.md
├─ trace.csv
├─ judge.jsonl
├─ raw/<dataset>.jsonl
└─ cases/case-NN/{data.csv,answer/,.harness/,case.json}
```

`case.json` 是该集的权威状态（status、attempt、指标、结束时间），先写临时文件再原子 rename；`results.csv` 是它的投影，崩溃后可由 `case.json` 重建。`resume` 按 `case.json` 状态跳过已计分集，不靠文件是否存在判断。`cases/` 默认保留以便复现；`--no-keep-cases` 可在成功后删除。每集的 `.harness/pre.json`、`post.json` 是该集的环境证据，随 case 一起保留。

## 6. 容器设计

### 6.1 镜像

- 基础：**单镜像** `python:3.11-slim-bookworm`，在同一 Debian 版本上安装 Node 20 与 Python 工具链，避免跨镜像复制 Node 二进制的 ABI/段错误；v1 用镜像体积换稳定性；
- 安装固定版本 Claude Code CLI（`ARG CLAUDE_CODE_VERSION`）与 `toolbox.lock` 中的算法库；
- 非 root 用户 `agent`；镜像内无 API key、无数据集、无仓库代码；
- 构建源可配：pip 与 npm 均支持国内镜像参数；
- tag 形如 `general-agent-eval/agent:py311-toolbox-v1`，报告记录 `docker image inspect` 的镜像 ID 与 RepoDigest。

`toolbox.lock` 至少包含：causal-learn、lingam、pgmpy、scikit-learn、pandas、numpy、scipy、networkx、statsmodels。版本以当前已验证环境为基准锁定。

### 6.2 单集启动

```text
docker run --name gae-<run_id>-<nn>
  --label gae.run=<run_id> --label gae.case=<nn>
  [Linux 宿主机加 --user <uid>:<gid>]
  --cpus <n> --memory <size> --pids-limit <n> --shm-size <size>
  -v <case_host_path>:/work -w /work
  -e ANTHROPIC_BASE_URL -e ANTHROPIC_AUTH_TOKEN -e ANTHROPIC_MODEL
  -e PIP_INDEX_URL
  -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -e MKL_NUM_THREADS=1
  <image> claude -p <prompt> --model <model>
    --output-format stream-json --verbose --dangerously-skip-permissions
    --strict-mcp-config --disallowedTools WebSearch,WebFetch
```

- **不使用 `--rm`**：容器退出后先 `docker inspect` 读取 `State.ExitCode`、`State.OOMKilled`、起止时间，再 `docker rm`。失败时默认保留容器供 `general-agent-eval shell` 调试，由 `--keep-container-on-failure` 控制；成功时总是删除。
- 每集一个新容器；运行期安装随容器删除，下一集回到镜像基线。
- 超时采用两层：业务预算 `timeout_s`（默认 600s）到点先 `docker kill -s TERM` 让入口脚本转发信号并落 post；再等 30s 宽限期，仍未退出则 `docker kill -s KILL`。`docker run` 的外部硬超时设为 `timeout_s + 35s`，防止 Docker 自身挂死。启动前按 `gae.run=<run_id>` 标签清理残留容器。
- 宿主机信号：捕获 SIGINT/SIGTERM，在 `finally` 中按标签清理当前 run 的容器，并保留已落盘的 `case.json`。
- 退出码解释：容器内 `claude` 的退出码透传；`125/126/127` 为 Docker 自身或启动失败；`137` 为 SIGKILL（结合 `OOMKilled` 与耗时区分超时/OOM）；`143` 为 SIGTERM。
- 权限：Linux 宿主机传 `--user <uid>:<gid>`；case 目录及 `answer/`、`.harness/` 由宿主机预建并确保可写。`doctor` 执行真实挂载探针：容器写探针文件，宿主机读取并删除，失败则阻止运行。
- `container.py` 的 argv 构造是纯函数，便于单测；平台差异（是否传 `--user`、路径格式、资源参数）由入参决定。

### 6.3 环境证据

入口脚本 `agent-entrypoint.py`（作为容器 PID 1，负责信号转发）：

1. 启动前记录 `claude --version`、`python3 -V`、`pip freeze` 到 `/work/.harness/pre.json`；
2. 以子进程运行 `claude`，透传 stdout/stderr；收到 SIGTERM 时转发给 claude，等待其退出；
3. claude 退出后记录 `post.json`，并以 claude 的退出码退出。

超时被 SIGKILL 时只可能有 pre 记录，报告标注 "环境证据不完整"。不要用 shell 的 `exec claude`，否则退出后无法写 post。

### 6.4 网络与密钥

- 容器默认 bridge 网络，需要访问模型端点；允许 pip 安装，因此需能访问配置的镜像源。
- API key 通过 `-e` 传入，不写入镜像、不写入工作区、不写入报告；注意本机其他用户可通过 `docker inspect` 看到容器环境变量，共享主机上需限制 Docker 权限或改用更严格的 secret 注入。
- 可选增强（后续）：仅放行模型端点与镜像源的出网策略。

## 7. 选择、执行与状态

- 状态：`success`、`timeout_partial`（有矩阵）、`timeout`、`invalid_answer`、`invalid_leak`、`claude_error`。
- 重试：`claude_error` 且退出耗时 <10s 时默认重试 1 次（`--retries` 可调）；超时、无效答案、泄漏不重试；`results.csv` 增加 `attempt` 列。
- 非超时的 Docker 层失败归入 `claude_error`，`reason` 区分 `docker_error`（125/126/127）和 `docker_oom`（137 + `OOMKilled`）；超时仍走 `timeout`/`timeout_partial`，并记录是人为 kill。容器输出完整落 `raw/`，便于排查。
- Docker 不可用、镜像缺失或数据校验失败时，`setup`/`doctor` 给出可操作的错误信息，不进入运行。
- 提示词沿用现行轻度版：先规划、轻量分析、每评估一种算法立即覆盖写 `answer/adjacency.csv`、最终算法写 `answer/algorithm.txt`、10 分钟预算。
- 泄漏审计：容器只挂 case 目录，天然看不到金标准；审计词调整为不含容器的合法路径（如 `/home/agent`），保留 `cdfm_bench`、`causalagent3`、`rep00`、数据集缓存绝对路径等。
- 工具调用与算法识别沿用现有 `trace.csv` 逻辑。

## 8. 解析、评分与报告

### 8.1 解析

- 只认 `answer/adjacency.csv` 与 `answer/algorithm.txt`；聊天文本不作为评分依据。
- 矩阵解析容忍：纯 CSV、markdown 代码块包裹、前后解释文字、`X0` 表头、`true/false`、多余空白；校验形状、0/1、对角线。
- `algorithm.txt` 取第一行非空内容作为 `final_algorithm`，用于报告展示。

### 8.2 元数据 `run.json`

```json
{
  "run_id": "...",
  "started_at": "...",
  "finished_at": "...",
  "selection": {"mechanisms": [], "dims": [], "ns": [], "per_cell": 1, "seed": 20261030},
  "runtime": {"mode": "docker", "image": "...", "image_id": "...", "docker_version": "..."},
  "agent": {"claude_version": "...", "model": "deepseek-chat", "base_url": "..."},
  "toolbox": {"lock_sha256": "...", "pre_freeze_sha256": "..."},
  "data": {"dataset_sha256": "...", "baseline_sha256": "..."},
  "host": {"os": "...", "python": "..."},
  "env_drift_cases": []
}
```

`env_drift_cases` 记录 pre/post freeze 不一致的 case；运行期安装允许，但必须可见。

### 8.3 报告

- 总体：有向 F1、SHD、有向 P/R、骨架 P/R，与基线按 dataset 匹配后并排；样本不一致时标注。未配置基线时跳过基线列并明确说明。
- 基线口径：只对 agent 的计分集求基线均值；同一 dataset 多行时，优先 `success`/`timeout_partial` 且有 `f1` 的行，仍有重复取最后一行，失败行不参与匹配。
- 分格表：D×N 的 F1/SHD/骨架指标、计分集数、状态分布。
- 分机制表：Agent vs 基线，ΔF1。
- 失败清单：状态、原因、耗时、算法。
- 算法选择：`final_algorithm` 分布与按机制的众数。
- 环境与成本：镜像 digest、CLI 版本、token、估算成本、耗时、环境漂移 case。
- `invalid_leak` / `invalid_answer` / `timeout` / `claude_error` 不计入均值；`timeout_partial` 计入并单列。

## 9. 跨平台

- 支持宿主机：Windows（Docker Desktop）与 WSL/Linux（dockerd 或 Docker Desktop 集成）。
- `doctor` 用真实检查验证挂载：启动一次性容器，在 `/work` 写入探针文件，再由宿主机读取并删除。
- 路径规则：工作区放在宿主机可被 Docker 挂载的路径；Windows 用盘符路径，WSL 用 `/mnt/...` 或 WSL 路径，由平台层决定传给 `docker -v` 的形式。
- 容器内始终是 Linux；宿主机预装的 `claude` 与评测无关。

## 10. 配置

`~/.general-agent-eval/config.yaml`（`--config` 可覆盖），示例：

```yaml
cache_dir: ~/.general-agent-eval/cache
workspace_root: ~/.general-agent-eval/work
output_dir: ~/.general-agent-eval/runs

download:
  dataset_url: ""          # 占位，待填
  dataset_sha256: ""
  baseline_url: ""
  baseline_sha256: ""

agent:
  image: general-agent-eval/agent:py311-toolbox-v1
  base_url: https://api.deepseek.com/anthropic
  model: deepseek-chat
  auth_env: DEEPSEEK_API_KEY
  timeout_s: 600
  retries: 1
  jobs: 1
  keep_container_on_failure: true
  pip_index_url: https://mirrors.aliyun.com/pypi/simple/
  resources:
    cpus: 4
    memory: 8g
    pids_limit: 512
    shm_size: 1g

judge:
  enabled: false           # v1 存根，默认关闭；先保证因果图主打分链路
  model: deepseek-chat
  url: https://api.deepseek.com/chat/completions

# 基线 CSV 本地路径（绝对路径或 ~ 开头）；空 = 不启用
baseline_results: ""
```

- key 只从环境变量读取；支持 `--env-file`，但仓库内不保存。
- 配置优先级：命令行 > 环境变量 > 配置文件 > 默认值。

## 11. 测试与验收

### 11.1 测试

- 纯函数单测：矩阵解析各类输入、指标固定样例（含 CPDAG/SHD 期望值）、选择校验、容器 argv 构造、sha256 校验、配置优先级。
- 禁止 mock/假实现；需要真实依赖的检查放进 `doctor` 与 `smoke`。
- 导入边界测试：解析包源码 AST，断言不存在 `evaluation_runner` 等仓库模块的 import。
- `selftest` 覆盖离线部分，CI 可执行。
- `smoke` 真实跑 1 集（默认选最小维度的 Linear），需显式调用并确认费用。

### 11.2 验收标准

1. 干净机器（Docker + key）执行 `setup → doctor → smoke` 全部通过。
2. 非 TTY 且未指定机制/D/N 时报错，不产生任何运行目录。
3. 超时后容器被终止，`docker ps` 无 `gae.run` 标签的残留；容器未被 `--rm` 提前销毁，退出码与 `OOMKilled` 已记录。
4. `results.csv` 保留原字段并新增 `attempt`；`report.md` 含元数据与环境证据。
5. 包在移出 CausalAgent3 后仍可安装与运行（无仓库路径依赖）。
6. 同一选择重复运行，镜像 ID、toolbox 哈希、CLI 版本一致；结果允许不同但环境可比。
7. Windows 与 WSL 两种宿主机都能通过 `doctor` 的挂载检查：容器内写探针文件，宿主机可读、可删。
8. 容器实际生效的 CPU、内存、pids、shm 与配置一致（`docker inspect HostConfig` 校验）。
9. Docker 层失败与超时能被区分：`docker_error`、`docker_oom` 写入 `claude_error.reason`，超时仍为 `timeout`/`timeout_partial`，不会与模型自身失败混淆。

## 12. 与现有 harness 的关系

- 逻辑来源：`~/agent-test/{run.py,agent_report.py,tool_trace.py,judge.py,selftest.py}`；
- 迁移方式：拆分为包内模块，提示词、状态机、评分口径、报告字段保持行为等价；
- 旧 harness 保持不动，作为历史结果与对照；新系统用容器 Linux 环境，旧结果是宿主机 Windows 环境，**两者不可逐集直接比较**，报告中分别标注运行环境。

## 13. 待定项

1. 数据集与基线的下载 URL、sha256（先留空；未配置时 `setup` 报错并提示用 `--data-dir`）。
2. `toolbox.lock` 的精确版本（以当前已验证环境为基准，构建镜像时固化）。
3. 镜像分发方式：默认本地构建；是否推内网 registry 待定。
4. Claude Code CLI 的固定版本号（构建时写入 `ARG` 并记录）。
5. Native 运行时（WSL/Linux）：v1 不做。后续实现时与 Docker 共用 `toolbox.lock`、Claude 版本、case 布局与评分；要求 Python 3.11、PATH + PreToolUse hook + 命令/脚本审计，`env_violation` 的 case 不计分；结果默认不与 Docker 合并，需显式 `--merge-runtimes`。
