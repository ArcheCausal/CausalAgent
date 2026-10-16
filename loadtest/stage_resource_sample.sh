#!/usr/bin/env bash
# 在服务器上执行，产出一次负载窗口内主机与容器资源的定时采样。
#
# 文档职责：定义负载与排空阶段的主机资源、容器资源、数据库连接数和 Job 队列长度采样格式；
# 采样结果由 python -m runner attach-sample 写入对应档位的运行摘要。
#
# 适用范围：在 staging 服务器上按档位执行采样时使用；只在本地运行的压测客户端不读取本脚本。
#
# 用法：
#   bash loadtest/stage_resource_sample.sh <run_id> <持续秒数> [采样间隔秒] [档位名] > sample.json
#
# 采样时长必须大于负载时长，因为停止提交后 Job 还会继续执行。采样进程在收尾前不会写出
# finished_at，因此必须在进程结束之后再取回文件，否则 JSON 会被截断。只读 /proc、docker
# stats 与 SHOW GLOBAL STATUS，不修改任何服务、数据或配置，不输出密钥与口令。

set -u

RUN_ID="${1:-unknown-run}"
DURATION="${2:-120}"
INTERVAL="${3:-10}"
LABEL="${4:-$RUN_ID}"
SCHEMA_VERSION="loadtest-server-sample-v1"

PROJECT="${COMPOSE_PROJECT_NAME:-causalagent-staging}"
ENV_FILE="${CAUSALAGENT_ENV_FILE:-/opt/causalagent-staging/.env.staging}"

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
    # 从受控凭据文件读取一个键；文件缺失、不可读或键缺失时返回空。
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

db_query() {
    # 只执行 SELECT / SHOW；凭据只出现在进程参数里，不写入输出。
    if [ -z "$MYSQL_USER_VALUE" ] || [ -z "$MYSQL_PASSWORD_VALUE" ]; then
        printf 'missing'
        return 0
    fi
    dk exec "$PROJECT-mysql-primary-1" mysql \
        -u"$MYSQL_USER_VALUE" -p"$MYSQL_PASSWORD_VALUE" -N -B -e "$1" 2>/dev/null \
        || printf 'error'
}

host_sample() {
    # 输出一行主机资源采样；字段缺失时写成 null，不用 0 冒充。
    read -r load1 load5 load15 _ < /proc/loadavg 2>/dev/null || load1=null
    mem="$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{printf "%s %s", t, a}' /proc/meminfo 2>/dev/null)"
    mem_total="${mem%% *}"; mem_avail="${mem##* }"
    swap="$(awk '/^SwapTotal:/{t=$2} /^SwapFree:/{f=$2} END{printf "%s %s", t, f}' /proc/meminfo 2>/dev/null)"
    swap_total="${swap%% *}"; swap_free="${swap##* }"
    cpu="$(awk '/^cpu /{i=$5+$6; t=0; for(n=2;n<=NF;n++) t+=$n; printf "%s %s", t, i}' /proc/stat 2>/dev/null)"
    cpu_total="${cpu%% *}"; cpu_idle="${cpu##* }"
    disk_sectors="$(awk '/^(r|w)sect/{v+=$3} END{print v+0}' /proc/diskstats 2>/dev/null)"
    printf '{"timestamp": "%s", "load1": %s, "load5": %s, "load15": %s, ' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${load1:-null}" "${load5:-null}" "${load15:-null}"
    printf '"mem_total_kb": %s, "mem_available_kb": %s, "swap_total_kb": %s, "swap_free_kb": %s, ' \
        "${mem_total:-null}" "${mem_avail:-null}" "${swap_total:-null}" "${swap_free:-null}"
    printf '"cpu_total_jiffies": %s, "cpu_idle_jiffies": %s, "disk_sectors": %s}' \
        "${cpu_total:-null}" "${cpu_idle:-null}" "${disk_sectors:-0}"
}

