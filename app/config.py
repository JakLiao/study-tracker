"""全局配置 (从环境变量读, 启动期一次性加载)."""
from __future__ import annotations

import os
from pathlib import Path

from .env_loader import database_url_with_name, load_dotenv_if_exists


# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent


load_dotenv_if_exists(ROOT_DIR)


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


# ====== 数据库 (PostgreSQL) ======
# 连接串只从环境变量 / .env 读, 代码里不再有兜底默认值 ——
# 兜底里带真实密码, 等于把密码抄进了版本库, 换密码时还会漏改一处。
# 没配就报错: 让"忘配"当场暴露, 好过静默连到某个错库上。
DATABASE_URL: str = os.environ.get("DATABASE_URL", "").strip()

# 测试库: 与业务库同实例、不同库名, 避免测试清表影响真实家庭数据。
# 连接参数(主机/端口/用户/密码)沿用 DATABASE_URL, 只换库名 —— 密码因此
# 只有 .env 一个出处。
TEST_DATABASE_URL: str = os.environ.get(
    "TEST_DATABASE_URL",
    database_url_with_name(DATABASE_URL, "study_tracker_test"),
).strip()

# 历史 SQLite 文件: 只用于一次性数据导入与归档, 运行时不再读写
_raw_db_path = os.environ.get("STUDY_DB_PATH", "./data/study.db")
if Path(_raw_db_path).is_absolute():
    DB_PATH: Path = Path(_raw_db_path).resolve()
else:
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
# M5 每日负荷上限 (分钟): 课表 + 未完成任务预估超过它就在周计划页标"超载"。
# 不写进 settings 默认种子: 该值目前只在周计划页读取, 家长想调再手动
# 往 settings 里加一条 daily_load_limit 即可, 不必为它加一行设置界面。
DEFAULT_DAILY_LOAD_MINUTES = 120

SUBJECTS = ("语文", "数学", "英语")
STATUSES = ("todo", "doing", "done", "skipped")
CONFIDENCE_LEVELS = ("high", "medium", "low")
TASK_TYPES = ("订正", "背诵", "预习", "阅读", "计算", "抄写", "observation", "other")

# ====== 统一任务池 (M1) ======
# 学科作业 / 培训打卡 / 习惯, 同一个 assignments 表 + 同一个看板。
# subject 只有 task_kind=subject 时必填; 培训/习惯用 project_id 归类。
TASK_KINDS = ("subject", "training", "habit")
TASK_KIND_LABELS = {"subject": "学科", "training": "培训", "habit": "习惯"}

# 项目模板: 建项目 + 一键生成默认任务用的预设 (不是固定枚举, 家长可自建 custom 项目)。
# 每个模板的 tasks 就是"低摩擦录入"的默认清单 —— 点一下直接入库, 不用再手打。
PROJECT_TEMPLATES: tuple[dict, ...] = (
    {
        "key": "piano",
        "label": "钢琴",
        "task_kind": "training",
        "project_type": "piano",
        "default_name": "钢琴",
        "tasks": (
            {"task_name": "基本功（音阶/哈农）", "est_minutes": 10, "task_type": "练习"},
            {"task_name": "曲目练习", "est_minutes": 20, "task_type": "练习"},
        ),
    },
    {
        "key": "basketball",
        "label": "篮球",
        "task_kind": "training",
        "project_type": "basketball",
        "default_name": "篮球",
        "tasks": (
            {"task_name": "运球练习", "est_minutes": 15, "task_type": "练习"},
            {"task_name": "投篮练习", "est_minutes": 15, "task_type": "练习"},
        ),
    },
    {
        "key": "badminton",
        "label": "羽毛球",
        "task_kind": "training",
        "project_type": "badminton",
        "default_name": "羽毛球",
        "tasks": (
            {"task_name": "挥拍练习", "est_minutes": 10, "task_type": "练习"},
            {"task_name": "步法练习", "est_minutes": 10, "task_type": "练习"},
        ),
    },
    {
        "key": "reading",
        "label": "阅读",
        "task_kind": "habit",
        "project_type": "reading",
        "default_name": "每日阅读",
        "tasks": (
            {"task_name": "课外阅读", "est_minutes": 20, "task_type": "阅读"},
        ),
    },
    {
        "key": "habit",
        "label": "每日习惯",
        "task_kind": "habit",
        "project_type": "habit",
        "default_name": "每日习惯",
        "tasks": (
            {"task_name": "整理书包", "est_minutes": 5, "task_type": "other"},
            {"task_name": "练字", "est_minutes": 10, "task_type": "抄写"},
            {"task_name": "跳绳", "est_minutes": 10, "task_type": "other"},
        ),
    },
)

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
