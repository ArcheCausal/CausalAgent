# general-agent-eval 分步实施指南（Docker 版 v1）

> 配套文档：`general-agent-eval-design.md`（设计口径，冲突以设计文档为准）。
> 本文件是**操作手册**：按阶段做，每阶段有命令、检查点、常见坑。
> 面向：第一次正经用 Docker 的开发者。日期：2026-11-09。

---

## 0. 怎么读这份文档

### 0.1 优先级图例

| 标记 | 含义 | 缺失后果 |
| --- | --- | --- |
| **A｜必须** | v1 不可省略，漏了结果不可信或不可复现 | 答案错误 / 无法复现 / 数据泄漏 / 无法排障 |
| **B｜建议** | 强烈推荐，时间紧可先手工兜底 | 排障变慢、证据不全、体验差 |
| **C｜推迟** | v1 明确不做，接口留好 | 不影响正确性 |

### 0.2 实施顺序原则

先做**能在本机离线验证**的部分（包骨架、数据选择、解析、评分），再做**依赖 Docker 的部分**（镜像、容器执行）。理由是：解析和评分错了，后面跑多少集都是白跑；而 Docker 出问题时的报错噪音大，不适合同时调业务逻辑。

---

## 1. 优先级总表

### A｜必须（v1 非做不可）

| # | 项目 | 为什么 |
| --- | --- | --- |
| A1 | `docker run` **不加 `--rm`**；先 `inspect` 再 `rm` | 容器一删，`ExitCode`/`OOMKilled` 全部丢失，你无法区分"模型超时"和"容器被杀" |
| A2 | 所有容器打 `gae.run=<run_id>` / `gae.case=<nn>` 标签；Ctrl-C / SIGTERM 时按标签清理 | 否则中断一次就留下僵尸容器，占内存、占名字、下次 `--name` 冲突 |
| A3 | 两层超时：业务 600s → SIGTERM → 30s 宽限 → SIGKILL；宿主侧硬超时 635s | 只有一层 `docker run` 超时会导致：要么杀得太早丢结果，要么杀不掉一直挂着 |
| A4 | 真实的挂载探针（mount probe） | "目录挂进去了"不能靠猜；必须在容器内读写验证，否则 `answer/` 写到容器里，宿主拿不到 |
| A5 | Linux 下 `--user $(id -u):$(id -g)` | 否则 `/work` 里的文件归 root，宿主读不了、删不掉，下一集 `rmtree` 失败 |
| A6 | `run.json` 证据链：镜像 digest、Claude CLI 版本、`toolbox.lock` 哈希、数据集 sha256、宿主信息、起止时间 | 换台机器结果不一样时，没有这些就只能重跑 |
| A7 | `pre.json` / `post.json`：容器内 `claude --version`、`python3 -V`、`pip freeze`；后置缺失要显式标"环境证据不完整" | 事后证明"用的是这套库、这个 CLI"，而不是"我以为用的是" |
| A8 | 数据集与金标准**绝不挂进容器**；容器只挂当前 case 目录 | 反过来就是直接送答案，审计也救不回来 |
| A9 | 状态语义严格：`success` / `timeout_partial` / `timeout` / `invalid_answer` / `invalid_leak` / `claude_error` / `docker_error` / `docker_oom` | 混用状态会让报告均值不可解释 |
| A10 | 旧 Windows harness 结果与新 Docker 结果**不可直接比较**；报告里写明 | 解释器/库/CLI 全变了，混着算均值是数据造假 |
| A11 | `parse.py` 覆盖噪声：表头、`X0` 前缀、`true/false`、多余列、对角线非零、非 0/1 值、缺行 | 实际跑过的 210 集里这些全都出现过 |
| A12 | 评分与 `evaluation-runner/evaluate.py` 做**对拍测试**（同一输入同分数） | 自己重写的 SHD/F1，差一点就整份报告作废 |
| A13 | `resume` 按 `case.json` 的**状态**判断，不按"文件是否存在" | 空文件、半截文件、上次失败的 JSON 都会骗过文件存在性检查 |
| A14 | 密钥只从环境变量进，不进 config、不进镜像、不进报告、不进 `case.json` | `docker inspect` 能直接看到环境变量明文，报告/日志里更不能有 |
| A15 | 共享机器上跑要在报告里提示"容器内进程可访问宿主网络" | 降低预期，别让人以为天然沙箱 |
| A16 | `--dangerously-skip-permissions` 必须有注释说明原因 | 这是承认风险，不是默认好习惯 |
| A17 | 失败分类：业务失败（答案差）/ CLI-API 失败（额度、限流、退出码）/ 基础设施失败（Docker、磁盘、权限） | 三类混在一起，你永远不知道该改提示词还是改容器 |
| A18 | `smoke` 花钱，必须显式 `--yes` | 防止手滑跑掉一整批 |
| A19 | 测试分三层：`selftest`（离线、免费、CI 必过）/ `doctor`（真环境、免费）/ `smoke`（真调用、付费） | 免费的把关拦住 90% 的低级错误 |
| A20 | `toolbox.lock` 里的包**必须在容器内 import 成功**才算装好 | 写了 lock 不等于装上了；`lingam` 装不上是常见情况 |
| A21 | 单镜像：Python + Node 装在同一 base 上，不用多阶段 | 多阶段在 ABI / glibc 上踩坑概率高，v1 用镜像体积换稳定性 |

