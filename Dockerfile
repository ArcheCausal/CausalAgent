# CausalAgent Docker 镜像构建文件

FROM python:3.11-slim AS python-deps

WORKDIR /app

# 容忍部署网络的短时抖动
ENV PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=5

RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 update && apt-get -o Acquire::Retries=5 install -y \
    gcc \
    g++ \
    default-libmysqlclient-dev \
    pkg-config \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# 依赖锁与镜像平台/解释器固定一致；CPU Torch 的 index 和 hash 也由锁文件控制。
COPY tests/smoke/requirements-deep-agent-py311-linux.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.lock


FROM python-deps AS test

COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt

CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests/unit"]


FROM node:24-alpine AS admin-builder

WORKDIR /frontend

COPY packages/design-system/src/ /packages/design-system/src/
COPY admin-frontend/package.json admin-frontend/package-lock.json ./
RUN npm ci \
    && ln -s /frontend/node_modules /packages/design-system/node_modules

COPY admin-frontend/ ./
COPY packages/design-system/ /packages/design-system/
RUN npm run build


FROM node:24-alpine AS chat-builder

WORKDIR /frontend

COPY chat-frontend/package.json chat-frontend/package-lock.json ./
RUN npm ci

COPY chat-frontend/ ./
COPY packages/design-system/ /packages/design-system/
RUN npm run build


FROM node:24-alpine AS website-builder

WORKDIR /frontend

COPY packages/design-system/src/ /packages/design-system/src/
COPY website-frontend/package.json website-frontend/package-lock.json website-frontend/.npmrc ./
RUN npm ci \
    && ln -s /frontend/node_modules /packages/design-system/node_modules

COPY website-frontend/ ./
COPY packages/design-system/ /packages/design-system/
RUN npm run build


FROM node:24-alpine AS rag-eval-builder

WORKDIR /workspace/app/rag_eval/frontend

COPY app/rag_eval/frontend/package.json app/rag_eval/frontend/package-lock.json ./
RUN npm ci

COPY app/rag_eval/frontend/ ./
COPY packages/design-system/ /workspace/packages/design-system/
RUN npm run build


FROM python-deps AS runtime

COPY . .
COPY --from=admin-builder /frontend/dist /opt/causalagent-admin
COPY --from=chat-builder /frontend/dist /opt/causalagent-chat
COPY --from=website-builder /frontend/dist /opt/causalagent-website
COPY --from=rag-eval-builder /workspace/app/rag_eval/frontend_dist /opt/causalagent-rag-eval

ENV ADMIN_FRONTEND_DIST_DIR=/opt/causalagent-admin \
    CHAT_FRONTEND_DIST_DIR=/opt/causalagent-chat \
    WEBSITE_FRONTEND_DIST_DIR=/opt/causalagent-website \
    RAG_EVAL_FRONTEND_DIST_DIR=/opt/causalagent-rag-eval

EXPOSE 5001

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:5001 --workers ${WEB_WORKERS:-1} --threads ${WEB_THREADS:-12} --timeout ${WEB_TIMEOUT:-120} CausalAgent:app"]

