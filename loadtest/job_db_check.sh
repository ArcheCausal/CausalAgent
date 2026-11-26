#!/usr/bin/env bash
# 文档职责：服务器只读采集入口，JSON v2；旧 Job ID 文本清单仍可使用。
# 适用范围：服务器 Python 3；ops/server_evidence.py 必须与脚本一起部署。
# db: <run_id> <requests.json|job_ids.txt>
# sample: <run_id> <最大持续秒数> [间隔秒] [档位] [停止提交标记.json]
# 标记包含 run_id、submission_stopped_at(epoch/ISO)。收到标记后持续观察 1800 秒。
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/ops/server_evidence.py" db "$@"
