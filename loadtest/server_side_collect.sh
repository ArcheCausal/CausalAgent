#!/usr/bin/env bash
# 在服务器上执行，产出一次单次快照的只读采集：主机、容器、镜像、数据库与出网探测。
#
# 文档职责：定义负载前后单次快照的字段与缺失状态表示，作为报告“服务器侧”一节的唯一来源。
#
# 适用范围：在 staging 服务器上采集基线或收尾快照时使用；与 stage_resource_sample.sh 的
# 定时采样互补，本脚本只回答“某一时刻是什么状态”。
#
# 用法：
#   sudo bash loadtest/server_side_collect.sh <run_id> <before|after> > server-side-<run_id>.json
#   python -m runner attach-server --from <该文件>       # 负载前基线
#   python -m runner attach-after  --from <该文件>       # 停止提交后的收尾
#
# 只读执行：不修改服务、数据或配置，不输出密钥、口令、Prompt、模型正文或用户文件内容。

set -u

RUN_ID="${1:-unknown-run}"
PHASE="${2:-snapshot}"
SCHEMA_VERSION="loadtest-server-collect-v1"

PROJECT="${COMPOSE_PROJECT_NAME:-causalagent-staging}"
ENV_FILE="${CAUSALAGENT_ENV_FILE:-/opt/causalagent-staging/.env.staging}"
API_BASE="${LOADTEST_BASE_URL:-http://127.0.0.1:8088}"

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

read_env() {
    if [ ! -f "$ENV_FILE" ]; then return 1; fi
    if [ -r "$ENV_FILE" ]; then
        grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2-
    else
        sudo -n grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2-
    fi
}

json_string() {
    # 转义为 JSON 字符串内容；优先用 python3，退化到 sed。
    if command -v python3 >/dev/null 2>&1; then
        printf %s "$1" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read())[1:-1],end="")'
    else
        printf %s "$1" | sed -e "s/\\\\/\\\\\\\\/g" -e "s/"/\\"/g" | tr "\n" " "
    fi
}

emit_string() {
    printf '"%s": "%s"' "$1" "$(json_string "$2")"
}

MYSQL_USER_VALUE="$(read_env MYSQL_USER)"
MYSQL_DATABASE_VALUE="$(read_env MYSQL_DATABASE)"
[ -z "$MYSQL_DATABASE_VALUE" ] && MYSQL_DATABASE_VALUE="rag_eval_staging"

mysql_query() {
    if [ -z "$MYSQL_USER_VALUE" ]; then
        printf 'missing'
        return 0
    fi
    dk exec "$PROJECT-mysql-primary-1" sh -c '
        . /opt/causalagent/secret-env.sh
        MYSQL_PWD="$(secret_value MYSQL_PASSWORD)" || exit 1
        export MYSQL_PWD
        exec mysql "$@"
    ' _ -u"$MYSQL_USER_VALUE" --default-character-set=utf8mb4 -N -B -e "$1" 2>/dev/null \
        || printf 'error'
}

replica_query() {
    if [ "$DOCKER_SUDO" = "-1" ]; then printf 'missing'; return 0; fi
    dk exec "$PROJECT-mysql-replica-1" sh -c '
        . /opt/causalagent/secret-env.sh
        MYSQL_PWD="$(secret_value MYSQL_ROOT_PASSWORD)" || exit 1
        export MYSQL_PWD
        exec mysql "$@"
    ' _ -uroot -N -B -e "$1" 2>/dev/null \
        || printf 'error'
}

echo "{"
printf '  "schema_version": "%s",\n' "$SCHEMA_VERSION"
printf '  %s,\n' "$(emit_string run_id "$RUN_ID")"
printf '  %s,\n' "$(emit_string phase "$PHASE")"
printf '  %s,\n' "$(emit_string collected_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
printf '  %s,\n' "$(emit_string source "在服务器上只读执行 loadtest/server_side_collect.sh")"
printf '  %s,\n' "$(emit_string hostname "$(hostname)")"
printf '  %s,\n' "$(emit_string compose_project "$PROJECT")"
printf '  %s,\n' "$(emit_string uptime "$(uptime -p 2>/dev/null || uptime)")"
printf '  %s,\n' "$(emit_string load_average "$(cat /proc/loadavg 2>/dev/null || echo unknown)")"
printf '  "cpu_count": %s,\n' "$(nproc 2>/dev/null || echo 0)"

