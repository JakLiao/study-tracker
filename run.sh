#!/usr/bin/env bash
# ====== study-tracker 一键启动 (WSL2 / Linux) ======
# 用法:
#   bash run.sh                 # 默认启动
#   bash run.sh --port 9000     # 自定义端口
#
# 前置:
#   1. 已复制 .env.example 为 .env 并填入 MINIMAX_API_KEY (可选, 不填也能跑)
#   2. 已 pip install -r requirements.txt

set -e
cd "$(dirname "$0")"

# 检查 .env
if [ ! -f ".env" ]; then
  echo "[WARN] .env 不存在, 仅手动录入可用, AI 解析将不可用"
  echo "       复制 .env.example 为 .env 并填入 MINIMAX_API_KEY 后重启"
fi

# 读 .env (避免泄漏)
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8876}"
# 数据目录 (历史 SQLite 归档 / 导出临时文件)
mkdir -p data

# 数据库连接串: 只从环境变量 / .env 读, 这里不留带密码的兜底值
# (兜底值进版本库 = 密码进版本库)。上面已 source .env, 所以正常路径
# 这里一定有值; 真的没配就让 Python 侧报"去 .env 里填"。
DATABASE_URL="${DATABASE_URL:-}"

# 解析自定义参数
for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-8876}" ;;
    --host=*) HOST="${arg#*=}" ;;
  esac
done

# 打印数据库目标时脱敏密码, 日志/截图里不泄漏凭据
DB_DISPLAY="$(printf '%s' "$DATABASE_URL" | sed -E 's#(://[^:/@]+:)[^@]*@#\1***@#')"

echo "==============================================="
echo " study-tracker v1 · http://${HOST}:${PORT}"
echo " AI 模型: ${MINIMAX_MODEL:-MiniMax-M3}"
echo " AI Key : $([ -n "${MINIMAX_API_KEY:-}" ] && echo 已配置 || echo 未配置)"
echo " DB     : ${DB_DISPLAY}"
echo "==============================================="

exec env DATABASE_URL="$DATABASE_URL" python3 -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" --log-level info