container_resources() {
    # 输出一份容器资源快照；docker 不可用时给出明确状态。
    if [ "$DOCKER_SUDO" = "-1" ]; then
        printf '{"status": "unavailable", "reason": "docker 不可用"}'
        return 0
    fi
    stats="$(dk stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' 2>/dev/null \
        | awk 'BEGIN{first=1; printf "["} {if(!first)printf ", "; first=0; printf "{\"name\": \"%s\", \"cpu\": \"%s\", \"mem\": \"%s\"}", $1, $2, $3} END{printf "]"}')"
    # 重启次数与 OOM 用一次 docker ps 取全；逐容器 inspect 会把采样间隔拖长数倍。
    restart_counts="$(dk ps -a --format '{{.Names}}={{.State}}/{{.Status}}' 2>/dev/null | paste -sd ',' -)"
    # 采样窗口内不逐容器读取 OOMKilled 标记（成本过高），改由 server_side_collect.sh 的
    # 单次快照提供 OOM 结论；这里给出已退出容器名单作为运行期异常的下界信号。
    exited="$(dk ps -a --filter status=exited --format '{{.Names}}' 2>/dev/null | paste -sd ',' -)"
    printf '{"status": "collected", "stats": %s, "container_states": "%s", "exited_containers": "%s"}' \
        "${stats:-[]}" "$restart_counts" "$exited"
}

queue_snapshot() {
    # 输出一次 Job 队列长度快照；数据库不可用时给出明确状态。
    raw="$(db_query "SELECT CONCAT(COUNT(*), ' ', SUM(status='queued'), ' ', SUM(status='running'), ' ', SUM(status='draining')) FROM $MYSQL_DATABASE_VALUE.analysis_jobs;")"
    case "$raw" in
        missing|error)
            printf '{"status": "%s"}' "$raw"
            ;;
        *)
            set -- $raw
            printf '{"status": "collected", "total": %s, "queued": %s, "running": %s, "draining": %s}' \
                "${1:-null}" "${2:-null}" "${3:-null}" "${4:-null}"
            ;;
    esac
}

connections_snapshot() {
    # 输出一次数据库连接数快照。
    raw="$(db_query "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected','Threads_running','Aborted_connects','Uptime');")"
    case "$raw" in
        missing|error)
            printf '{"status": "%s"}' "$raw"
            ;;
        *)
            connected="missing"; running="missing"; aborted="missing"; uptime="missing"
            while IFS='	' read -r key value; do
                case "$key" in
                    Threads_connected) connected="${value:-null}" ;;
                    Threads_running) running="${value:-null}" ;;
                    Aborted_connects) aborted="${value:-null}" ;;
                    Uptime) uptime="${value:-null}" ;;
                esac
            done <<EOF
$raw
EOF
            printf '{"status": "collected", "threads_connected": %s, "threads_running": %s, "aborted_connects": %s, "uptime_seconds": %s}' \
                "$connected" "$running" "$aborted" "$uptime"
            ;;
    esac
}

echo "{"
printf '  "schema_version": "%s",\n' "$SCHEMA_VERSION"
printf '  "run_id": "%s",\n' "$RUN_ID"
printf '  "label": "%s",\n' "$LABEL"
printf '  "started_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '  "interval_seconds": %s,\n' "$INTERVAL"
printf '  "cpu_count": %s,\n' "$(nproc 2>/dev/null || echo 0)"
echo '  "samples": ['

end=$(( $(date +%s) + DURATION ))
first=1
while :; do
    if [ "$first" = "1" ]; then first=0; else printf ',\n'; fi
    printf '    {"host": %s, "containers": %s, "queue": %s, "connections": %s}' \
        "$(host_sample)" "$(container_resources)" "$(queue_snapshot)" "$(connections_snapshot)"
    [ "$(date +%s)" -ge "$end" ] && break
    sleep "$INTERVAL"
done

echo
echo '  ],'
printf '  "finished_at": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "}"