printf '  %s,\n' "$(emit_string memory_mb "$(free -m 2>/dev/null | awk '/^Mem:/{print "total=" $2 " used=" $3 " available=" $7}')")"
printf '  %s,\n' "$(emit_string swap_mb "$(free -m 2>/dev/null | awk '/^Swap:/{print "total=" $2 " used=" $3 " free=" $4}')")"
printf '  %s,\n' "$(emit_string disk "$(df -h / 2>/dev/null | awk 'NR==2{print $2 " total, " $3 " used, " $4 " avail, " $5 " used"}')")"
printf '  %s,\n' "$(emit_string disk_inodes "$(df -i / 2>/dev/null | awk 'NR==2{print $2 " total, " $3 " used, " $4 " free, " $5 " used"}')")"

if [ "$DOCKER_SUDO" != "-1" ]; then
    # 逐行读取再用 ; 连接；tr 在这里会把多字节字符拆成单字节，必须避免。
    containers="$(dk ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null | paste -sd ';' -)"
    exited="$(dk ps -a --filter status=exited --format '{{.Names}} {{.Status}}' 2>/dev/null | paste -sd ';' -)"
    oom=""; restarts=""
    for name in $(dk ps -a --format '{{.Names}}' 2>/dev/null); do
        flag="$(dk inspect -f '{{.State.OOMKilled}}' "$name" 2>/dev/null)"
        [ "$flag" = "true" ] && oom="$oom$name,"
        count="$(dk inspect -f '{{.RestartCount}}' "$name" 2>/dev/null)"
        restarts="$restarts$name=$count,"
    done
    up_count="$(dk ps -q 2>/dev/null | wc -l | tr -d " ")"
    # 优先给出容器配置里记录的带 digest 引用；本地短 ID 不能用于版本对照。
    app_image="$(dk inspect -f '{{.Config.Image}}' "$PROJECT-app-1" 2>/dev/null)"
    [ -z "$app_image" ] && app_image="$(dk ps --format '{{.Names}}|{{.Image}}' 2>/dev/null | grep -m1 "$PROJECT-app-1" | cut -d'|' -f2)"
    mcp_image="$(dk inspect -f '{{.Config.Image}}' "$PROJECT-causal-mcp-1" 2>/dev/null)"
    [ -z "$mcp_image" ] && mcp_image="$(dk ps --format '{{.Names}}|{{.Image}}' 2>/dev/null | grep -m1 "$PROJECT-causal-mcp-1" | cut -d'|' -f2)"
    app_rev="$(dk inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$PROJECT-app-1" 2>/dev/null)"
    printf '  "containers_up": %s,\n' "${up_count:-0}"
    printf '  %s,\n' "$(emit_string containers "$containers")"
    printf '  %s,\n' "$(emit_string containers_exited "$exited")"
    printf '  %s,\n' "$(emit_string oom_killed "$oom")"
    printf '  %s,\n' "$(emit_string restart_counts "$restarts")"
    printf '  "images": {\n'
    printf '    %s,\n' "$(emit_string app "$app_image")"
    printf '    %s,\n' "$(emit_string mcp "$mcp_image")"
    printf '    %s\n' "$(emit_string app_image_revision_label "$app_rev")"
    printf '  },\n'

    if [ -n "$MYSQL_USER_VALUE" ]; then
        tcon="$(mysql_query "SHOW GLOBAL STATUS WHERE Variable_name='Threads_connected';" | awk '{print $2}')"
        trun="$(mysql_query "SHOW GLOBAL STATUS WHERE Variable_name='Threads_running';" | awk '{print $2}')"
        tcre="$(mysql_query "SHOW GLOBAL STATUS WHERE Variable_name='Threads_created';" | awk '{print $2}')"
        abor="$(mysql_query "SHOW GLOBAL STATUS WHERE Variable_name='Aborted_connects';" | awk '{print $2}')"
        upti="$(mysql_query "SHOW GLOBAL STATUS WHERE Variable_name='Uptime';" | awk '{print $2}')"
        rep_io="$(replica_query "SELECT service_state FROM performance_schema.replication_connection_status;" | head -1)"
        rep_err="$(replica_query "SELECT COALESCE(last_error_message, 'none') FROM performance_schema.replication_connection_status;" | head -1)"
        read_only="$(replica_query "SHOW VARIABLES WHERE Variable_name='read_only';" | awk '{print $2}')"
        read_ok="$(replica_query "SELECT 1;" | head -1)"
        jobs="$(mysql_query "SELECT CONCAT('total=', COUNT(*), ' queued=', SUM(status='queued'), ' running=', SUM(status='running'), ' succeeded=', SUM(status='succeeded'), ' failed=', SUM(status='failed'), ' canceled=', SUM(status='canceled'), ' waiting_input=', SUM(status='waiting_input')) FROM $MYSQL_DATABASE_VALUE.analysis_jobs;" | head -1)"
        printf '  "mysql": {\n'
        printf '    %s,\n' "$(emit_string threads_connected "$tcon")"
        printf '    %s,\n' "$(emit_string threads_running "$trun")"
        printf '    %s,\n' "$(emit_string threads_created "$tcre")"
        printf '    %s,\n' "$(emit_string aborted_connects "$abor")"
        printf '    %s,\n' "$(emit_string uptime_seconds "$upti")"
        printf '    %s,\n' "$(emit_string replication_io_state "$rep_io")"
        printf '    %s,\n' "$(emit_string replication_last_error "$rep_err")"
        printf '    %s,\n' "$(emit_string replica_read_only "$read_only")"
        printf '    %s\n' "$(emit_string replica_read_query "$read_ok")"
        printf '  },\n'
        printf '  "job_queue": {\n'
        printf '    %s,\n' "$(emit_string table "$MYSQL_DATABASE_VALUE.analysis_jobs")"
        printf '    %s\n' "$(emit_string counts "$jobs")"
        printf '  },\n'
    else
        printf '  "mysql": {"status": "missing", "reason": "部署环境文件无 MYSQL_USER"},\n'
        printf '  "job_queue": {"status": "missing", "reason": "凭据不可用"},\n'
    fi
