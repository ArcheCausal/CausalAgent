#!/usr/bin/env bash
# 在服务器上执行，按本轮真实 Job 清单只读核对主库终态、会话归属、时间戳与执行释放状态。
#
# 文档职责：定义逐 Job 主库核对的输入清单格式与输出字段，作为报告“主库 Job 终态核对”一节
# 的唯一来源；核对是数据库层证据，与管理端页面核对相互独立。
#
# 适用范围：在 staging 服务器上执行；客户端只记录自己发起的请求，本脚本负责独立确认。
#
# 用法：
#   sudo bash loadtest/job_db_check.sh <run_id> [job_ids.txt] > job-db-<run_id>.json
#
# job_ids.txt 每行一个 job_id，由压测运行摘要中的 SSE 样本导出。不传清单文件时只输出全库分布。
# 只执行 SELECT；凭据从受控凭据文件读取，不写入输出；不输出 Prompt、模型正文或用户文件内容。

set -u

RUN_ID="${1:-unknown-run}"
JOB_LIST="${2:-}"
SCHEMA_VERSION="loadtest-job-db-check-v1"

PROJECT="${COMPOSE_PROJECT_NAME:-causalagent-staging}"
ENV_FILE="${CAUSALAGENT_ENV_FILE:-/opt/causalagent-staging/.env.staging}"

read_env() {
    if [ ! -f "$ENV_FILE" ]; then return 1; fi
    if [ -r "$ENV_FILE" ]; then
        grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2-
    else
        sudo -n grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2-
    fi
}

MYSQL_USER_VALUE="$(read_env MYSQL_USER)"
MYSQL_PASSWORD_VALUE="$(read_env MYSQL_PASSWORD)"
MYSQL_DATABASE_VALUE="$(read_env MYSQL_DATABASE)"
[ -z "$MYSQL_DATABASE_VALUE" ] && MYSQL_DATABASE_VALUE="rag_eval_staging"

if docker info >/dev/null 2>&1; then
    DOCKER_SUDO=0
elif sudo -n docker info >/dev/null 2>&1; then
    DOCKER_SUDO=1
else
    DOCKER_SUDO=-1
fi

dk() {
    if [ "$DOCKER_SUDO" = "-1" ]; then return 1; fi
    if [ "$DOCKER_SUDO" = "1" ]; then sudo -n docker "$@"; else docker "$@"; fi
}

query() {
    # 只读查询主库；不可用时返回明确的缺失标记。
    if [ "$DOCKER_SUDO" = "-1" ]; then printf 'missing'; return 0; fi
    if [ -z "$MYSQL_USER_VALUE" ] || [ -z "$MYSQL_PASSWORD_VALUE" ]; then printf 'missing'; return 0; fi
    dk exec "$PROJECT-mysql-primary-1" mysql \
        -u"$MYSQL_USER_VALUE" -p"$MYSQL_PASSWORD_VALUE" --default-character-set=utf8mb4 -N -B -e "$1" 2>/dev/null \
        || printf 'error'
}

json_string() {
    if command -v python3 >/dev/null 2>&1; then
        printf %s "$1" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read())[1:-1],end="")'
    else
        printf %s "$1" | sed -e "s/\\\\/\\\\\\\\/g" -e "s/"/\\"/g" | tr "\n" " "
    fi
}

emit_string() {
    printf '"%s": "%s"' "$1" "$(json_string "$2")"
}

emit_status_counts() {
    # 把一行「状态	计数」输出为 JSON 对象；无数据时输出空对象。
    awk -F'\t' 'BEGIN{first=1} NF>=2{if(!first)printf ",\n"; first=0; printf "    \"%s\": %s", $1, $2}'
}

echo "{"
printf '  "schema_version": "%s",\n' "$SCHEMA_VERSION"
printf '  %s,\n' "$(emit_string run_id "$RUN_ID")"
printf '  %s,\n' "$(emit_string collected_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
printf '  %s,\n' "$(emit_string source "在服务器上只读执行 loadtest/job_db_check.sh")"
printf '  %s,\n' "$(emit_string table "$MYSQL_DATABASE_VALUE.analysis_jobs")"