### B｜建议（做起来不亏）

| # | 项目 | 收益 |
| --- | --- | --- |
| B1 | 答案只在容器退出后由宿主读取，容器内不写 `.harness/` 以外的宿主路径 | 避免半截文件被当成最终答案 |
| B2 | `--cpus` / `--memory` / `--pids-limit` / `--shm-size` | 防止一集把整机拖死；也防止 OOM 时连带杀掉你的编辑器 |
| B3 | 失败时 `--keep-container-on-failure` + `shell` 子命令 | 出问题时能进去看现场，而不是靠猜 |
| B4 | `docker inspect` 结果落盘到 `raw/<dataset>.inspect.json` | 事后复盘不用重跑 |
| B5 | 每集 `case.json` 原子写（写临时文件 + `os.replace`） | 断电/中断不会留下半个 JSON |
| B6 | 日志里对 API key 做脱敏（长度 > 20 的疑似 token 打码） | 防手滑粘贴进 issue |
| B7 | `trace.csv` 记录每轮工具调用 | 分析"为什么这集失败"时唯一的一手材料 |
| B8 | 报告里标注每格实际计分集数 vs 计划集数 | 样本不一致时读者能自己判断 |

### C｜推迟（v1 明确不做）

| # | 项目 | 推迟理由 |
| --- | --- | --- |
| C1 | `--jobs > 1` 并发 | 串行先跑通；并发会让超时、资源、日志全部复杂一档 |
| C2 | 原生（WSL/Linux 非容器）运行时 | 接口留 `runtime: docker` 字段，实现留空 |
| C3 | 镜像推 registry / 多机分发 | v1 每台机器本地 `docker build` |
| C4 | 网络策略（白名单代理）/ 密钥注入（vault） | v1 用宿主网络 + 环境变量 |
| C5 | LLM judge | v1 保留 stub，`enabled: false`；评分只认矩阵 |
| C6 | 自动下载 URL 的正式地址 | 先用占位符 + sha256 校验路径跑通 |

---

## 2. 开工前必须理解的 5 个 Docker 概念

看不懂后面的命令，多半是这 5 个没分清：

1. **镜像（image）vs 容器（container）**：镜像是只读模板，容器是它跑起来的实例。改容器里的东西，除非 `commit`，否则 `docker run` 一次新的就没了——这正是我们要的"运行时装包不跨集漂移"。
2. **绑定挂载（bind mount）**：`-v 宿主路径:容器路径`，两边是同一份文件。宿主的 uid/gid 原样映射进容器，所以容器里用 root 写文件，宿主上就是 root 所有。
3. **PID 1**：容器里的第一个进程。它负责接收 `docker stop` 的 SIGTERM 并转发给子进程。`shell` 当 PID 1 经常不转发信号，所以我们要用 Python 入口当 PID 1。
4. **退出码**：`125` = docker 自己失败（参数错、镜像不存在）；`126/127` = 命令不可执行/找不到；`137` = 被 SIGKILL（常是 OOM）；`143` = 被 SIGTERM。**这些和模型的行为无关**，必须单独归类。
5. **`docker inspect`**：容器元数据的唯一真相来源，`State.ExitCode`、`State.OOMKilled`、`State.StartedAt` 都在里面。所以**不能 `--rm`**。

