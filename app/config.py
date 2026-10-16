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
# 默认 8876: 8765 已被 serve_gallery.py 占用, 回落会 address already in use.
# 与 run.sh / service.sh / .env.example 保持一致, 勿单独改.
PORT: int = int(os.environ.get("PORT", "8876"))


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

# task_type 关键词兜底表 (功能设计 §7.2 规划项, 此前未实现).
# AI 没给 task_type (或给 other) 时, 按这个顺序在 task_name / source_span 里找第一个命中词.
# 顺序有讲究: 具体动作词排前面, "阅读" 这种泛词排最后 ——
# 否则 "抄写阅读心得" 会被判成 阅读, 而家长实际要记的是 抄写.
TASK_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("订正", "订正"),
    ("背诵", "背诵"),
    ("预习", "预习"),
    ("计算", "计算"),
    ("抄写", "抄写"),
    ("阅读", "阅读"),
)


# ====== 校历 ======
# 复盘页统计"每个上学的日子各科用多少分钟"时, 必须先知道哪天上学。
# 否则国庆那一周会拉出"日均 20 分钟"的结论 —— 实际只是七天没上学,
# 拿没有作业的日子去平均, 等于用假期稀释真实负担, 家长会以为孩子变轻松了。
#
# 每年国务院都会重排, 所以这份表是"按年"的常量而不是算法 ——
# 任何"按春节推算节假日"的规则都会在遇到调休时算错 (春节调休会
# 把假期拆到前后两周), 宁可每年显式维护。
#
# 两条规则各自的作用:
#   SCHOOL_HOLIDAYS: 放假日 (含落在工作日的部分)。不上学, 整天剔除。
#   SCHOOL_MAKEUP_CLASS_DAYS: 调休上班日, 全部落在周末。这天要上学,
#     所以**不能**按"周末不上学"剔除。
#
# 2026 年数据来源: 《国务院办公厅关于2026年部分节假日安排的通知》
#   国办发明电〔2025〕7号, 2025-11-04 发布。
#   https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm
SCHOOL_HOLIDAYS_BY_YEAR: dict[int, dict[str, str]] = {
    2026: {
        # 元旦 1/1(周四)~1/3(周六)
        "2026-01-01": "元旦", "2026-01-02": "元旦", "2026-01-03": "元旦",
        # 春节 2/15(周日)~2/23(周一)
        "2026-02-15": "春节", "2026-02-16": "春节", "2026-02-17": "春节",
        "2026-02-18": "春节", "2026-02-19": "春节", "2026-02-20": "春节",
        "2026-02-21": "春节", "2026-02-22": "春节", "2026-02-23": "春节",
        # 清明 4/4(周六)~4/6(周一)
        "2026-04-04": "清明", "2026-04-05": "清明", "2026-04-06": "清明",
        # 劳动节 5/1(周五)~5/5(周二)
        "2026-05-01": "劳动节", "2026-05-02": "劳动节", "2026-05-03": "劳动节",
        "2026-05-04": "劳动节", "2026-05-05": "劳动节",
        # 端午 6/19(周五)~6/21(周日)
        "2026-06-19": "端午", "2026-06-20": "端午", "2026-06-21": "端午",
        # 中秋 9/25(周五)~9/27(周日)
        "2026-09-25": "中秋", "2026-09-26": "中秋", "2026-09-27": "中秋",
        # 国庆 10/1(周四)~10/7(周三)
        "2026-10-01": "国庆", "2026-10-02": "国庆", "2026-10-03": "国庆",
        "2026-10-04": "国庆", "2026-10-05": "国庆", "2026-10-06": "国庆",
        "2026-10-07": "国庆",
    },
}

# 调休上班日 (通知里逐条列明的"X月X日上班")。2026 年 6 天。
SCHOOL_MAKEUP_CLASS_DAYS_BY_YEAR: dict[int, dict[str, str]] = {
    2026: {
        "2026-01-04": "元旦调休",     # 周日
        "2026-02-14": "春节调休",     # 周六
        "2026-02-28": "春节调休",     # 周六
        "2026-05-09": "劳动节调休",   # 周六
        "2026-09-20": "国庆调休",     # 周日
        "2026-10-10": "国庆调休",     # 周六
    },
}
