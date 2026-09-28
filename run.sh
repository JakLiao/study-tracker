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
PORT="${PORT:-8765}"

# 数据目录
mkdir -p data

# 解析自定义参数
for arg in "$@"; do
  case "$arg" in
    --port=*) PORT="${arg#*=}" ;;
    --port) shift; PORT="${1:-8765}" ;;
    --host=*) HOST="${arg#*=}" ;;
  esac
done

echo "==============================================="
echo " study-tracker v1 · http://${HOST}:${PORT}"
echo " AI 模型: ${MINIMAX_MODEL:-MiniMax-M3}"
echo " AI Key : $([ -n "${MINIMAX_API_KEY:-}" ] && echo 已配置 || echo 未配置)"
echo "==============================================="

exec python3 -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" --log-level info