---

## 3. 阶段 0：环境准备

### 3.1 选一种 Docker 后端

| 方案 | 适用 | 注意 |
| --- | --- | --- |
| Docker Desktop (Windows) | 只在 Windows 上用 | 需要开启 WSL2 集成；路径要共享 |
| WSL 内独立 dockerd | 在 WSL 里开发、跑脚本 | 每个 WSL 发行版各有一套 daemon，别和 Desktop 混用 |

**不要两个同时用**，否则 `docker ps` 看到的是不同的容器集合，会怀疑人生。

### 3.2 验证命令

```bash
docker version
docker info | head -20
docker run --rm hello-world
```

检查点：
- `docker version` 的 Client 和 Server 都有输出（只有 Client = daemon 没起来）；
- `docker run` 能拉下 hello-world 并打印成功。

### 3.3 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `permission denied ... /var/run/docker.sock` | 当前用户不在 `docker` 组 | `sudo usermod -aG docker $USER` 后重新登录 |
| `Cannot connect to the Docker daemon` | daemon 没启动 | Windows 开 Docker Desktop；WSL 里 `sudo service docker start` |
| 拉镜像极慢或超时 | 国内网络 | 配镜像加速器（改 `daemon.json` 的 `registry-mirrors`） |
| `no space left on device` | 镜像/容器堆积 | `docker system df` 看占用，`docker system prune` 清理（先确认没有要保留的容器） |

> 你之前的记忆里记过：容器内访问国外源受限，需要国内镜像源。镜像构建阶段同样适用，`pip` 用 `PIP_INDEX_URL`，apt 换国内源。

---

## 4. 阶段 1：包骨架 + `selftest`（离线，不碰 Docker）

### 4.1 建目录

```text
CausalAgent3/general-agent-eval/
├── pyproject.toml          # console script: general-agent-eval
├── README.md
├── src/general_agent_eval/
│   ├── cli.py              # 子命令分发
│   ├── config.py           # 读 config、默认值、路径展开
│   ├── selector.py         # D×N×机制 的多选
│   ├── data.py             # 缓存、sha256、下载（v1 先只读本地）
│   ├── metrics.py          # precision/recall/F1/SHD/skeleton
│   ├── parse.py            # adjacency.csv 解析
│   ├── audit.py            # 泄漏审计
│   ├── runner.py           # 编排：选→跑→评→写
│   ├── container.py        # 只在这一个文件里出现 docker 命令
│   └── report.py           # CSV + Markdown
├── docker/
│   ├── Dockerfile
│   ├── agent-entrypoint.py
│   └── toolbox.lock
└── tests/
    ├── test_parse.py
    └── test_metrics.py
```

### 4.2 先写 `metrics.py` 和 `parse.py`

顺序上先做这两个，因为它们**不需要 Docker、不需要网络、不需要密钥**，可以立刻验证对错。

`parse.py` 必须处理的输入（全部来自真实跑过的数据）：

| 输入形态 | 期望 |
| --- | --- |
| 第一行 `,X0,X1,...` 表头 | 跳过 |
| 每行以 `X0,X1,...` 开头 | 去掉行首标签 |
| `true/false` | 转 1/0 |
| 数字带空格 / 制表符 / 分号 | 都能切分 |
| 列数 > dim | 截断到 dim |
| 行数 != dim | 返回 `shape` 错误，不猜 |
| 对角线非零 | 返回 `diagonal` 错误 |
| 出现 0/1 之外的值 | 返回 `values` 错误（不做阈值化） |

**不要自作聪明做阈值化**：模型应该自己输出 0/1；悄悄阈值化会把"格式错误"变成"看起来还行的分数"，掩盖问题。

### 4.3 评分对拍

`metrics.py` 不要"重写一个更好的"。做法是从 `evaluation-runner/evaluate.py` 抄算法，然后写一个对拍测试：

