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

⚠️  本服务默认由 PM2 托管 (pm2-start.sh, 常驻自启).
    这种情况下 start/stop/restart 会被拒绝, 请用:
        pm2 restart study-tracker
    service.sh 只在"没有 PM2 托管"的场景下才真正拉起进程.

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
  bash service.sh status                 # 看运行状态 + 健康检查 + 托管方式
  pm2 restart study-tracker              # 日常重启 (PM2 托管时用这条)
  bash service.sh start --port 9000      # 无 PM2 托管时换端口起
  bash service.sh logs --lines 200       # 看最近日志
  bash service.sh stop                   # 无 PM2 托管时优雅停
EOF
}

log() { printf '%s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; }

load_key() {
  if [ -z "${MINIMAX_API_KEY:-}" ] && [ -f "$HOME/.bashrc" ]; then
    # 必须在子 shell 里加载, 且临时关掉 set -u/set -e。
    # 本机 .bashrc 会 source ROS2 的 setup.bash, 它引用了未绑定变量
    # (AMENT_TRACE_SETUP_FILES 等), 在 set -u 下会让整个脚本静默退出 ——
    # 曾导致 `start` 无任何输出且服务没起来, 极难排查。
    # `|| true` 兜不住: 退出发生在 .bashrc 内部, 不是 source 的返回值。
    MINIMAX_API_KEY="$(
      set +u
      set +e
      set -a
      # shellcheck disable=SC1090
      . "$HOME/.bashrc" >/dev/null 2>&1
      set +a
      printf '%s' "${MINIMAX_API_KEY:-}"
    )"
    export MINIMAX_API_KEY
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

# 本项目在 HOST:PORT 上的所有 uvicorn 进程 PID (可能多个, 也可能没有).
# 排除本脚本自身 —— pgrep -f 匹配的是完整命令行, 脚本命令行里带着同样的模式串.
our_pids() {
  pgrep -f "uvicorn app.main:app --host ${HOST} --port ${PORT}" 2>/dev/null \
    | grep -vx "$$" || true
}

# 收集"本项目当前真正在跑"的 PID 集合.
# 两个来源取并集, 缺一不可:
#   - PID 文件: 能认出 host/port 被手工改过、因此不在 our_pids 模式里的进程
#   - 命令行匹配: 能认出 PID 文件丢了(WSL 重启 / 误删 / 手工起过)仍然占着端口的残留进程
# 只认 PID 文件会漏掉后者, 这正是"restart 假成功"的根因 (见 do_start 注释).
collect_running_pids() {
  local out=""
  local pid p
  pid="$(service_pid || true)"
  if [ -n "$pid" ] && is_running "$pid" && pid_is_ours "$pid"; then
    out="$pid"
  fi
  for p in $(our_pids); do
    case " $out " in
      *" $p "*) ;;
      *) out="${out:+$out }$p" ;;
    esac
  done
  printf '%s' "$out"
}

# 在 our_pids 结果里挑出"不在 before 快照里"的那个 —— 即本次真正启动的进程.
new_pid_not_in() {
  local before=" $1 "
  local p
  for p in $(our_pids); do
    case "$before" in
      *" $p "*) ;;
      *) printf '%s' "$p"; return 0 ;;
    esac
  done
  return 0
}

# 尽力找出占用 PORT 的进程 PID, 仅用于给出更清楚的报错.
# 依赖 ss (WSL 一般自带); 没有就返回空, 后面还有健康检查兜底, 不影响正确性.
port_owner() {
  command -v ss >/dev/null 2>&1 || return 0
  ss -ltnpH 2>/dev/null \
    | awk -v port=":${PORT}" '$4 ~ port"$" {print}' \
    | grep -o 'pid=[0-9]\+' \
    | head -n1 \
    | cut -d= -f2
}

