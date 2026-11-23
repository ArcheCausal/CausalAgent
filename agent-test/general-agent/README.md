# 通用 Agent 因果评测

文档职责：记录通用 Agent 的安装、Docker 运行入口和证据位置。

适用范围：评测能自主选择算法并产出邻接矩阵的通用 Agent。运行证据和模型评审规则见 [Agent 评测 AGENTS.md](../AGENTS.md)。

每个案例使用独立的 Docker 容器。容器中的 Claude Code 读取 `data.csv`、选择算法并写入 `answer/adjacency.csv`，宿主侧解析矩阵并计算图指标。

## 安装和配置

```powershell
Set-Location agent-test/general-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.yaml config.yaml
```

命令从仓库根目录开始。后续命令在当前子目录内执行，WSL/Linux 使用 `.venv/bin/general-agent-eval`。可编辑安装会从源码目录定位 `docker/Dockerfile` 和 `toolbox.lock`。

数据集需单独准备。配置模板从当前子目录解析 `../datasets/cdfm_bench_D10_30_fixed_r5`，也可将 `data.local_dir` 改为已有数据目录的绝对路径。按本次目标设置 `agent.base_url` 和 `agent.model`。模型凭据从 `agent.auth_env` 指定的环境变量读取，模板使用 `DEEPSEEK_API_KEY`。

## 常用命令

```powershell
$agentEval = ".\.venv\Scripts\general-agent-eval.exe"

# 离线自检
& $agentEval selftest

# 查看将运行的案例，不调用模型
& $agentEval --config config.yaml run --mechanisms Linear --dims 10 --ns 500 --limit 1 --dry-run

# 镜像缺失时构建，再检查镜像、数据及容器环境
& $agentEval --config config.yaml setup --build
& $agentEval --config config.yaml doctor

# 真实运行同一选择，模型调用可能产生费用
& $agentEval --config config.yaml run --mechanisms Linear --dims 10 --ns 500 --limit 1

# 从案例记录重建报告
& $agentEval --config config.yaml report --run "<run_dir>"
```

纯函数测试使用 `.\.venv\Scripts\python.exe -m pip install pytest` 安装测试依赖，再执行 `.\.venv\Scripts\python.exe -m pytest -q tests`。

| 命令 | 说明 |
| --- | --- |
| `setup` | 检查 Docker/镜像/baseline，打印可选机制/D/N；`--build` 显式构建镜像 |
| `selftest` | 离线自检，不联网不调 Docker |
| `doctor` | 检查工具箱、CLI、挂载、资源约束和凭据变量等环境条件 |
| `run` | 交互终端可补选缺少的维度，非交互执行时要显式提供 `--mechanisms`、`--dims`、`--ns`；`--dry-run` 只预选案例 |
| `resume --run <dir>` | 按 `case.json` 状态跳过已完成集 |
| `report --run <dir>` | 只读 `case.json` 重建 `summary.csv` / `report.md` |
| `smoke --yes` | 真实调用模型运行一集 |
| `shell --container <name>` | 进入失败保留的容器 |

## 目录产物

```text
<output_dir>/runs/<run_id>/
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

`run.json` 记录镜像指纹、Docker 和 CLI 版本、工具箱锁文件与数据集哈希。`report.md` 汇总图指标、算法、用量、耗时、失败状态和环境漂移。`results.csv` 中的 `algorithms` 来自工具轨迹，`final_algorithm` 优先取 `answer/algorithm.txt`。

`success` 和有有效矩阵的 `timeout_partial` 计入均值。SHD 使用 CPDAG 口径，可逆边的方向不增加距离。可选配置 `baseline_results` 指向已有 CSV，按 `dataset` 匹配，存在状态列时只选取 `success` 和 `timeout_partial` 且有数值 F1 的行。报告会标注样本数差异。比较指标前须核对样本和评分定义，尤其是 CausalAgent 入口的 SHD 和对镜像边的精确率规则。