```python
# tests/test_metrics.py 的核心断言
# 同一批 (pred_edges, gold_edges) 输入，本地实现与参考实现分数完全一致
assert ours["precision"] == ref["precision"]
assert ours["f1"] == ref["f1"]
assert ours["shd"] == ref["shd"]
```

SHD 依赖 CPDAG（`causal-learn` 的 `dag2cpdag`），这一处尤其容易差一格。

### 4.4 检查点

```bash
cd CausalAgent3/general-agent-eval
python -m pytest tests/ -q          # 全绿
python -m general_agent_eval selftest   # 输出 OK，0 次网络/0 次 docker 调用
```

`selftest` 的内容：解析所有噪声样例、对拍评分、校验配置默认值、校验"未选机制时直接报错退出"。

---

## 5. 阶段 2：数据与选择

### 5.1 缓存布局

```text
~/.general-agent-eval/cache/
└── cdfm_bench_rep00/
    ├── manifest.json          # 版本、sha256、来源 URL
    └── <机制>/D010_N500/      # 数据文件
```

### 5.2 规则

- **A**：下载后必须校验 sha256，不匹配就中止，不要"先跑着看看"。
- **A**：`manifest.json` 记录来源与时间，写进 `run.json`。
- **A**：**没有默认选择**。`run` 不带 `--mechanisms` / `--dims` / `--ns` 时打印可用清单然后退出（退出码非 0）。
- 金标准只在宿主侧读取，永远不进容器（对应 A8）。

### 5.3 检查点

```bash
general-agent-eval run --config config.yaml
# 期望：打印可选的机制/D/N 清单，退出码 1，不启动任何容器

general-agent-eval run --mechanisms Linear,CPT --dims 10 --ns 500 --dry-run
# 期望：只列出选中的集，不执行
```

---

## 6. 阶段 3：镜像与工具箱

### 6.1 Dockerfile 要点

```dockerfile
FROM python:3.11-slim-bookworm

# 国内 apt 源（构建期）
# Node 20 + Claude Code CLI 装在同一镜像里
# 非 root 用户 agent

COPY docker/toolbox.lock /tmp/toolbox.lock
RUN pip install --no-cache-dir -r /tmp/toolbox.lock \
 && python -c "import lingam, pgmpy, causallearn, sklearn, numpy, pandas"

COPY docker/agent-entrypoint.py /usr/local/bin/agent-entrypoint.py
ENTRYPOINT ["python3", "/usr/local/bin/agent-entrypoint.py"]
```

关键点：

| 要求 | 原因 |
| --- | --- |
| `pip install` 后面紧跟 **import 自检** | A20：装没装上不能靠"命令没报错" |
| 单镜像装 Python + Node | A21：避免多阶段 ABI 问题 |
| 固定 base tag（`3.11-slim-bookworm`） | 至少同一台机器重构建基线一致 |
| 构建完记录 digest | A6：`docker inspect --format '{{index .RepoDigests 0}}' <image>` |
| 非 root 用户 | 配合 A5，容器内默认不 root 更安全 |

### 6.2 `toolbox.lock`

只放**成品包 + 精确版本**，不放"最新版"：

```text
numpy==1.26.4
pandas==2.2.2
scikit-learn==1.4.2
causal-learn==0.1.3.8
lingam==1.8.3
pgmpy==0.1.25
```

> 版本号要按实际装成功的填。`lingam` 对 numpy 版本敏感，先在小容器里试装再写死。

### 6.3 构建与验证

```bash
docker build -t general-agent-eval/agent:py311-toolbox-v1 -f docker/Dockerfile .
docker run --rm general-agent-eval/agent:py311-toolbox-v1 \
  python -c "import lingam, pgmpy, sklearn; print('ok')"
docker run --rm general-agent-eval/agent:py311-toolbox-v1 claude --version
```

检查点：
- 两次 `docker run` 都打印成功；
- `claude --version` 有版本号（记下来，写进 `run.json`）；
- `docker images` 能看到 tag 和大小。

### 6.4 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `pip install` 卡住 / SSL 错误 | 没配国内源 | 构建时 `PIP_INDEX_URL`，或在 Dockerfile 里 `pip config set global.index-url` |
| `lingam` 装完 import 报 numpy 版本冲突 | 依赖不兼容 | 放宽/锁定 numpy，重试到 import 通过 |
| `claude: not found` | Node 未装或 PATH 未包含 | 用绝对路径或显式 `ENV PATH` |
| `exec format error` | 在 ARM 上跑了 x86 镜像（或反之） | `docker build --platform` 或换 base |