else
    printf '  "containers_up": "missing",\n'
    printf '  "containers": "docker-unavailable",\n'
    printf '  "containers_exited": "missing",\n'
    printf '  "oom_killed": "missing",\n'
    printf '  "restart_counts": "missing",\n'
    printf '  "images": {"status": "missing"},\n'
    printf '  "mysql": {"status": "missing", "reason": "docker 不可用"},\n'
    printf '  "job_queue": {"status": "missing", "reason": "docker 不可用"},\n'
fi

probe_host() {
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$1" <<'PY'
import socket, sys
host = sys.argv[1]
try:
    ip = socket.gethostbyname(host)
except Exception as exc:
    print("dns-fail " + type(exc).__name__)
    raise SystemExit
try:
    sock = socket.create_connection((host, 443), timeout=8)
    print("tcp-ok " + str(sock.getpeername()[0]))
    sock.close()
except Exception as exc:
    print("tcp-fail ip=" + ip + " " + type(exc).__name__ + " " + str(exc))
PY
    else
        echo "python3-unavailable"
    fi
}

printf '  %s,\n' "$(emit_string api_entry "$API_BASE")"
echo '  "network_probe": {'
printf '    %s,\n' "$(emit_string huggingface_co "$(probe_host huggingface.co)")"
printf '    %s,\n' "$(emit_string hf_mirror_com "$(probe_host hf-mirror.com)")"
echo '    "note": "仅确认 TCP 可达性，不下载权重"'
echo '  },'
printf '  %s\n' "$(emit_string note "只读采集；不修改服务、数据或配置；不包含任何密钥、口令、Prompt 或用户文件内容")"
echo "}"
