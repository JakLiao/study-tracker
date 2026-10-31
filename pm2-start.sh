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
#   pm2 start ./pm2-start.sh --name study-tracker --cwd "$(pwd)" --interpreter bash
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

# exec: 让 uvicorn 取代本脚本成为 PM2 的直接子进程
exec python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info