---

## 7. 阶段 4：容器执行器（最容易出错的一步）

`container.py` 是**唯一**允许拼 `docker` 命令的地方。其余模块不知道 Docker 存在。

### 7.1 启动命令模板

```text
docker run
  --name gae-<run_id>-<nn>
  --label gae.run=<run_id>
  --label gae.case=<nn>
  [--user <uid>:<gid>]              # Linux 必加；Windows Docker Desktop 可省
  --cpus 2 --memory 4g --pids-limit 512 --shm-size 512m
  -v <case_dir>:/work -w /work
  -e ANTHROPIC_BASE_URL -e ANTHROPIC_AUTH_TOKEN -e ANTHROPIC_MODEL
  -e PIP_INDEX_URL
  -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -e MKL_NUM_THREADS=1
  <image>
  claude -p <prompt> --model <model> --output-format stream-json --verbose
         --dangerously-skip-permissions --strict-mcp-config
         --disallowedTools WebSearch,WebFetch
```

**A1：不要 `--rm`。**
**A16：`--dangerously-skip-permissions` 旁边写明"容器内无敏感挂载，仅此 case 目录可写"。**

### 7.2 挂载探针（A4）

跑正式任务前，先用同一套挂载参数跑一个探测容器：

```bash
docker run --rm --user $(id -u):$(id -g) -v <case_dir>:/work -w /work <image> \
  sh -c 'echo probe > /work/.harness/mount-probe && cat /work/.harness/mount-probe'
```

然后在宿主侧确认 `case_dir/.harness/mount-probe` 存在且内容一致。**探针失败就中止整批**，不要"跑一集看看"。

### 7.3 两层超时（A3）

```text
宿主计时开始
  ├─ 到 600s：docker kill -s TERM <name>   （业务超时）
  ├─ 再等 30s：docker kill -s KILL <name>  （宽限结束）
  └─ 到 635s：宿主侧强制放弃本次 wait      （硬超时，防止卡死）

容器退出后：先 inspect，再决定 rm
```

优先级：`timeout_partial`（有答案矩阵）> `timeout`（无矩阵）。也就是说，**超时不等于没有结果**，只要容器在 TERM 之前把 `answer/adjacency.csv` 写出来了，就算 `timeout_partial`，计入均值但单列标注。

### 7.4 信号清理（A2）

宿主进程捕获 `SIGINT` / `SIGTERM`：

```text
on signal:
  docker ps -aq --filter label=gae.run=<run_id> | xargs -r docker kill -s TERM
  sleep(10)
  docker ps -aq --filter label=gae.run=<run_id> | xargs -r docker kill -s KILL
  落盘当前进度 → 退出
```

用标签清理而不是记容器名列表：中断时你可能已经不在那个调用栈里了。

### 7.5 退出码分类（A9 + A17）

| 观察 | 归类 | 是否计分 |
| --- | --- | --- |
| exit 0 且矩阵有效 | `success` | 是 |
| 超时且有矩阵 | `timeout_partial` | 是 |
| 超时且无矩阵 | `timeout` | 否 |
| exit 125/126/127 | `docker_error` | 否 |
| exit 137 且 `OOMKilled=true` | `docker_oom` | 否 |
| exit 非 0（其他）且无矩阵 | `claude_error` | 否 |
| 命中泄漏词 | `invalid_leak` | 否 |
| 有矩阵但格式非法 | `invalid_answer` | 否 |

**先判 Docker 层，再判业务层**：容器根本没起来时去解析 `adjacency.csv` 是浪费时间。
另外：`OOMKilled=true` 时即使有矩阵也标 `docker_oom`——那可能是写了一半的矩阵。

### 7.6 重试策略

`retries: 1` **只对快速失败**生效（例如 2 秒内 exit 1，典型的 CLI 崩溃）。超时不重试（10 分钟一次，重试太贵）。重试次数记进 `case.json`。

### 7.7 检查点

```bash
general-agent-eval run --mechanisms Linear --dims 10 --ns 500 --limit 1
```

