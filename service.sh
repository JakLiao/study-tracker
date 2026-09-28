#!/usr/bin/env bash
# ====== study-tracker 服务管理 (start / stop / restart / status / logs) ======
#
# 用法:
#   bash service.sh start   [--port 8876] [--host 127.0.0.1] [--foreground]
#   bash service.sh stop
#   bash service.sh restart [--port 8876]
#   bash service.sh status
#   bash service.sh logs [--lines 50]
#
# 说明:
#   - PID / 元信息 / 日志 落在 run/ 目录 (已 gitignore)
#   - MINIMAX_API_KEY 从 ~/.bashrc 注入当前进程内存, 不落盘 / 不打印
#   - stop 先发 SIGTERM 优雅退出, 超时才升级到 SIGKILL
#   - start 用 setsid 脱离终端, 关掉本脚本不会带走服务

set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8876}"
ACTION=""
FOREGROUND=0
STOP_TIMEOUT=10

HOST_GIVEN=0
PORT_GIVEN=0

RUN_DIR="run"
PID_FILE="$RUN_DIR/service.pid"
ENV_FILE="$RUN_DIR/service.env"
LOG_FILE="$RUN_DIR/service.log"

# 本机直连, 绕过 OpenClaw 代理 (代理会把 127.0.0.1 请求打成 502)
export NO_PROXY="127.0.0.1,localhost,::1"
export no_proxy="$NO_PROXY"

# ---------------------------------------------------------------- 工具函数

usage() {
  cat <<'EOF'
study-tracker 服务管理

用法:
  bash service.sh start   [--port N] [--host H] [--foreground]
  bash service.sh stop
  bash service.sh restart [--port N]
  bash service.sh status
  bash service.sh logs    [--lines N]

参数:
  -p, --port N     服务端口 (默认 8876, 也可用环境变量 PORT)
  -H, --host H     监听地址 (默认 127.0.0.1, 也可用环境变量 HOST)
  -f, --foreground 前台运行 (仅 start), Ctrl+C 退出, 不写 PID 文件
  -n, --lines N    logs 打印行数 (默认 50)
  -h, --help       显示本帮助

示例:
  bash service.sh start                  # 后台起 8876
  bash service.sh start --port 9000      # 换端口起
  bash service.sh status                 # 看运行状态 + 健康检查
  bash service.sh logs --lines 200       # 看最近日志
  bash service.sh stop                   # 优雅停
EOF
}

log() { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; }

load_key() {
  if [ -z "${MINIMAX_API_KEY:-}" ] && [ -f "$HOME/.bashrc" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$HOME/.bashrc" >/dev/null 2>&1 || true
    set +a
  fi
}

# 从运行中的服务读取真实 host/port (命令行未显式指定时才用)
load_meta() {
  if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    . "$ENV_FILE"
  fi
}

save_meta() {
  mkdir -p "$RUN_DIR"
  printf 'HOST=%s\nPORT=%s\n' "$HOST" "$PORT" >"$ENV_FILE"
}

service_pid() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(tr -d '[:space:]' <"$PID_FILE")"
  [ -n "$pid" ] || return 1
  printf '%s' "$pid"
}

is_running() {
  local pid
  pid="$(service_pid)" || return 1
  kill -0 "$pid" 2>/dev/null
}

# 确认 PID 确实是本项目的 uvicorn, 避免 PID 被系统回收后误杀无关进程
pid_is_ours() {
  local pid="$1"
  local cmd
  cmd="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *uvicorn*app.main:app*) return 0 ;;
    *) return 1 ;;
  esac
}

wait_health() {
  local tries="${1:-40}"
  local i=0
  while [ "$i" -lt "$tries" ]; do
    if curl -fsS -m 3 "http://${HOST}:${PORT}/api/health" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 0.5
  done
  return 1
}

# ---------------------------------------------------------------- 子命令

