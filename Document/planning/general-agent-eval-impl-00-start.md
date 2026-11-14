# 实现手册 0：开工准备与总览

> 这是「分阶段完整代码手册」的第 0 份。共 5 份：
>
> | 文件 | 内容 | 产出 |
> | --- | --- | --- |
> | **00-start（本文件）** | 环境、目录、依赖、配置 | 空骨架 + 可安装的包 |
> | [01-core](general-agent-eval-impl-01-core.md) | 配置、数据、解析、评分、审计 | 离线可测的核心 |
> | [02-image](general-agent-eval-impl-02-image.md) | Dockerfile、入口、工具链 | 可运行的镜像 |
> | [03-runtime](general-agent-eval-impl-03-runtime.md) | 容器执行、编排、CLI | 能跑一集 |
> | [04-report-ops](general-agent-eval-impl-04-report-ops.md) | 报告、doctor、验收、排错 | v1 完成 |
>
> 配套设计文档：`general-agent-eval-design.md`（口径以它为准）。
> 配套指南：`general-agent-eval-implementation-guide.md`（优先级与坑速查）。

---

## 0.1 你将做出什么

一条命令：

```bash
general-agent-eval run \
  --config config.yaml \
  --mechanisms Linear,CPT,RFFHetero \
  --dims 10 15 \
  --ns 500 1000
```

它做的事：

1. 从缓存读取数据集，按「机制 × D × N」选集；
2. 每集启动一个 Docker 容器，容器里是固定 Python 工具链 + Linux 版 Claude Code CLI；
3. 模型读 `data.csv`，自己选因果发现算法，把邻接矩阵写到 `answer/adjacency.csv`；
4. 宿主侧解析矩阵、算 F1/SHD、写 `case.json`；
5. 全部跑完后生成 `report.md` + `summary.csv`。

**重要边界**：被测的是「通用 Agent 会不会自己选算法」，所以提示词不指定算法；工具链只是保证它想用什么都有。

---

## 0.2 前置检查

在 **WSL Ubuntu** 里执行（不是 Windows 的 Git Bash）：

```bash
uname -a
python3 --version
docker version
docker run --rm hello-world
```

要求：

| 项 | 要求 | 不满足怎么办 |
| --- | --- | --- |
| 系统 | WSL2 Ubuntu 22.04+ 或原生 Linux | 手册里的路径按 Linux 写 |
| Python | 3.10+ | `sudo apt install python3 python3-venv python3-pip` |
| Docker | Client/Server 都有版本输出 | `sudo service docker start` |
| 网络 | 能访问 PyPI（可用国内镜像） | 见 0.4 |
| 密钥 | 环境变量里有 DeepSeek key | `export DEEPSEEK_API_KEY=...` |

> 如果你在 Windows 里用 Git Bash：`/home/...` 不存在，必须 `wsl.exe -d Ubuntu -- bash -lc "..."`，或直接开 Ubuntu 终端。这是之前踩过的坑。

---

## 0.3 建立目录

```bash
cd ~/CausalAgent3
mkdir -p general-agent-eval/src/general_agent_eval
mkdir -p general-agent-eval/docker
mkdir -p general-agent-eval/tests
cd general-agent-eval
```

最终结构（现在只需要建目录，文件后面逐个填）：

```text
general-agent-eval/
├── pyproject.toml
├── config.yaml
├── README.md
├── src/general_agent_eval/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── selector.py
│   ├── data.py
│   ├── parse.py
│   ├── metrics.py
│   ├── audit.py
│   ├── trace.py
│   ├── container.py
│   ├── runner.py
│   ├── report.py
│   ├── preflight.py
│   └── selftest.py
├── docker/
│   ├── Dockerfile
│   ├── agent-entrypoint.py
│   └── toolbox.lock
└── tests/
    ├── test_parse.py
    └── test_metrics.py
```

---

