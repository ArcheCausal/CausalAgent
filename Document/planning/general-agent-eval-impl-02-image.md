# 实现手册 2：镜像、入口、工具链

> 本阶段产出：一个能跑 Python 因果库 + Linux 版 Claude Code CLI 的镜像。
> 结束标准：三条 `docker run` 验证命令都能通过。

---

## 2.1 这一阶段要解决的三个问题

1. **解释器唯一**：容器里只有 `/usr/local/bin/python3`，不会出现"用了哪个 conda 环境"的问题；
2. **依赖可证明**：`toolbox.lock` 写了什么，构建时就必须 import 成功；
3. **进程可管理**：Python 入口当 PID 1，能接住 `docker stop` 并转发给 Claude CLI。

---

## 2.2 `docker/toolbox.lock`

只锁**直接依赖**的版本。传递依赖的精确版本在构建后从 `pip freeze` 里取（会自动进 `pre.json`，作为证据）。

```text
numpy==1.26.4
scipy==1.13.1
pandas==2.2.2
scikit-learn==1.4.2
causal-learn==0.1.3.8
lingam==1.8.3
statsmodels==0.14.2
networkx==3.3
```

为什么不放 `pgmpy`：它会拖进 torch，镜像从几百 MB 涨到几个 GB。需要时让模型在容器内临时 `pip install`，反正每集容器都是新的，不会污染下一集。

> 如果某个版本在你的镜像源上不存在：`pip index versions lingam` 查一下，换成实际存在且能互相兼容的版本，再把 lock 改掉。**不要**写成 `>=`，否则每次重建镜像都可能不同。

---

## 2.3 `docker/Dockerfile`

```dockerfile
FROM python:3.11-slim-bookworm

ARG CLAUDE_VERSION=latest

# 国内 apt 源；bookworm 的源文件是 debian.sources，不是 sources.list
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true

# Debian bookworm 自带 Node 18，满足 Claude Code 的 Node >= 18；比 nodesource 少一层网络依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY docker/toolbox.lock /tmp/toolbox.lock
# 装完立刻 import：写了 lock 不等于装上了，这里是唯一的硬证据
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /tmp/toolbox.lock \
    && python3 -c "import numpy, pandas, sklearn, lingam; from causallearn.utils.DAG2CPDAG import dag2cpdag; print('toolbox ok')"

RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION} \
    && claude --version

COPY docker/agent-entrypoint.py /usr/local/bin/agent-entrypoint.py
RUN chmod +x /usr/local/bin/agent-entrypoint.py

ENV PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python3", "/usr/local/bin/agent-entrypoint.py"]
```

几个刻意的取舍：

| 选择 | 理由 |
| --- | --- |
| 单镜像装 Python + Node | 多阶段镜像在 glibc / ABI 上容易出问题，v1 用体积换稳定 |
| Node 用 apt 的 18 | 少一个外部脚本；国内网络更稳。若 CLI 要求更高版本再换 nodesource |
| **不写 `USER`** | 运行时用 `--user uid:gid` 动态指定，避免和宿主 UID 不匹配 |
| 不装 pgmpy | 避免拖入 torch，镜像小一个数量级 |
| `ENTRYPOINT` 是 Python 脚本 | shell 当 PID 1 不转发信号，`docker stop` 会等超时才杀 |

---

## 2.4 `docker/agent-entrypoint.py`

```python
#!/usr/bin/env python3
"""容器 PID 1：记录环境证据 → 运行 claude → 转发信号 → 记录后置证据。"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HARNESS = Path("/work/.harness")


def run(cmd, timeout=30):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (proc.stdout + proc.stderr).strip()
    except Exception as exc:
        return f"ERROR: {exc}"


def dump(name, payload):
    HARNESS.mkdir(parents=True, exist_ok=True)
    target = HARNESS / f"{name}.json"
    tmp = HARNESS / f".{name}.tmp"
    data = {
        "time": time.time(),
        "uid": os.getuid(),
        "cwd": os.getcwd(),
        "python": run(["python3", "-V"]),
        "claude": run(["claude", "--version"]),
        "pip_freeze": run(["pip", "freeze"]).splitlines(),
    }
    data.update(payload)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, target)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: agent-entrypoint.py <command> [args...]", file=sys.stderr)
        return 2

    dump("pre", {"command": sys.argv[1:]})
    child = subprocess.Popen(sys.argv[1:])

    def forward(signum, frame):
        # 只转发；退出码仍以子进程为准
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    code = child.wait()
    dump("post", {"command": sys.argv[1:], "exit_code": code})
    return code


if __name__ == "__main__":
    sys.exit(main())
```

