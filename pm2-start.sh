#!/usr/bin/env bash
# ====== study-tracker · PM2 启动包装器 ======
#
# 用途: 交给 PM2 监督, 实现崩溃自动拉起 + 开机自启 (pm2 startup/save)
#
# 为什么需要这个包装器 (不能直接 pm2 start python3 -m uvicorn):
#   1. MINIMAX_API_KEY 只存在于 ~/.bashrc, PM2 不 source bashrc -> 不注入就 AI 全废
#   2. WSL2 下 HTTPS_PROXY 会把 127.0.0.1 请求也走代理 -> 502, 必须 NO_PROXY
#   3. 必须 exec 前台运行, PM2 才能直接监督 uvicorn 进程本身
#      (service.sh 的 setsid 脱离法会让 PM2 跟踪不到子进程血缘)
#
# 用法 (勿手工后台跑, 交给 PM2):
#   pm2 start ecosystem.config.js
#
# 为什么参数要写进 ecosystem.config.js 而不是命令行: PM2 的重启预算、
# 退避间隔这些参数决定"数据库晚启动时服务能不能自愈", 属于服务契约的一部分,
# 必须进版本库 —— 只写在某个人的 shell 历史里, 换台机器就丢了。
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8876}"

# PostgreSQL 连接串. PM2 不读 .env, 所以这里自己 source 一次;
# 真实密码只在 .env 里 (已 gitignore), 不在版本库中留兜底值。
if [ -f ".env" ]; then
  set +u
  set +e
  set -a
  # shellcheck disable=SC1091
  . ".env" >/dev/null 2>&1
  set +a
  set -e
  set -u
fi
export DATABASE_URL="${DATABASE_URL:-}"

# 本机直连, 绕过 OpenClaw / WSL2 代理 (代理会把 127.0.0.1 请求打成 502)
# ⚠️ 2026-09-29 15:38 新增 api.minimaxi.com:
#   PM2 daemon 是从 OpenClaw exec 会话继承的, 带着 OpenClaw 出站代理
#   (127.0.0.1:39773, 凭据会轮换). 长驻进程拿的是**启动那一刻的旧凭据**,
#   轮换后即失效 -> CONNECT tunnel 407 -> requests 抛 ProxyError -> AI 解析全废.
#   api.minimaxi.com 是国内站, 直连即可 (实测 HTTP 404/0.22s = 网络层通),
#   故列入 NO_PROXY 绕开代理, 而不是去修那套轮换凭据。
export NO_PROXY="127.0.0.1,localhost,::1,api.minimaxi.com"
export no_proxy="$NO_PROXY"

# 从 ~/.bashrc 取 MINIMAX_API_KEY, 只进进程内存, 不落盘
# 必须在子 shell 里加载且临时关掉 set -u/set -e:
# 本机 .bashrc 会 source ROS2 的 setup.bash, 它引用未绑定变量
# (AMENT_TRACE_SETUP_FILES 等), 在 set -u 下会让整个脚本静默退出。
if [ -z "${MINIMAX_API_KEY:-}" ]; then
  MINIMAX_API_KEY="$(
    set +u
    set +e
    . "$HOME/.bashrc" >/dev/null 2>&1 || true
    printf '%s' "${MINIMAX_API_KEY:-}"
  )"
fi
export MINIMAX_API_KEY

if [ -z "${MINIMAX_API_KEY:-}" ]; then
  echo "[WARN] MINIMAX_API_KEY 未取到, AI 解析将不可用 (手动录入照常)" >&2
fi

mkdir -p run

# ====== 等数据库就绪 (不靠 PM2 重启兜底) ======
# 数据库没起来时, 应用进程 1 秒内就死; PM2 默认 15 次重试 2 秒烧光后
# **永久不再重试** —— 根因修好也不会自己起来。详见 scripts/wait_for_db.py
# 顶部注释。这里改成"活着等": 端口通了才放行 uvicorn, 进程不退出,
# PM2 就不会判定失败、不会放弃。
#
# DB_WAIT_SECONDS 默认 120s, 专门用来熬过 Docker Desktop 开机到容器 ready
# 那几十秒 (容器 start + 端口发布 + PG 接受连接)。
if ! python3 scripts/wait_for_db.py --wait-seconds "${DB_WAIT_SECONDS:-120}"; then
  echo "[FATAL] 数据库未就绪, 本次启动放弃 (PM2 会按退避策略重试本脚本)" >&2
  exit 1
fi

# exec: 让 uvicorn 取代本脚本成为 PM2 的直接子进程
exec python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info