## 0.4 创建 `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "general-agent-eval"
version = "0.1.0"
description = "通用 Agent 因果发现评测（Docker 运行时）"
requires-python = ">=3.10"
dependencies = [
    "numpy>=1.24",
    "pandas>=2.0",
    "PyYAML>=6.0",
    "causal-learn>=0.1.3.8",
]

[project.scripts]
general-agent-eval = "general_agent_eval.cli:main"

[tool.setuptools.packages.find]
where = ["src"]
```

为什么宿主也要 `causal-learn`：SHD 要在金标准 DAG 的 CPDAG 上算，这个转换直接用它的 `dag2cpdag`，和基线口径一致。

---

## 0.5 创建 `src/general_agent_eval/__init__.py`

```python
"""通用 Agent 因果发现评测。"""

__version__ = "0.1.0"
```

---

## 0.6 创建 `config.yaml`

先用**本地已有数据**跑通，下载逻辑留到后面。

```yaml
cache_dir: ~/.general-agent-eval/cache
output_dir: ~/general-agent-eval-runs
workspace_dir: ~/.general-agent-eval/work

data:
  name: cdfm_bench_rep00
  # 已有数据时填这里，跳过下载
  local_dir: /home/yuki/CausalAgent3/evaluation-runner/cdfm_bench_rep00
  url: ""
  sha256: ""
  exclude: [TimeLag]

selection:
  mechanisms: []
  dims: []
  ns: []
  per_cell: 1
  seed: 20261030

agent:
  image: general-agent-eval/agent:py311-toolbox-v1
  base_url: https://api.deepseek.com/anthropic
  model: deepseek-chat
  auth_env: DEEPSEEK_API_KEY
  timeout_s: 600
  grace_s: 30
  retries: 1
  disallowed_tools: WebSearch,WebFetch
  pip_index_url: https://mirrors.aliyun.com/pypi/simple/

docker:
  cpus: "2"
  memory: 4g
  pids_limit: 512
  shm_size: 512m
  host_user: true
  keep_container_on_failure: true
  network: bridge

judge:
  enabled: false

baseline_results: ""
```

几个刻意的选择：

- `selection` 三组都是空数组，表示**没有默认选择**，`run` 时必须显式传；
- `judge.enabled: false`，v1 不用 LLM 判官；
- `baseline_results` 留空，报告里就不做基线对比；
- `local_dir` 指着现有 numpy 数据，先不折腾下载。

---

## 0.7 创建 `.gitignore`

```gitignore
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
runs/
```

---

## 0.8 安装与验证

```bash
cd ~/CausalAgent3/general-agent-eval
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip -i https://mirrors.aliyun.com/pypi/simple/
pip install -e . -i https://mirrors.aliyun.com/pypi/simple/
python -c "import general_agent_eval; print(general_agent_eval.__version__)"
```

预期：打印 `0.1.0`。

如果 `causal-learn` 装得慢，先用镜像；装完确认：

```bash
python -c "from causallearn.utils.DAG2CPDAG import dag2cpdag; print('ok')"
```

> 这一步失败先别往下走。后面所有评分都依赖它。

---

## 0.9 常见错误

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `No module named general_agent_eval` | 没装或没激活 venv | `source .venv/bin/activate` 后重装 |
| `externally-managed-environment` | 用了系统 python 直接装 | 用 venv，别用系统 python |
| `causal-learn` 编译失败 | 缺编译工具 | `sudo apt install build-essential`，或升级 pip |
| `docker: command not found` | 在 WSL 但没装 docker | 装 Docker Engine 或启用 Docker Desktop 集成 |
| `permission denied /var/run/docker.sock` | 不在 docker 组 | `sudo usermod -aG docker $USER`，重新登录 |

---

## 0.10 本阶段检查点

- [ ] `python -c "import general_agent_eval"` 成功
- [ ] `python -c "from causallearn.utils.DAG2CPDAG import dag2cpdag"` 成功
- [ ] `docker run --rm hello-world` 成功
- [ ] `config.yaml` 里的 `local_dir` 路径存在

四项都过，进入 [01-core](general-agent-eval-impl-01-core.md)。
