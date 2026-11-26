FROM python:3.11-slim

# 当前已验证版本；升级需重建镜像并更新报告记录
ARG CLAUDE_VERSION=2.1.322

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