跑一集后检查：
- `docker ps -a` 里有一个 `gae-*` 容器（未删除）；
- `raw/<dataset>.inspect.json` 存在，`State.ExitCode` 是 0；
- `case-NN/answer/adjacency.csv` 在**宿主**上可见（证明挂载是对的）；
- `results.csv` 有一行，状态合理。

确认后再 `docker rm` 或让下一阶段统一清理。

---

## 8. 阶段 5：解析、评分、审计

### 8.1 顺序

容器退出 → `inspect` → 读 `answer/adjacency.csv` → `parse` → `audit` → `score` → 写 `case.json` → 更新 `results.csv`。

### 8.2 审计（A8 的兜底）

扫描容器输出里的工具调用参数，出现 `cdfm_bench` / `rep00` / 上级目录路径等词就记 `invalid_leak`。

注意两个真实教训：
- `.npz` **不要**作为审计词——模型自己存中间结果会误报；
- 目录名用中性的 `case-NN`，不要用带机制名的路径，否则模型顺着目录名就能猜到答案。

### 8.3 检查点

用一集真实产物（或手工构造的假产物）跑 `report`，确认：
- 解析出的边数与 `n_pred_edges` 一致；
- 与参考实现分数一致；
- 故意写一个非法矩阵，得到 `invalid_answer` 而不是 0 分计入。

---

## 9. 阶段 6：运行目录、`case.json`、`resume`

### 9.1 目录

```text
<output>/runs/<run_id>/
├── run.json            # 镜像 digest、CLI 版本、lock 哈希、数据 sha、宿主信息、开始/结束
├── results.csv         # 每集一行的投影
├── summary.csv
├── report.md
├── trace.csv
├── raw/<dataset>.jsonl
├── raw/<dataset>.inspect.json
└── cases/case-NN/
    ├── data.csv
    ├── answer/
    ├── .harness/
    └── case.json       # 权威状态，原子写
```

**`case.json` 是唯一真相，`results.csv` 是投影**（A13）。原因：CSV 是流式追加的，中断时可能最后一行不完整；`case.json` 每集独立、可原子替换。

### 9.2 `case.json` 必须包含

```json
{
  "dataset": "CPT/D010_N500",
  "case_name": "case-01",
  "status": "success",
  "reason": "",
  "attempt": 1,
  "container": {"name": "gae-...", "exit_code": 0, "oom_killed": false},
  "env": {"pre": "...", "post": "..."},
  "metrics": {"precision": 0.0, "recall": 0.0, "f1": 0.0, "shd": 0},
  "final_algorithm": "DirectLiNGAM",
  "duration_s": 0.0
}
```

### 9.3 `resume` 语义

- 只跳过 `status in {success, timeout_partial, invalid_leak, invalid_answer}` 的集（已有结论）；
- `timeout` / `claude_error` / `docker_error` / `docker_oom` **重跑**；
- 不按"文件存在"判断（A13）；
- 重跑时 case 目录先清空重建，避免上一轮的 `answer/` 被误读成本轮结果。

### 9.4 检查点

跑 3 集 → 中途 Ctrl-C → `resume` → 确认只重跑未完成的那几集，且 `run.json` 的起止时间合理。

---

## 10. 阶段 7：报告

### 10.1 两个文件

- `results.csv`：每集一行，机器可读；
- `report.md`：人类可读，含总体、分格、分机制、失败清单、口径声明。

### 10.2 报告必须写明的（A10）

> 本结果由容器化运行时产生（镜像 digest `sha256:...`，Claude CLI `x.y.z`）。
> 与 2026-11 之前的 Windows 原生 harness 结果**不可直接比较**。

### 10.3 常用表格

| 表 | 内容 |
| --- | --- |
| 总体 | F1 / SHD / P / R / 骨架 P/R，Agent vs 基线 |
| 分隔 | 每格计分集数、F1、SHD |
| 分机制 | 每机制 F1/ΔF1/SHD |
| 失败清单 | 状态、原因、最终算法 |
| 口径 | 哪些状态计分、哪些不计分、判官未启用 |

### 10.4 检查点

`general-agent-eval report --run <run_id>` 能在**不重跑**的情况下重新生成全部报告（纯读 `case.json`）。

---