为什么必须由它当 PID 1：

- `docker stop` 只给 PID 1 发 SIGTERM。如果 PID 1 是 `sh -c "claude ..."`，信号不一定传给 claude；
- 被 SIGKILL 时 `post.json` 不会生成——这本身是有用信息，报告里会标"环境证据不完整"；
- `pre.json` 里的 `claude --version` 和 `pip freeze` 就是本次运行的环境快照。

---

## 2.5 构建

先确认 Claude Code 的版本号（后面要写进 `run.json`）：

```bash
npm view @anthropic-ai/claude-code version
```

然后构建：

```bash
cd ~/CausalAgent3/general-agent-eval

# 第一次可先用默认 latest 跑通
docker build -t general-agent-eval/agent:py311-toolbox-v1 -f docker/Dockerfile .

# 正式跑之前建议锁定版本重建
docker build \
  --build-arg CLAUDE_VERSION=2.0.14 \
  -t general-agent-eval/agent:py311-toolbox-v1 \
  -f docker/Dockerfile .
```

构建时间主要花在 apt 和 npm；国内网络下 `pip` 和 `npm` 都已换成镜像源。

---

## 2.6 验证（三条命令）

```bash
# 1. Python 工具链
docker run --rm --entrypoint python3 \
  general-agent-eval/agent:py311-toolbox-v1 \
  -c "import numpy, pandas, sklearn, lingam; from causallearn.utils.DAG2CPDAG import dag2cpdag; print('toolbox ok')"

# 2. Claude CLI
docker run --rm --entrypoint claude \
  general-agent-eval/agent:py311-toolbox-v1 --version

# 3. Node（CLI 的运行时）
docker run --rm --entrypoint node \
  general-agent-eval/agent:py311-toolbox-v1 --version
```

预期：`toolbox ok`、一个版本号、`v18.x`。

再验证入口脚本能写证据（需要给 `/work` 一个可写目录）：

```bash
mkdir -p /tmp/gae-entry-test
docker run --rm \
  -v /tmp/gae-entry-test:/work \
  --entrypoint python3 general-agent-eval/agent:py311-toolbox-v1 \
  -c "print('hello')"
ls -l /tmp/gae-entry-test/.harness
```

> 注意最后一条用了 `--entrypoint python3`，所以不会走我们的入口，只验证挂载。要验证入口本身，去掉 `--entrypoint`，把命令换成 `python3 -c "print('hello')"`：

```bash
docker run --rm -v /tmp/gae-entry-test:/work \
  general-agent-eval/agent:py311-toolbox-v1 \
  python3 -c "print('hello')"
cat /tmp/gae-entry-test/.harness/pre.json | head -20
```

预期：`pre.json` 和 `post.json` 都存在，里面有 `claude` 版本和 `pip_freeze`。

---

## 2.7 记录镜像指纹

```bash
docker inspect --format '{{index .RepoDigests 0}}' general-agent-eval/agent:py311-toolbox-v1
```

本地构建、没推仓库时结果可能为空。代码里会回退到镜像 ID：

```bash
docker inspect --format '{{.Id}}' general-agent-eval/agent:py311-toolbox-v1
```

把这个值先抄下来，第 3 阶段的 `run.json` 会自动记录。

---

## 2.8 常见错误

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `Could not find a version that satisfies numpy==...` | 镜像源没这个版本 | `pip index versions numpy`，改 lock |
| `npm ERR! network` | npm 镜像没生效 | 确认 `npm config get registry` 是 npmmirror |
| `claude: not found` | npm 全局 bin 不在 PATH | `npm bin -g` 查看，或在 Dockerfile 里显式 `ENV PATH` |
| `Illegal instruction` | 在 ARM 上跑 x86 镜像 | 用 `--platform linux/amd64` 构建和运行 |
| 构建到一半磁盘满 | 镜像层堆积 | `docker system df`，必要时 `docker builder prune` |
| `permission denied /work` | 用了 `--user` 但挂载目录属主不对 | 建目录时用当前用户，别用 root 建 |
| 入口写不了 `pre.json` | `/work` 没挂或不可写 | 检查 `-v` 和 `--user` |

---

## 2.9 本阶段检查点

- [ ] `docker build` 成功，日志里有 `toolbox ok`
- [ ] `toolbox ok` 验证命令通过
- [ ] `claude --version` 有输出
- [ ] 入口能在挂载目录写出 `pre.json` / `post.json`
- [ ] 已记下镜像 digest 或 Image ID

下一份：[03-runtime](general-agent-eval-impl-03-runtime.md)。
