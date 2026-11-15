# general-agent-eval P1 设计与实现口径

日期：2026-11-15
分支：`feat(evaluation)-general-agent`
上位设计：`general-agent-eval-design.md`（冲突以其为准）
前置：`general-agent-eval-p0-design.md`

## 1. 目标

补齐正式批次开跑前与跑完后的两处可用性缺口：

1. 交互选择与开跑确认（设计 §5.2）
2. 报告算法分布、成本耗时、失败详情与基线样本一致性（设计 §8.3）

## 2. 非目标

- 不做 `jobs>1`、native 运行时、judge、网络白名单
- 不改 CLI 其它命令（`shell`、`--no-keep-cases`、`--env-file`、`doctor --probe-agent`）
- 不改评分口径与执行链路
- 不新增第三方依赖

## 3. 交互选择

### 3.1 参数

- `--mechanisms/--dims/--ns` 接受逗号分隔或重复传参；新增 `all` 展开为数据集可用值。
- `run` 新增 `--seed`（覆盖 `selection.seed`）与 `--yes`（跳过确认）。
- `--dry-run` 只选集并打印，不提示确认。
- 非 TTY 且缺参：保持现状，打印可用值并返回 2，不进入交互。

### 3.2 交互流程（TTY 且缺参时）

逐轴提示，未提供的轴才询问：

```
机制：
  1) Linear
  2) CPT
选择编号（逗号分隔，a=全选，回车中止）：
```

- 输入 `1,3`（或空格分隔）按编号多选；`a` 全选；空输入中止（返回 0，不默认全量）。
- 编号越界或非数字：提示后重新输入，不退出。
- 三轴收集完后选集，打印集数与预估，再问 `确认开跑？[y/N]`；仅 `y/yes` 继续。

### 3.3 预估口径

配置常量，不做历史均值推断：

```yaml
estimate:
  seconds_per_case: 600
  cost_usd_per_case: 0.6
```

预估文案由纯函数生成：`预计耗时约 X 分钟，成本约 $Y（N 集 × 单集 600s / $0.60）`。
成本显式标注为“预估”，不写入 `run.json`，不影响报告。

### 3.4 可测性

- `_parse_picks(raw, size) -> list[int]`：返回 0 基下标；空串、非数字、越界、重复编号抛 `ValueError`（空输入的“中止”在调用方处理）。
- `_expand_csv(text, values)`、`_expand_list(items, values)`：处理 `all` 与普通列表；未知值原样保留，交给 `selector.select` 产出空集后由调用方报错。
- `estimate_text(count, config)`：纯函数。
- 交互本身不 mock TTY；测试只覆盖上述纯函数与非 TTY 报错分支。

## 4. 报告补充

在现有报告（P0 后的指标/状态/环境证据）基础上追加：

### 4.1 算法选择

- `final_algorithm` 是自由文本（如 `GES (BDeu) + DirectLiNGAM orientation`），直接计数会碎片化；报告计数前先归一化：
  按 `runner.ALGORITHM_PATTERNS` 的关键词族匹配，命中多个时全部计入（如一条文本含 GES 与 DirectLiNGAM 则各 +1），一个都没有记“（未识别）”。
- 表一：归一化后的算法族计数（降序）。
- 表二：按机制的众数（同样归一化），附该众数出现次数。
- CSV 里的 `final_algorithm` 保持原始文本，不改写。

### 4.2 成本与耗时

- 计分集：平均耗时、token 输入/输出合计、成本合计（`usage.total_cost_usd`）。
- 全部 case 成本合计（含未计分）单列一行。
- 字段缺失（旧 run）显示 `-`，求和跳过缺失值，不报错。

### 4.3 未计分集

现有表格补 `duration_s`、`final_algorithm` 两列。

### 4.4 基线样本一致性

- 计分集按 dataset 匹配基线；匹配数少于计分集数时，在“总体指标”下明确写：
  `样本不一致：agent 计分 N 集 / 基线匹配 M 集（按 dataset 匹配）。`
- 未配置基线时保持现有“跳过基线列”行为。

### 4.5 results.csv 算法列

- 新增 `algorithms` 列：本集试过的全部算法（工具调用输入里命中的全部算法族，`;` 连接、去重、排序），对标旧 agent-test 的 `algorithms` 语义。
- `final_algorithm`：优先 `answer/algorithm.txt` 首行（截 50 字）；没有则回退为 `algorithms` 的值。
- 两个字段都写入 `case.json`，`results.csv` 为投影；旧 run 无该字段时为空，不报错。

## 5. 影响文件

- `general-agent-eval/src/general_agent_eval/cli.py`
- `general-agent-eval/src/general_agent_eval/report.py`
- `general-agent-eval/src/general_agent_eval/runner.py`（`RESULT_HEADER`、`case.json`、`_row`）
- `general-agent-eval/src/general_agent_eval/config.py`
- `general-agent-eval/config.yaml`
- `general-agent-eval/README.md`
- `general-agent-eval/tests/test_cli_selection.py`（新增）
- `general-agent-eval/tests/test_report.py`（新增）

## 6. 测试

1. `_parse_picks`：正常多选、`a`、空、越界、非数字。
2. `all` 展开：三个轴分别覆盖。
3. `estimate_text`：集数×常量，缺配置时用默认值。
4. 报告：算法归一化计数与机制众数、成本/token 汇总、失败表列、基线样本不一致提示、字段缺失容错。
5. 算法列：`algorithms` 从工具输入命中多个算法族并去重排序；`final_algorithm` 有 `algorithm.txt` 时取首行、否则等于 `algorithms`；`_row` 投影含该列。

验证顺序：`pytest -q` → `selftest` → `run --dry-run`（TTY 交互人工验证）→ 旧 run `report`。

## 7. 验收

- [ ] TTY 缺参进入三轴交互；空输入中止，`a` 全选
- [ ] `--yes` 跳过确认；`--dry-run` 不提示
- [ ] 非 TTY 缺参仍返回 2
- [ ] 预估值 = 集数 × 配置常量
- [ ] 旧 run 报告可生成，缺失字段显示 `-`
- [ ] `pytest -q` 全绿；不新增依赖