is_running() {
  local pid="${1:-}"
  [ -n "$pid" ] || return 1
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

# 本服务实际由 PM2 托管 (见 pm2-start.sh 与 pm2-study-tracker.service),
# PM2 会监督并在崩溃时拉起, 所以正确的重启方式是 pm2 restart, 不是直接 kill.
# 这里识别出 PM2 身份, 好在 stop/start/restart 里给出对应指引 ——
# 少了这一步, service.sh 的 kill 会被 PM2 立刻抵消掉:
# 表现为"停不掉", 而且 PID 每次都变 (PM2 换了个新子进程).
pm2_manages_us() {
  command -v pm2 >/dev/null 2>&1 || return 1
  local pm2_bin
  pm2_bin="$(command -v pm2)"
  # pm2 可能不在默认 PATH (npm 全局目录), 常见于 systemd 拉起的 daemon
  [ -x "$HOME/.npm-global/bin/pm2" ] && pm2_bin="$HOME/.npm-global/bin/pm2"
  [ -x "$HOME/.npm/bin/pm2" ] && pm2_bin="$HOME/.npm/bin/pm2"
  "$pm2_bin" jlist 2>/dev/null | grep -q '"name":"study-tracker"' || return 1
  return 0
}

# PM2 托管时, 实际干活的那条命令
pm2_restart_hint() {
  local pm2_bin="pm2"
  command -v pm2 >/dev/null 2>&1 || pm2_bin="$HOME/.npm-global/bin/pm2"
  [ -x "$pm2_bin" ] || pm2_bin="$HOME/.npm/bin/pm2"
  printf '%s' "$pm2_bin restart study-tracker"
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
  # PM2 托管时不能自己拉起: PM2 监督着同一份 pm2-start.sh, 两个管理者会打架
  # (service.sh 用 setsid 脱离, PM2 跟不到血缘; 结果就是两个进程抢同一端口).
  if pm2_manages_us; then
    log "本服务由 PM2 托管, start 已被拒绝 (避免与 PM2 双管抢同一端口)"
    fail "请用: $(pm2_restart_hint)"
    return 1
  fi

  local running
  running="$(collect_running_pids)"
  if [ -n "$running" ]; then
    log "已在运行: PID ${running}  http://${HOST}:${PORT}"
    return 0
  fi
  [ -f "$PID_FILE" ] && rm -f "$PID_FILE"

  # 端口预检: 端口被非本项目进程占着时, uvicorn 会 bind 失败立刻退出,
  # 而下面的 pgrep 仍能抓到占端口的那个进程 -> 误报"启动成功", PID 文件还写错.
  # 2026-09-29 实际踩过: restart 后旧进程没停掉, 新进程报
  # "address already in use" 退出, 脚本却打印"启动成功: PID <旧进程>".
  # 这里提前失败并直接点名占用者, 比让人去翻日志快得多.
  local squatter
  squatter="$(port_owner || true)"
  if [ -n "$squatter" ]; then
    fail "端口 ${HOST}:${PORT} 被 PID ${squatter} 占用, 且不是本项目的服务"
    fail "处理掉它 (kill ${squatter}) 或换端口: bash service.sh start --port ${PORT}"
    return 1
  fi

  load_key

  if [ "$FOREGROUND" -eq 1 ]; then
    log "前台启动 http://${HOST}:${PORT}  (Ctrl+C 退出)"
    exec env HOST="$HOST" PORT="$PORT" bash run.sh
  fi

  mkdir -p "$RUN_DIR"
  save_meta
  : >"$LOG_FILE"

  # 启动前先给"当前在跑的本项目进程"拍张快照.
  # 之后只认不在快照里的新进程 —— 否则 pgrep 会把旧进程当成本次启动的,
  # 写进 PID 文件的就是一个根本没重启过的 PID.
  local before
  before="$(our_pids | tr '\n' ' ')"

  setsid nohup env HOST="$HOST" PORT="$PORT" bash run.sh >>"$LOG_FILE" 2>&1 </dev/null &

  # run.sh 会 exec uvicorn, 这里按命令行抓真实 PID
  local pid="" i=0
  while [ "$i" -lt 40 ]; do
    pid="$(new_pid_not_in "$before")"
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
    # 健康检查没过 = 服务实际没起来. 此时 PID 文件是有害的:
    # 下次 start/status 会拿它当"在运行", 掩盖真正的启动失败. 删掉并说清.
    rm -f "$PID_FILE"
    fail "进程已起 (PID ${pid}) 但健康检查未通过, 已清理 PID 文件, 请查日志: ${LOG_FILE}"
    tail -n 20 "$LOG_FILE" >&2 || true
    return 1
  fi
}

do_stop() {
  # PM2 托管时直接 kill 会被立刻拉起 (PM2 监督着同一份 pm2-start.sh),
  # 表现为"停不掉" 且 PID 每次都变. 正确做法是交给 PM2 停.
  if pm2_manages_us; then
    local running
    running="$(collect_running_pids)"
    if [ -n "$running" ]; then
      log "本服务由 PM2 托管, stop 已被拒绝 (直接 kill 会被 PM2 立刻拉起)"
      fail "请用: $(pm2_restart_hint | sed 's/restart/stop/')"
      return 1
    fi
  fi

  local running
  running="$(collect_running_pids)"

  if [ -z "$running" ]; then
    log "服务未在运行"
    if [ -f "$PID_FILE" ]; then
      rm -f "$PID_FILE"
      log "已清理陈旧 PID 文件"
    fi
    return 0
  fi

  log "停止中 (PID ${running}) ..."
  # 逐个停, 避免误杀: collect_running_pids 已经过滤过"是不是本项目进程"
  local pid
  for pid in $running; do
    pid_is_ours "$pid" || continue
    kill -TERM "$pid" 2>/dev/null || true
  done

  local i=0
  local max=$((STOP_TIMEOUT * 2))
  while [ "$i" -lt "$max" ]; do
    local alive=""
    for pid in $running; do
      is_running "$pid" && alive="yes"
    done
    [ -z "$alive" ] && break
    sleep 0.5
    i=$((i + 1))
  done

  # 超时升级到 SIGKILL, 逐个判断
  local killed=""
  for pid in $running; do
    if is_running "$pid"; then
      warn "PID ${pid} 优雅退出超时, 升级到 SIGKILL"
      kill -9 "$pid" 2>/dev/null || true
      killed="yes"
    fi
  done
  [ -n "$killed" ] && sleep 0.5

  rm -f "$PID_FILE"
  log "已停止"
}

do_status() {
  local running
  running="$(collect_running_pids)"

  if [ -n "$running" ]; then
    log "运行中  PID=${running}  http://${HOST}:${PORT}"
    # PID 文件与实际进程不一致时要说出来 —— 这正是"以为重启了其实没重启"的现场
    local pid_file_pid
    pid_file_pid="$(service_pid || true)"
    if [ -z "$pid_file_pid" ]; then
      warn "PID 文件缺失或已失效, 但进程 ${running} 仍在跑 (restart 前建议先 stop)"
    elif [ " $running " != *" ${pid_file_pid} "* ]; then
      warn "PID 文件记的是 ${pid_file_pid}, 实际在跑的是 ${running}, 两者不一致"
    fi
    local health
    if health="$(curl -fsS -m 3 "http://${HOST}:${PORT}/api/health" 2>/dev/null)"; then
      log "健康检查: ${health}"
    else
      warn "健康检查: 无响应"
    fi
    if pm2_manages_us; then
      log "托管方式: PM2  (重启请用 $(pm2_restart_hint))"
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
    # PM2 托管时 stop/start 都会被拒, 明确告诉用户正确姿势, 免得看两条报错
    if pm2_manages_us; then
      log "本服务由 PM2 托管, 请用: $(pm2_restart_hint)"
      exit 1
    fi
    do_stop
    echo
    do_start
    ;;
  status) do_status ;;
  logs) do_logs ;;
esac