## 11. 阶段 8：`doctor` 与 `smoke`

### 11.1 `doctor`（免费，真环境）

检查项：

- Docker daemon 可用、版本；
- 镜像存在、digest 可读；
- 容器内 `claude --version`、`python3 -V`、`toolbox` import；
- 挂载探针；
- 密钥环境变量存在（只打印"已设置"，不打印值）；
- 缓存目录、输出目录可写；
- 磁盘剩余空间足够跑 N 集。

### 11.2 `smoke`（付费，`--yes` 才跑）

跑 1 集最小的（例如 `Linear/D010_N500`），验证端到端：启动容器 → 出答案 → 解析 → 评分 → 写报告。跑完打印本次花费（若 CLI 输出 usage）。

### 11.3 检查点

新机器上按顺序：`doctor` 全绿 → `smoke --yes` 成功 → 才允许跑正式批次。

---

## 12. 验收清单（打勾再宣布 v1 完成）

- [ ] `selftest` 离线全绿，无网络、无 Docker 调用
- [ ] `parse` 覆盖表头 / `X0` 前缀 / `true-false` / 多余列 / 形状错 / 对角线 / 非 0/1 值
- [ ] `metrics` 与 `evaluation-runner/evaluate.py` 对拍一致（含 SHD）
- [ ] `run` 未显式选择机制/D/N 时拒绝执行
- [ ] 挂载探针真实读写通过，失败即中止
- [ ] 容器**不**加 `--rm`，先 `inspect` 后 `rm`
- [ ] `State.ExitCode` / `State.OOMKilled` 已落盘
- [ ] 两层超时生效：600s TERM、30s 宽限、635s 硬停
- [ ] Ctrl-C 后无 `gae-*` 残留容器
- [ ] Linux 下 case 文件归当前用户，非 root
- [ ] `run.json` 含 digest / CLI 版本 / lock 哈希 / 数据 sha256
- [ ] `pre.json`/`post.json` 落盘；SIGKILL 时报告标"证据不完整"
- [ ] 金标准与数据集从不挂进容器
- [ ] 状态语义正确，`docker_error` / `docker_oom` 不混入 `timeout`
- [ ] `resume` 按 `case.json` 状态跳过，失败集重跑
- [ ] 报告声明"新旧结果不可比"
- [ ] 配置、日志、报告中无密钥明文
- [ ] `smoke` 必须 `--yes`
- [ ] `toolbox.lock` 的包在容器内 import 成功
- [ ] 单镜像同时提供 `python3` 与 `claude`

---

## 13. 延后项怎么留接口

| 延后项 | 留什么 |
| --- | --- |
| 并发 | `config.jobs` 字段 + `runner` 里串行实现；并发改成 worker 池即可 |
| 原生运行时 | `config.runtime: "docker"`；`container.py` 后面加 `native.py` |
| registry | `config.image` 支持 `repo/name:tag`，本地构建只是其中一种来源 |
| 网络/密钥 | `container.py` 里网络参数集中一处，后续加 `--network` / 代理 |
| judge | `judge.py` stub 返回 `{"skipped": true}`，报告里显示"未启用" |

---

## 14. Docker 新手常见错误对照表（速查）

| 现象 | 真实原因 | 修法 |
| --- | --- | --- |
| 容器里写的东西宿主看不到 | 挂载路径写错 / 没挂 | 跑挂载探针；用绝对路径 |
| 宿主删不掉 case 目录 | 容器内 root 写的 | 加 `--user uid:gid`，或事后 `sudo chown -R` |
| `Ctrl-C` 后容器还在跑 | 没捕获信号 / 没标签清理 | 按 `gae.run` 标签清理 |
| 超时了但容器还活着 | 只杀了宿主进程 | `docker kill -s TERM` → 等 → `-s KILL` |
| 分不清是超时还是 OOM | 用了 `--rm` | 去掉 `--rm`，先 inspect |
| `docker run` 立刻退出 exit 1 | 多半是命令拼错或 CLI 崩溃 | 看 `docker logs`；快速失败可重试一次 |
| 模型答案很差 | 可能根本没跑模型，容器提前挂了 | 先看 `pre.json` 和 `raw/*.jsonl` 是否有内容 |
| 一集把机器拖死 | 没限制资源 | `--cpus/--memory/--pids-limit` |
| 镜像越构建越大 | 没用 `--no-cache-dir`、没清 apt 缓存 | 加 `--no-cache-dir`，合并 `RUN` |
| Windows 路径挂载失败 | 路径没共享 / 用了 Git Bash 的 `/c/` | 用 Docker Desktop 设置里共享该盘，路径用 `C:/...` |