if [ "$DOCKER_SUDO" = "-1" ] || [ -z "$MYSQL_USER_VALUE" ] || [ -z "$MYSQL_PASSWORD_VALUE" ]; then
    printf '  "status": "missing",\n'
    printf '  %s,\n' "$(emit_string reason "docker 不可用或凭据文件缺少 MYSQL_USER / MYSQL_PASSWORD")"
    echo '  "by_status_all_time": {},'
    echo '  "by_status_last_3h": {},'
    echo '  "jobs": [],'
    printf '  %s\n' "$(emit_string note "未取得主库数据，不得据此判定通过")"
    echo "}"
    exit 0
fi

printf '  "status": "collected",\n'
echo '  "by_status_all_time": {'
query "SELECT status, COUNT(*) FROM $MYSQL_DATABASE_VALUE.analysis_jobs GROUP BY status;" | emit_status_counts
echo
echo '  },'
echo '  "by_status_last_3h": {'
query "SELECT status, COUNT(*) FROM $MYSQL_DATABASE_VALUE.analysis_jobs WHERE created_at >= NOW() - INTERVAL 3 HOUR GROUP BY status;" | emit_status_counts
echo
echo '  },'
printf '  "distinct_users_last_3h": %s,\n' "$(query "SELECT COUNT(DISTINCT user_id) FROM $MYSQL_DATABASE_VALUE.analysis_jobs WHERE created_at >= NOW() - INTERVAL 3 HOUR;" | head -1)"

echo '  "jobs": ['
if [ -n "$JOB_LIST" ] && [ -f "$JOB_LIST" ]; then
    first=1
    while IFS= read -r job_id; do
        job_id="$(printf '%s' "$job_id" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$job_id" ] && continue
        case "$job_id" in \#*) continue ;; esac
        row="$(query "SELECT job_id, user_id, session_id, status, attempt_count, created_at, started_at, finished_at, COALESCE(execution_state,'null'), COALESCE(execution_release_reason,'null'), COALESCE(execution_released_at,'null') FROM $MYSQL_DATABASE_VALUE.analysis_jobs WHERE job_id='$job_id';")"
        [ "$first" = "1" ] && first=0 || printf ',\n'
        if [ -z "$row" ] || [ "$(printf '%s' "$row" | head -c 7)" = "missing" ] || [ "$(printf '%s' "$row" | head -c 5)" = "error" ]; then
            printf '    {"job_id": "%s", "status": "not_found", "missing": true}' "$job_id"
            continue
        fi
        printf '%s' "$row" | python3 -c '
import json, sys
line = sys.stdin.readline().rstrip("\n")
parts = line.split("\t")
parts += ["missing"] * (11 - len(parts))
record = {
    "job_id": parts[0],
    "user_id": parts[1],
    "session_id": parts[2],
    "status": parts[3],
    "attempt_count": parts[4],
    "created_at": parts[5],
    "started_at": parts[6],
    "finished_at": parts[7],
    "execution_state": parts[8],
    "execution_release_reason": parts[9],
    "execution_released_at": parts[10],
}
print(json.dumps(record, ensure_ascii=False), end="")
'
    done < "$JOB_LIST"
    echo
else
    printf '    {"job_id": null, "missing": true, "reason": "未提供本轮 Job 清单文件，只有全库分布可用"}'
    echo
fi
echo '  ],'

printf '  "failed_jobs": ['
query "SELECT job_id, user_id, session_id, created_at, finished_at, attempt_count, COALESCE(error_message,'') FROM $MYSQL_DATABASE_VALUE.analysis_jobs WHERE status='failed' ORDER BY id;" | python3 -c '
import json, sys
rows = [line.rstrip("\n").split("\t") for line in sys.stdin if line.strip()]
out = []
for row in rows:
    row = row + [""] * (7 - len(row))
    out.append({
        "job_id": row[0], "user_id": row[1], "session_id": row[2],
        "created_at": row[3], "finished_at": row[4],
        "attempt_count": row[5], "error_message": row[6],
    })
print(json.dumps(out, ensure_ascii=False)[1:-1], end="")
'
echo
echo '  ],'
printf '  %s\n' "$(emit_string note "只读核对；不输出 Prompt、模型正文或用户文件内容")"
echo "}"
