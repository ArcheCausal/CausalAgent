FROM python:3.11-slim

WORKDIR /app

ENV PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=5 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1

# CDFM 权重在构建期下载并逐文件校验；revision 与摘要写死在 cdfm_weights.py，
# 镜像内副本位于只读目录，运行期不再访问网络。
ARG HF_ENDPOINT=https://hf-mirror.com

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc \
       g++ \
       git \
       default-libmysqlclient-dev \
       pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-base.txt /tmp/requirements-base.txt
COPY Agent/CausalAgentMCP/requirements.txt /tmp/causal-mcp-requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
       torch==2.7.1+cpu \
    && mkdir -p /tmp/cdfm-wheel \
    && pip download --no-cache-dir --no-deps --only-binary=:all: \
       --dest /tmp/cdfm-wheel cdfm-base==0.1.0 \
    && printf '%s  %s\n' \
       '3ca703403509403c2bfd0e13b074eca7d0297da03031be784209ab36581dc4c6' \
       '/tmp/cdfm-wheel/cdfm_base-0.1.0-py3-none-any.whl' \
       | sha256sum -c - \
    && pip install --no-cache-dir --no-deps \
       /tmp/cdfm-wheel/cdfm_base-0.1.0-py3-none-any.whl \
    && pip install --no-cache-dir -r /tmp/causal-mcp-requirements.txt \
    && pip install --no-cache-dir --no-deps \
       git+https://github.com/DMIRLAB-Group/CDMIR.git@7caa64a819e8699243bbe4a54e623626f7801a40 \
    && pip check \
    && python -c "from cdfm import CDFM; print('cdfm-import-ok')"

COPY Agent ./Agent
COPY app ./app
COPY config ./config
COPY Database ./Database
COPY observability ./observability
# 预发环境门禁脚本由一次性 seed 服务复用；只复制该脚本，不带入部署工具。
COPY scripts/staging_environment_guard.py ./scripts/staging_environment_guard.py

# 只读权重目录：构建末尾下载并校验，不合格直接构建失败。
RUN python -m Agent.CausalAgentMCP.cdfm_weights fetch \
      --destination /opt/cdfm-weights-image \
      --endpoint "${HF_ENDPOINT}" \
    && chmod -R a-w /opt/cdfm-weights-image

EXPOSE 8080

CMD ["python", "-m", "Agent.CausalAgentMCP.app"]
