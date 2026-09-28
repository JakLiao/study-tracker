"""全局配置 (从环境变量读, 启动期一次性加载)."""
from __future__ import annotations

import os
from pathlib import Path


# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv_if_exists() -> None:
    """手工解析 .env (避免引入 python-dotenv 依赖).
    仅在 KEY 不存在时尝试注入; 已存在的系统环境变量优先级最高.
    解析规则: KEY=VALUE, 跳过空行与 # 开头行, 不支持引号嵌套.
    """
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # 已有系统环境变量不覆盖 (系统优先)
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_if_exists()


# ====== AI 配置 ======
MINIMAX_API_KEY: str = os.environ.get("MINIMAX_API_KEY", "").strip()
MINIMAX_API_BASE: str = os.environ.get("MINIMAX_API_BASE", "https://api.minimaxi.com").strip()
# v1 固定一个模型, 不做切换. 已实测 MiniMax-M3 对当前账号可用.
MINIMAX_MODEL: str = os.environ.get("MINIMAX_MODEL", "MiniMax-M3").strip()
# 默认 120s: 实测 9.22 三科合并样本解析约 58s, 60s 偏贴边 (上次只差 1.9s).
# 家长可能一次性粘贴一周通知, 120s 留有余量.
# 内部还有 1 次 +2s 退避重试, 最坏总耗时 120s + 2s + 120s = 242s.
# 家长不会等 3 分钟, 但一周通知必须能跑通.
MINIMAX_TIMEOUT: int = int(os.environ.get("MINIMAX_TIMEOUT", "120"))


def ai_configured() -> bool:
    """API Key 是否配置. 仅返回布尔, 绝不返回 Key 本身."""
    return bool(MINIMAX_API_KEY)


# ====== 服务监听 ======
HOST: str = os.environ.get("HOST", "127.0.0.1").strip()
PORT: int = int(os.environ.get("PORT", "8765"))


# ====== 数据库 ======
_raw_db_path = os.environ.get("STUDY_DB_PATH", "./data/study.db")
if Path(_raw_db_path).is_absolute():
    DB_PATH: Path = Path(_raw_db_path).resolve()
else:
    # 相对路径相对项目根解析
    DB_PATH = (ROOT_DIR / _raw_db_path).resolve()


# ====== 业务默认 ======
DEFAULT_SUBJECT_BASELINE = {"语文": 20, "数学": 15, "英语": 15}
DEFAULT_TASK_TYPE_BASELINE = {
    "订正": 25,
    "背诵": 15,
    "预习": 20,
    "阅读": 20,
    "计算": 10,
    "抄写": 15,
}
DEFAULT_SCHOOL_DISMISS = "16:30"
DEFAULT_DINNER = "18:30"
DEFAULT_GRADE = "3"

SUBJECTS = ("语文", "数学", "英语")
STATUSES = ("todo", "doing", "done", "skipped")
CONFIDENCE_LEVELS = ("high", "medium", "low")
TASK_TYPES = ("订正", "背诵", "预习", "阅读", "计算", "抄写", "observation", "other")
