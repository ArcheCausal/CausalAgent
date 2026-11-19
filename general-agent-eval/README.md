# general-agent-eval

通用 Agent 因果发现评测：每集一个 Docker 容器，容器内是固定 Python 工具箱 + Linux 版 Claude Code CLI；模型自己读 `data.csv`、选算法、写 `answer/adjacency.csv`，宿主侧解析评分。

## 快速开始（WSL Ubuntu）

```bash
##进入当前目录后
source .venv/bin/activate
export DEEPSEEK_API_KEY=...

# 离线检查
python -m general_agent_eval.selftest
pytest -q

# 环境检查（Docker / 镜像 / 密钥 / 挂载）
general-agent-eval --config config.yaml doctor

# 环境齐备检查（Docker / 镜像 / 数据 / baseline）
general-agent-eval --config config.yaml setup

# 镜像缺失时显式构建（耗时，需网络）
general-agent-eval --config config.yaml setup --build

# 跑一集（付费）
general-agent-eval --config config.yaml run \
  --mechanisms Linear --dims 10 --ns 500 --limit 1

# 交互选择（不传三参）
general-agent-eval --config config.yaml run

# 生成报告
general-agent-eval --config config.yaml report --run ~/general-agent-eval-runs/runs/<run_id>
```

## 命令

| 命令 | 说明 |
| --- | --- |
| `setup` | 检查 Docker/镜像/baseline，打印可选机制/D/N；`--build` 显式构建镜像 |
| `selftest` | 离线自检，不联网不调 Docker |
| `doctor` | 十项环境检查，任何 FAIL 都别开跑 |
| `run` | 缺参时 TTY 交互多选；`all` 全选；`--yes` 跳过确认；`--dry-run` 只选集 |
| `resume --run <dir>` | 按 `case.json` 状态跳过已完成集 |
| `report --run <dir>` | 只读 `case.json` 重建 `summary.csv` / `report.md` |
| `smoke --yes` | 端到端跑一集，必须确认 |
| `shell --container <name>` | 进入失败保留的容器 |

## 目录产物

```text
~/general-agent-eval-runs/runs/<run_id>/
├── run.json          # 镜像指纹、selection、数据指纹、宿主信息
├── results.csv       # case.json 的投影
├── trace.csv         # 工具调用轨迹
├── summary.csv
├── report.md
├── raw/<dataset>.jsonl          # stream-json 原始输出
├── raw/<dataset>.inspect.json   # 容器 inspect（密钥已脱敏）
└── cases/case-NN/
    ├── case.json     # 权威状态 + 指标
    ├── data.csv
    ├── answer/adjacency.csv
    └── .harness/     # pre.json / post.json / home/
```

`run.json` 记录镜像 digest、Docker 版本、CLI 版本、toolbox.lock 哈希、
数据集/baseline 哈希与 `env_drift_cases`；`report.md` 会列出 pre/post 缺失的 case
和运行期依赖漂移。

报告新增算法选择分布、按机制众数、token/成本/耗时汇总；未计分集带耗时与算法；
基线按 dataset 匹配不足时标注样本不一致；未配置基线时跳过基线列。`results.csv`
新增 `algorithms` 列记录本集试过的全部算法，`final_algorithm` 保持原始文本。