do_start() {
  if is_running && pid_is_ours "$(service_pid)"; then
    local run_host run_port
    run_host="$(. "$ENV_FILE" 2>/dev/null; printf '%s' "$HOST")"
    run_port="$(. "$ENV_FILE" 2>/dev/null; printf '%s' "$PORT")"
    log "已在运行: PID $(service_pid)  http://${run_host}:${run_port}"
    if [ "$run_port" != "$PORT" ]; then
      warn "你请求的是 ${PORT}, 但服务跑在 ${run_port}; 先 stop 再 start, 或用 restart --port ${PORT}"
    fi
    return 0
  fi
  [ -f "$PID_FILE" ] && rm -f "$PID_FILE"

  load_key

  if [ "$FOREGROUND" -eq 1 ]; then
    log "前台启动 http://${HOST}:${PORT}  (Ctrl+C 退出)"
    exec env HOST="$HOST" PORT="$PORT" bash run.sh
  fi

  mkdir -p "$RUN_DIR"
  save_meta
  : >"$LOG_FILE"

  setsid nohup env HOST="$HOST" PORT="$PORT" bash run.sh >>"$LOG_FILE" 2>&1 </dev/null &

  # run.sh 会 exec uvicorn, 这里按命令行抓真实 PID
  local pid="" i=0
  while [ "$i" -lt 40 ]; do
    pid="$(pgrep -f "uvicorn app.main:app --host ${HOST} --port ${PORT}" 2>/dev/null | head -n1 || true)"
    [ -n "$pid" ] && break
    i=$((i + 1))
    sleep 0.25
  done

  if [ -z "$pid" ]; then
    fail "启动失败, 未发现 uvicorn 进程, 最近日志:"
    tail -n 20 "$LOG_FILE" >&2 || true
    return 1
  fi

  printf '%s' "$pid" >"$PID_FILE"

  if wait_health; then
    local key_state="未配置, 仅手动录入可用"
    [ -n "${MINIMAX_API_KEY:-}" ] && key_state="已配置"
    log "启动成功: PID ${pid}  http://${HOST}:${PORT}"
    log "AI Key: ${key_state}"
    log "日志: ${LOG_FILE}"
  else
    warn "进程已起 (PID ${pid}) 但健康检查未通过, 请查日志: ${LOG_FILE}"
    return 1
  fi
}

do_stop() {
  if ! is_running; then
    log "服务未在运行"
    if [ -f "$PID_FILE" ]; then
      rm -f "$PID_FILE"
      log "已清理陈旧 PID 文件"
    fi
    return 0
  fi

  local pid
  pid="$(service_pid)"

  if ! pid_is_ours "$pid"; then
    warn "PID ${pid} 已不是本项目的 uvicorn 进程, 只清理 PID 文件不动进程"
    rm -f "$PID_FILE"
    return 0
  fi

  log "停止中 (PID ${pid}) ..."
  kill -TERM "$pid" 2>/dev/null || true

  local i=0
  local max=$((STOP_TIMEOUT * 2))
  while [ "$i" -lt "$max" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      log "已停止"
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done

  warn "优雅退出超时, 升级到 SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
  sleep 0.5
  rm -f "$PID_FILE"
  log "已强制停止"
}

do_status() {
  if is_running && pid_is_ours "$(service_pid)"; then
    local pid
    pid="$(service_pid)"
    log "运行中  PID=${pid}  http://${HOST}:${PORT}"
    local health
    if health="$(curl -fsS -m 3 "http://${HOST}:${PORT}/api/health" 2>/dev/null)"; then
      log "健康检查: ${health}"
    else
      warn "健康检查: 无响应"
    fi
    return 0
  fi

  if [ -f "$PID_FILE" ]; then
    warn "PID 文件存在但进程已不在, 已清理"
    rm -f "$PID_FILE"
  fi
  log "未运行  (预期 http://${HOST}:${PORT})"
  return 1
}

do_logs() {
  if [ ! -f "$LOG_FILE" ]; then
    warn "日志不存在: ${LOG_FILE}"
    return 1
  fi
  tail -n "$LOG_LINES" "$LOG_FILE"
}

# ---------------------------------------------------------------- 参数解析

LOG_LINES=50

while [ $# -gt 0 ]; do
  case "$1" in
    start | stop | restart | status | logs)
      ACTION="$1"
      shift
      ;;
    -p | --port)
      PORT="${2:?--port 需要一个值}"
      PORT_GIVEN=1
      shift 2
      ;;
    --port=*)
      PORT="${1#*=}"
      PORT_GIVEN=1
      shift
      ;;
    -H | --host)
      HOST="${2:?--host 需要一个值}"
      HOST_GIVEN=1
      shift 2
      ;;
    --host=*)
      HOST="${1#*=}"
      HOST_GIVEN=1
      shift
      ;;
    -f | --foreground)
      FOREGROUND=1
      shift
      ;;
    -n | --lines)
      LOG_LINES="${2:?--lines 需要一个值}"
      shift 2
      ;;
    --lines=*)
      LOG_LINES="${1#*=}"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      fail "未知参数: $1"
      usage
      exit 1
      ;;
  esac
done

[ -n "$ACTION" ] || ACTION="status"

# stop / status / logs / restart 未显式给 host/port 时, 沿用上次运行的值
if [ "$HOST_GIVEN" -eq 0 ] || [ "$PORT_GIVEN" -eq 0 ]; then
  if [ -f "$ENV_FILE" ]; then
    meta_host="$(. "$ENV_FILE"; printf '%s' "$HOST")"
    meta_port="$(. "$ENV_FILE"; printf '%s' "$PORT")"
    [ "$HOST_GIVEN" -eq 0 ] && HOST="$meta_host"
    [ "$PORT_GIVEN" -eq 0 ] && PORT="$meta_port"
  fi
fi

case "$ACTION" in
  start) do_start ;;
  stop) do_stop ;;
  restart)
    do_stop
    echo
    do_start
    ;;
  status) do_status ;;
  logs) do_logs ;;
esac