另外提醒一个**你自己踩过的**：Windows 上的 Bash 工具是 Git Bash，不是 WSL。`/home/yuki/...` 在 Git Bash 里不存在，要么用 `//wsl.localhost/Ubuntu/...`，要么显式 `wsl.exe -d Ubuntu -- bash -lc "..."`。

---

## 15. 这个项目能带给你什么（面试/工作向）

不要只把它当"跑分脚本"。它实际覆盖了下面这些工程能力，每条都能讲出取舍：

### 15.1 容器与进程

- **镜像分层与构建缓存**：为什么 `COPY requirements` 要在 `COPY src` 之前——改代码不该触发重装依赖。
- **PID 1 与信号转发**：为什么 shell 当入口会丢 SIGTERM；为什么要有 `agent-entrypoint.py`。
- **退出码语义**：125/126/127/137/143 的区分，以及"基础设施失败"与"业务失败"必须分开统计。
- **资源限制**：`--memory` 触发 OOM 时容器是 137，不是你的程序"算错了"。
- **只读模板 vs 可变实例**：运行时 `pip install` 不污染下一集，是容器模型的直接应用。

### 15.2 可复现性工程

- **证据链思维**：镜像 digest + CLI 版本 + 依赖 lock + 数据 sha256 + 前后快照。任何一项缺失，"结果不一样"就无法归因。
- **不可比性声明**：换了运行时就必须标注，不能把两批数据混在一个均值里。这是数据诚信问题，不只是技术问题。

### 15.3 Python 工程化

- `src/` 布局 + `pyproject.toml` + console script：让别人 `pipx install` 就能用。
- 配置分层：默认值 / 配置文件 / 命令行覆盖，且**危险默认值要显式确认**（`smoke --yes`）。
- CLI 用户体验：缺参数时打印**可选项**再退出，而不是抛 traceback。

### 15.4 测试策略

- 三层测试（离线 selftest / 环境 doctor / 付费 smoke），对应"单元 / 集成 / 端到端"。
- **对拍测试**：不信任自己重写的指标，和参考实现逐值比较。
- 不用 mock 掩盖真实行为（尤其是 Docker 交互）——mock 过的挂载测试全部通过，真机上照样写不进文件。

### 15.5 可观测性与排障

- 原始事件流（jsonl）不删：事后一切分析都靠它。
- `inspect` 落盘：排障不依赖"再跑一次"。
- 失败保留现场（`--keep-container-on-failure` + `shell`）：好的排障体验是设计出来的。

### 15.6 安全

- 密钥走环境变量、报告脱敏、永不入镜像。
- 最小挂载面：只挂当前 case；金标准在宿主侧。
- 对"跳过权限确认"这类高风险开关，写明理由和边界，而不是默默打开。

### 15.7 取舍与判断力（最值钱的部分）

能说清楚"为什么 v1 不做并发 / 不做原生运行时 / 不做 judge"，比多写 500 行代码更能体现工程成熟度：

- 并发会同时放大超时、资源竞争、日志交错三个问题，收益是墙钟时间；
- 原生运行时的收益是省一层容器，代价是解释器/依赖不可控——而这正是我们要解决的问题；
- judge 是 LLM 自评，和被测模型同源，先不做反而更诚实。

---

## 16. 下一步

1. 先做阶段 1（解析 + 评分 + selftest），半天内应有可验证产出。
2. 再做阶段 3（镜像 import 自检），这是整个方案的地基。
3. 阶段 4 的挂载探针和超时是**必须**先于任何正式批次完成的两件事。
4. 全部检查点通过后，先 `--limit 1` 跑一集，再 `--limit 3`，最后才放开全选。

> 遇到设计文档和本指南冲突：以设计文档为准。遇到设计文档没覆盖的实际问题：先记下来，别边跑边改口径。
