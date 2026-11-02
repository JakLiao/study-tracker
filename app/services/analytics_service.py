"""复盘偏差分析 (周 / 月)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .. import config
from ..db import get_conn
from ..errors import SampleTooSmallError
from .tenant import default_family_id

# 周一=0 ... 周日=6, 与 date.weekday() 对齐
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 统计周期的取值与中文名。前端按钮与后端参数用同一套词
# (current/last/month/last_month), 文案里的"本周/上周/本月/上月"也集中
# 在这里 —— 散落在各处各写一遍, 迟早会漏改。
PERIOD_LABELS = {
    "current": "本周", "last": "上周", "month": "本月", "last_month": "上月",
}


def _parse_month_param(month: str | None) -> date | None:
    """把 "YYYY-MM" 解析成那个月的 1 号。

    解析不了就返回 None —— 调用方 (_period_range) 收到 None 时按"缺省月份"
    处理 (上月周期缺省 = 上个月)。**不抛异常**: month 是可选的辅助参数,
    传坏了当没传, 而不是把整个复盘页打成 500。
    """
    s = (month or "").strip()
    if not s:
        return None
    try:
        y, m = s.split("-")[:2]
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        return None


def _month_start(d: date) -> date:
    """d 所在月的 1 号。"""
    return d.replace(day=1)


def _last_month_start(today: date) -> date:
    """上个月的 1 号。"""
    return _month_start(_month_start(today) - timedelta(days=1))


def _month_label(month_start: date, today: date) -> str:
    """月份的名字 (相对今天)。

    - 本月 / 上月 / 上上月 —— 就近三个月用相对说法, 家长不用先去数日历;
    - 更早的月份用 "8月" —— 再往前说"上上上月"就没法读了。

    月报文案与环比基准的名字都由这里给 (前端不自己判断), 免得两处
    各算一遍、改一处忘另一处。
    """
    if month_start == _month_start(today):
        return "本月"
    if month_start == _last_month_start(today):
        return "上月"
    if month_start == _month_start(_last_month_start(today) - timedelta(days=1)):
        return "上上月"
    return f"{month_start.month}月"


def _period_range(period: str, today: date, month: str | None = None) -> tuple[date, date]:
    """统计区间.

    - current / last: 自然周, 周一为一周开始, 固定 7 天。
    - month: 本月 1 日至**今日** (month-to-date)。
    - last_month: 指定月 (缺省 = 上个月) 的 1 日至该月最后一天 —— **整月**。

    为什么本月不到月底: 本月尚未到来的日子没有数据, 算进分母会把
    "平日日均"稀释成假的 —— 比如 10 月 5 日看本月, 4 个平日里有 3 个
    还没发生, 日均只剩真实值的三分之一。家长拿这个数去估"每天要写
    多久", 会被带偏。本周虽是整周 (含未到的日子), 但一周里未到的
    日子最多 3 天且多半落在周末档, 影响远小于整月。

    为什么上月是整月: 上月已经过完了, 它的每一天都是真实发生过的,
    没有"未到来"这回事。所以直接取整月, 不做 month-to-date 那套截断。
    (2026-11-03)
    """
    monday = today - timedelta(days=today.weekday())
    if period == "last":
        last_mon = monday - timedelta(days=7)
        return last_mon, last_mon + timedelta(days=6)
    if period == "month":
        return today.replace(day=1), today
    if period == "last_month":
        # 指定月份优先; 没指定就看上个月。
        start = _parse_month_param(month) or _last_month_start(today)
        return start, _last_day_of_month(start)
    # 默认 current
    return monday, monday + timedelta(days=6)


def _empty_result(period: str, today: date, sample_size: int = 0, note: str | None = None,
                  month: str | None = None):
    start, end = _period_range(period, today, month)
    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "sample_size": sample_size,
        "note": note,
        "by_type": [],
        "top_overestimate": [],
        "top_underestimate": [],
    }


def build_period_analytics(period: str, today: date, month: str | None = None) -> dict:
    """周期维度偏差: 预估 vs 实际, 按 科目 x 任务类型 聚合.

    业务规则:
    - 只统计 status=done 且 not is_optional 的记录
    - 实际用时缺省时按预估计入 (COALESCE), 不再因为没填就丢弃样本
    - sample_size < 5 -> 抛 SampleTooSmallError (前端展示"样本不足")
    - sample_size >= 5 但没有可执行任务的, 返回 note 提示

    month: last_month 周期下指定看哪个月 ("YYYY-MM"), 缺省看上个月。
    """
    start, end = _period_range(period, today, month)
    label = PERIOD_LABELS.get(period, "本周")

    # 未填实际用时的已完成项, 用 est_minutes 顶替。
    # 家长点"完成"时不必再被逼着填一次时长 —— 漏填的代价不该是
    # 这条彻底不参与统计, 否则样本量被系统性削小, 复盘长期显示"样本不足"。
    # 代价是这类样本的偏差恒为 0, 会把该科目的均值往 0 拉; 这是有意的,
    # "没填"表示"确实按预估做的", 而不是"数据缺失"。
    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT subject, task_type, est_minutes,
                   COALESCE(actual_minutes, est_minutes) AS effective_minutes
            FROM assignments
            WHERE family_id = %s
              AND due_date BETWEEN %s AND %s
              AND status = 'done'
              AND NOT is_optional
              AND (task_type IS NULL OR task_type != 'observation')
            """,
            (family_id, start, end),
        ).fetchall()

    sample_size = len(rows)
    if sample_size < 5:
        raise SampleTooSmallError(
            f"{label}完成数 {sample_size} < 5, 样本不足, 不生成偏差表"
        )

    if not rows:
        return _empty_result(period, today, sample_size=0,
                             note=f"{label}有完成但无实际耗时数据", month=month)

    # 按 (subject, task_type) 聚合
    agg: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for r in rows:
        subj = r["subject"] or "其他"
        ttype = r["task_type"] or "其他"
        agg[(subj, ttype)].append((int(r["est_minutes"]), int(r["effective_minutes"])))

    by_type: list[dict] = []
    for (subj, ttype), samples in agg.items():
        n = len(samples)
        avg_est = round(sum(e for e, _ in samples) / n, 1)
        avg_act = round(sum(a for _, a in samples) / n, 1)
        diff = round(avg_act - avg_est, 1)  # 正=低估, 负=高估
        diff_pct = round(diff / avg_est * 100, 1) if avg_est else 0.0
        by_type.append({
            "subject": subj,
            "task_type": ttype,
            "sample_size": n,
            "avg_est_minutes": avg_est,
            "avg_actual_minutes": avg_act,
            "diff_minutes": diff,
            "diff_pct": diff_pct,
            "advice": _make_advice(subj, ttype, diff, avg_est, avg_act),
        })

    # TOP 3 高估 (diff_minutes 最小, 实际远低于预估)
    over_sorted = sorted(by_type, key=lambda x: x["diff_minutes"])[:3]
    under_sorted = sorted(by_type, key=lambda x: -x["diff_minutes"])[:3]

    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "sample_size": sample_size,
        "by_type": by_type,
        "top_overestimate": [x for x in over_sorted if x["diff_minutes"] < 0],
        "top_underestimate": [x for x in under_sorted if x["diff_minutes"] > 0],
        "note": None,
    }


def _make_advice(subject: str, task_type: str, diff: float, avg_est: float, avg_act: float) -> str:
    """给家长一句话建议."""
    if abs(diff) < 3:
        return f"{subject}{task_type}估时基本准确 (估 {avg_est} 实 {avg_act})"
    if diff > 0:
        return f"{subject}{task_type}你平均估 {avg_est} 分钟, 实际 {avg_act} 分钟, 下次建议估 {round(avg_act)} 分钟"
    # diff < 0
    return f"{subject}{task_type}你平均估 {avg_est} 分钟, 实际 {avg_act} 分钟, 可下调到 {round(avg_act)} 分钟"


# ====== 每日各科作业用时 (2026-10-15) ======
#
# 这一块回答的是"孩子每个上学的日子, 各科分别要花多久", 与上面的偏差表
# 关注点不同: 偏差表问"我估得准不准", 这里问"真实负担有多大"。
#
# 为什么不能按自然日平均
# ------------------------
# 小学的作业量在一周内极不均衡, 而"上学日"这个概念本身不是"周一到周日":
#   - 周六周日通常不上学, 没作业 —— 算进去会把日均压低一大截
#   - 法定假期 (国庆/春节) 整周没作业 —— 同一问题, 而且更极端
#   - 但调休上班日 (周末补班) 要上学, **不能**当周末剔除
# 所以口径必须是"先查校历, 再决定这一天算不算", 而不是"按星期几猜"。
#
# 为什么周五要单列
# ----------------
# 学校惯例: 周五的作业里通常已经把周末两天的量一并布置了。把周五和
# 周一~周四 平均在一起, 算出来的"日均"是个不存在的数 —— 它既不代表
# 平日的真实负担, 也不代表周末。分开看才诚实: 周一~周四 是"平日档",
# 周五+周末 是"长档"。这一条来自用户明确要求 (2026-10-15)。
#
# 用时口径与看板 total 用时一致 (见 assignment_service.build_board_for):
#   只算 status='done' 的项 (跳过的没投入时间), 实耗为空时按预估计。
# 区别是这里**计入选做**: 这一栏问的是"今天总共花了多久", 选做做了
# 就是花了时间; 而偏差表排除选做是因为它在评估"估时准不准",
# 选做的估时质量不是家长要校准的东西。两个口径服务于两个问题, 不冲突。
#
# 排除的记录: task_type='observation' (观察作业, 不产生实际书写负担),
# 与 build_period_analytics 保持一致, 否则两处样本对不上。

# 平日档: 周一~周四。这四天作业结构最稳定, 适合看"每天固定要写多少"。
_PLAIN_WEEKDAYS = (0, 1, 2, 3)
# 长档: 周五 + 周末。周五通常把周末的量提前布置, 所以合看才有意义。
_FRIDAY = 4


def _calendar_holidays() -> dict[str, str]:
    """按日期查放假日。表按年组织, 跨年时在这里并表即可。"""
    out: dict[str, str] = {}
    for table in config.SCHOOL_HOLIDAYS_BY_YEAR.values():
        out.update(table)
    return out


def _calendar_makeup() -> dict[str, str]:
    """按日期查调休上班日 (周末补班, 要上学)。"""
    out: dict[str, str] = {}
    for table in config.SCHOOL_MAKEUP_CLASS_DAYS_BY_YEAR.values():
        out.update(table)
    return out


def classify_day(d: date, holidays: dict[str, str], makeup: dict[str, str]) -> tuple[str, str]:
    """判定某一天属于哪一档, 返回 (kind, label)。

    kind 取值:
      holiday  法定假期, 不上学 —— 整天从统计里剔除
      off      普通休息日 (周末且不补班), 不上学
      makeup   调休上班日 (落在周末但要上学) —— 按上学日算
      plain    平日档 (周一~周四)
      friday   周五 (含周末量, 单列)

    顺序有讲究: 调休上班日必须在"周末不上学"之前判定 —— 调休日本来
    就落在周末, 先判周末就永远命中"不上学", 调休规则等于没写。
    """
    ds = d.isoformat()
    if ds in holidays:
        return "holiday", holidays[ds]
    if ds in makeup:
        return "makeup", makeup[ds]
    if d.weekday() >= 5:
        return "off", "周末"
    if d.weekday() == _FRIDAY:
        return "friday", "周五"
    if d.weekday() in _PLAIN_WEEKDAYS:
        return "plain", WEEKDAY_NAMES[d.weekday()]
    # WEEKDAY_NAMES 覆盖 0~6, 走到这里说明表被改坏了。归为 off 而不是
    # 抛异常 —— 统计页不该因为校历数据问题整个打不开。
    return "off", "未知"


def build_daily_time_stats(period: str, today: date, month: str | None = None) -> dict:
    """每日各科作业用时 (按上学日口径).

    返回结构:
      days        区间内逐日明细 (含被剔除的假期, 让家长看得见"为什么少了一天");
                  周视图 7 天, 月视图是当月 1 日至今日
      school_days 计入统计的上学日 (plain + friday + makeup)
      plain_avg   周一~周四 日均 (各科 + 合计)
      friday      周五 单列
      weekend     周末单列
      makeup      调休上班日单列
      subject_share  各科时间占用 (预估 / 实际分开), 给占比饼图用

    month: last_month 周期下指定看哪个月 ("YYYY-MM"), 缺省看上个月。
    """
    start, end = _period_range(period, today, month)
    holidays = _calendar_holidays()
    makeup = _calendar_makeup()

    family_id = default_family_id()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT due_date, subject, est_minutes,
                   COALESCE(actual_minutes, est_minutes) AS minutes
            FROM assignments
            WHERE family_id = %s
              AND due_date BETWEEN %s AND %s
              AND status = 'done'
              AND (task_type IS NULL OR task_type != 'observation')
            """,
            (family_id, start, end),
        ).fetchall()

    # 先按 (日期, 科目) 累加 —— 一次查询, 不按天循环查库
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # 逐日的**预估**用时 (per_day 是实际/兜底预估)。前端要能按"只看得学日"
    # 重新算占比, 就必须拿到每天的预估值 —— 只有整段的 subject_share 是不够的。
    per_day_est: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # 各科时间占用 (给"占比饼图"用): 预估与实际分开累加。
    # 预估按 est_minutes; 实际按上面那个 minutes (即 COALESCE(actual, est)),
    # 与逐日用时同一口径 —— 这样饼图两个环的合计与柱状图的总用时对得上账。
    share_est: dict[str, int] = defaultdict(int)
    share_actual: dict[str, int] = defaultdict(int)
    for r in rows:
        subj = r["subject"] or "其他"
        due = r["due_date"]
        if not isinstance(due, str):
            due = due.isoformat()
        per_day[due][subj] += int(r["minutes"] or 0)
        per_day_est[due][subj] += int(r["est_minutes"] or 0)
        share_est[subj] += int(r["est_minutes"] or 0)
        share_actual[subj] += int(r["minutes"] or 0)

    # 科目顺序固定为 语文 -> 数学 -> 英语 (config.SUBJECTS 的顺序)。
    # 不这么做的话, dict 的键序来自 SQL 返回顺序, 也就是入库顺序 ——
    # 同一天里语文/数学/英语谁先被插入就排前面, 于是每根柱子的色块
    # 顺序都不一样 (周一黄在底, 周二蓝在底)。家长没法把"那个黄色"
    # 稳定地对应到语文, 堆叠图就失去了意义。
    # 只按 SUBJECTS 取, 不给表外科目兜底: subject 列有 CHECK 约束,
    # 只可能是这三者 (见 test_analytics 的对应断言), 兜底分支不可达。
    def _ordered(subjects: dict[str, int]) -> dict[str, int]:
        return {s: subjects[s] for s in config.SUBJECTS if s in subjects}

    days: list[dict] = []
    plain_days: list[dict] = []
    friday_days: list[dict] = []
    weekend_days: list[dict] = []
    makeup_days: list[dict] = []

    # 区间长度: 周视图 7 天, 月视图是当月 1 日至今日 (最多 31 天)。
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        kind, label = classify_day(d, holidays, makeup)
        by_subject = _ordered(dict(per_day.get(ds, {})))
        total = sum(by_subject.values())
        entry = {
            "date": ds,
            "weekday": d.weekday(),
            "weekday_label": WEEKDAY_NAMES[d.weekday()],
            "kind": kind,
            "kind_label": label,
            "is_school_day": kind in ("plain", "friday", "makeup"),
            "by_subject": by_subject,
            # 这一天各科的预估用时 (与 by_subject 分开存): 前端把周末
            # 藏掉时, 占比图要跟着一起藏, 两张图才能对得上账。
            "est_by_subject": _ordered(dict(per_day_est.get(ds, {}))),
            "total_minutes": total,
        }
        days.append(entry)
        # plain_avg 只用周一~周四。调休上班日虽然要上学, 但它落在周末,
        # 作息与作业结构都和平日不同, 混进"平日日均"会污染基线 ——
        # 所以它单列, 计入 school_days 但不进 plain_avg。
        if kind == "plain":
            plain_days.append(entry)
        elif kind == "friday":
            friday_days.append(entry)
        elif kind == "makeup":
            makeup_days.append(entry)
        elif kind == "off":
            weekend_days.append(entry)

    def _avg(entries: list[dict]) -> dict:
        """一组天的日均。分母用 len(entries) 而不是"有作业的天数" ——
        上了学却没写作业 (孩子提前做完了) 那天用时确实是 0, 算进平均才对。"""
        if not entries:
            return {"days": 0, "by_subject": {}, "total_minutes": 0}
        n = len(entries)
        subj_totals: dict[str, int] = defaultdict(int)
        for e in entries:
            for s, m in e["by_subject"].items():
                subj_totals[s] += m
        # 同样走 _ordered: 这里若用 sorted(), 会按 Unicode 码位排成
        # 数学 -> 语文 -> 英语, 与逐日的语数英顺序对不上, 图例也跟着乱。
        ordered = _ordered(dict(subj_totals))
        return {
            "days": n,
            "by_subject": {s: round(ordered[s] / n, 1) for s in ordered},
            "total_minutes": round(sum(ordered.values()) / n, 1),
        }

    def _bucket(entries: list[dict]) -> dict:
        """一档天数的合计。avg_minutes 是该档的日均 —— 月视图里各档天数
        相差悬殊 (平日二十来天 vs 周末八九天), 只看合计等于在比"哪档天多",
        必须给日均才有可比性。"""
        total = sum(e["total_minutes"] for e in entries)
        n = len(entries)
        return {
            "days": n,
            "total_minutes": total,
            "avg_minutes": round(total / n, 1) if n else 0,
        }

    school_days = plain_days + friday_days + makeup_days
    excluded = [e for e in days if not e["is_school_day"]]

    # 各科时间占用: 预估与实际各一份, 合计相同口径。
    # 注意这里统计的是**整个区间** (含周末与假期实际完成的作业), 不是
    # "上学日日均" —— 占比看的是一段时间的总盘子, 周末真写了作业那部分
    # 时间也是家长付出的, 不该从饼图里抹掉。
    est_by_subject = _ordered(dict(share_est))
    actual_by_subject = _ordered(dict(share_actual))

    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "days": days,
        "plain_avg": _avg(plain_days),
        "friday": _bucket(friday_days),
        "weekend": _bucket(weekend_days),
        "makeup": _bucket(makeup_days),
        "school_day_count": len(school_days),
        "excluded_days": [
            {"date": e["date"], "kind": e["kind"], "kind_label": e["kind_label"]}
            for e in excluded
        ],
        # 全部上学日的日均, 供只想看一个总数的人用
        "school_avg": _avg(school_days),
        "subject_share": {
            "est": {
                "by_subject": est_by_subject,
                "total_minutes": sum(est_by_subject.values()),
            },
            "actual": {
                "by_subject": actual_by_subject,
                "total_minutes": sum(actual_by_subject.values()),
            },
        },
    }


# ====== 月维度聚合 (2026-11-02) ======
#
# 为什么需要:
#   period=month 的 build_daily_time_stats 只把同一批图拉长到三十来天 ——
#   没有"这个月分成了几个周", 也没有"跟上月比怎么样"。家长真正想问的是
#   "这个月哪一周最重" 和 "这个月比上个月多花还是少花"。
#
# 口径与 build_daily_time_stats **完全一致** (只算 done、实耗为空按预估计、
# 排除 observation、只保留 SUBJECTS 里的学科), 这样
#   月报的合计 == 复盘页柱状图合计 == 环图合计
# 三处对得上账。培训/习惯任务没有科目, 与 build_daily_time_stats 一样
# 不进这两张图 —— 训练线由周报的 training 块单独讲。

def _last_day_of_month(d: date) -> date:
    """d 所在月的最后一天。"""
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


def _ordered_subjects(subjects: dict[str, int]) -> dict[str, int]:
    """固定 语文→数学→英语 顺序。

    与 build_daily_time_stats 里的 _ordered 同一套规则 —— 那份是函数内
    定义的局部闭包, 这里要单独有一份模块级的 (顺序错一次, 家长就再也
    对不上"那个蓝色是数学")。
    """
    return {s: subjects[s] for s in config.SUBJECTS if s in subjects}


def _add_months(d: date, n: int) -> date:
    """把日期挪 n 个月; day 夹到目标月的最后一天 (1/31 减一个月 → 2/28)。"""
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, _last_day_of_month(date(year, month, 1)).day)
    return date(year, month, day)


def _month_range_rows(conn, family_id: int, start: date, end: date) -> list:
    """区间内的作业行。月聚合一次查完, 不按天循环查库。"""
    return conn.execute(
        """
        SELECT due_date, subject, status, est_minutes, actual_minutes,
               is_optional, task_type
        FROM assignments
        WHERE family_id = %s AND due_date BETWEEN %s AND %s
        ORDER BY due_date, id
        """,
        (family_id, start, end),
    ).fetchall()


def _month_time_rows(rows: list) -> tuple[dict, dict]:
    """从作业行里拆出 (逐日各科用时, 各科合计用时)。

    只保留 SUBJECTS 里的学科 —— 与 build_daily_time_stats 的 _ordered
    同规则 (它把 subject 为空的训练/习惯任务归到"其他"后又整个丢掉,
    这里直接不收, 结果一致)。
    """
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_subject: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["status"] != "done":
            continue
        if r["task_type"] == "observation":
            continue
        subj = r["subject"]
        if subj not in config.SUBJECTS:
            continue
        minutes = int(r["actual_minutes"] if r["actual_minutes"] is not None
                      else r["est_minutes"]) or 0
        due = r["due_date"]
        if not isinstance(due, str):
            due = due.isoformat()
        per_day[due][subj] += minutes
        by_subject[subj] += minutes
    return per_day, by_subject


def _month_completion(rows: list) -> dict:
    """完成情况。与周报同口径: 分母用必做 (不含选做), done+skipped 算收尾。"""
    total = len(rows)
    optional = sum(1 for r in rows if r["is_optional"])
    required = total - optional
    settled = sum(1 for r in rows
                  if not r["is_optional"] and r["status"] in ("done", "skipped"))
    if required > 0:
        rate = min(100, round(settled * 100 / required))
    else:
        rate = 100 if rows else 0
    return {
        "total": total,
        "optional": optional,
        "required": required,
        "settled": settled,
        "rate": rate,
    }


def build_month_analytics(anchor: date, today: date) -> dict:
    """anchor 所在月份的月度聚合 (月内按周切分 + 环比上月同期)。

    anchor 取该月任意一天; month_start = anchor 所在月 1 日, month_end = 该月
    最后一天。

    数据区间是 **本月 1 日至今天** (month-to-date), 不是整月: 本月尚未到来
    的日子没有数据, 算进分母会把"日均"稀释成假的。与 _period_range('month')
    同一理由。

    环比上月用 **同期对齐**: 上个月同期 (上月 1 日 ~ 上月同一天), 不是上月
    整月。本月才过了 2 天却拿上月整月比, 会得出"少用了 95%"这种假结论。

    但**过去整月** (上月 / 更早的月份) 不是这个口径: 那些日子都已过完,
    拿整月跟"上个月的同期"比毫无意义 —— 上上月只有 30 天, "同期"会被截断
    成 30 天, 那其实是整月比。所以过去的整月一律按**整月**比, 并在
    payload 里标明基准 (prev_month_basis), 让文案说对 (2026-11-03)。
    """
    month_start = anchor.replace(day=1)
    month_end = _last_day_of_month(month_start)
    # 过去的月份 (month_end < today) 整月都是数据; 当月只到今天。
    data_end = min(today, month_end)
    elapsed_days = (data_end - month_start).days + 1 if data_end >= month_start else 0
    is_partial = today < month_end

    holidays = _calendar_holidays()
    makeup = _calendar_makeup()
    family_id = default_family_id()

    with get_conn() as conn:
        rows = _month_range_rows(conn, family_id, month_start, data_end)
        # 这个家庭最早的一条作业记录在哪天。
        #
        # 用途: 判断环比基准月是不是**只记了后半段**。家里是 9 月 21 日
        # 开始用的这个应用, 于是 9 月只有 9/21 之后的数据 —— 拿 10 月整月
        # 去比 9 月, 会算出"+329%"这种假涨幅 (9 月少了整整 20 天)。
        # 不是孩子变勤快了, 是 9 月前 20 天压根没记。
        # 所以基准月缺前半段时要**说出来**, 不能让家长照着假涨幅下结论。
        first_row = conn.execute(
            "SELECT MIN(due_date) AS d FROM assignments WHERE family_id = %s",
            (family_id,),
        ).fetchone()
        first_date = first_row["d"] if first_row else None
        if isinstance(first_date, str):
            first_date = date.fromisoformat(first_date)
        prev_start = _add_months(month_start, -1)
        if is_partial:
            # 本月 (还没过完): **同期对齐** —— 上月 1 日 ~ 上月同一天。
            # 本月才过 2 天却拿上月整月比, 会得出"少用了 95%"这种假结论。
            prev_end = min(
                prev_start + timedelta(days=max(elapsed_days - 1, 0)),
                _last_day_of_month(prev_start),
            )
        else:
            # 过去的整月: 跟**上一个整月**比, 基准月取全月, 不截断。
            #
            # 不能沿用上面的同期算法: 9 月 30 天比 8 月时, "同期"会算成
            # 8 月 1~30 日, 少算 8 月 31 日那一天 —— 而那是个完整月份的
            # 基准, 少一天就是拿 30 天比 31 天, 日均与百分比都会偏。
            prev_end = _last_day_of_month(prev_start)
        prev_rows = (_month_range_rows(conn, family_id, prev_start, prev_end)
                     if elapsed_days > 0 else [])

    per_day, by_subject = _month_time_rows(rows)
    total_minutes = sum(by_subject.values())
    ordered_subject = _ordered_subjects(dict(by_subject))

    # 上学日: 与 build_daily_time_stats 同一套日档判定。
    school_days = sum(
        1 for i in range(elapsed_days)
        if classify_day(month_start + timedelta(days=i), holidays, makeup)[0]
        in ("plain", "friday", "makeup")
    )
    denom = school_days or elapsed_days or 1

    # 各科占比与日均。
    subjects = [
        {
            "subject": s,
            "minutes": m,
            "pct": round(m * 100 / total_minutes, 1) if total_minutes else 0,
            "avg_per_day": round(m / denom, 1),
        }
        for s, m in ordered_subject.items()
    ]

    # 月内按自然周切分。落在月外的部分不计入 (只算本月内的日子)。
    #
    # 逐日的完成数先一次性算好, 再按周汇总 —— 不要在按天的循环里扫全部
    # 作业行, 那是 O(天数 × 行数), 月末能跑到几千次比较。
    per_day_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"done": 0, "required": 0, "settled": 0})
    for r in rows:
        due = r["due_date"]
        if not isinstance(due, str):
            due = due.isoformat()
        c = per_day_counts[due]
        if r["status"] == "done":
            c["done"] += 1
        if not r["is_optional"]:
            c["required"] += 1
            if r["status"] in ("done", "skipped"):
                c["settled"] += 1

    weeks: list[dict] = []
    for i in range(elapsed_days):
        d = month_start + timedelta(days=i)
        monday = d - timedelta(days=d.weekday())
        if not weeks or weeks[-1]["_monday"] != monday:
            weeks.append({
                "_monday": monday,
                "label": f"第{len(weeks) + 1}周",
                "start": d.isoformat(),
                "end": d.isoformat(),
                "days": 0,
                "minutes": 0,
                "done": 0,
                "required": 0,
                "settled": 0,
            })
        w = weeks[-1]
        w["end"] = d.isoformat()
        w["days"] += 1
        ds = d.isoformat()
        w["minutes"] += sum(per_day.get(ds, {}).values())
        c = per_day_counts.get(ds)
        if c:
            w["done"] += c["done"]
            w["required"] += c["required"]
            w["settled"] += c["settled"]
    for w in weeks:
        w.pop("_monday", None)
        w["avg_minutes"] = round(w["minutes"] / w["days"], 1) if w["days"] else 0
        w["rate"] = (min(100, round(w["settled"] * 100 / w["required"]))
                     if w["required"] else (100 if w["days"] else 0))

    # 环比上月同期。
    _, prev_by_subject = _month_time_rows(prev_rows)
    prev_total = sum(prev_by_subject.values())
    prev_school_days = sum(
        1 for i in range((prev_end - prev_start).days + 1)
        if classify_day(prev_start + timedelta(days=i), holidays, makeup)[0]
        in ("plain", "friday", "makeup")
    )
    prev_completion = _month_completion(prev_rows)
    prev_denom = prev_school_days or ((prev_end - prev_start).days + 1) or 1
    prev_avg = round(prev_total / prev_denom, 1)
    cur_avg = round(total_minutes / denom, 1)

    completion = _month_completion(rows)

    # 文案依据。**前端不猜"这是本月还是上月"** —— 名字与基准都从这里给,
    # 否则"上月"周期里写出一句"与上月同期相比"就正好说错了 (上月比的是
    # 上上月整月, 不是上月同期)。
    month_label = _month_label(month_start, today)
    prev_month_label = _month_label(prev_start, today)
    prev_month_basis = "same_period" if is_partial else "full_month"

    # 基准月是不是只记了后半段?
    #
    # 家里若是 9 月 21 日才开始用这个应用, 9 月就只剩 9/21 之后的数据 ——
    # 拿 10 月整月去比这样的 9 月, 会算出"+329%"这种假涨幅 (9 月少了
    # 整整 20 天)。不是孩子变勤快了, 是那 20 天压根没记。
    # 所以基准缺前半段时要**说出来**, 不能让家长照着假涨幅下结论。
    prev_month_partial = bool(first_date and prev_start < first_date)

    return {
        "month": f"{month_start.year:04d}-{month_start.month:02d}",
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "data_end": data_end.isoformat(),
        "elapsed_days": elapsed_days,
        "is_partial": is_partial,
        "school_days": school_days,
        # 所看月份的名字 (相对今天): 本月 / 上月 / 上上月 / 8月
        "month_label": month_label,
        # 环比基准的名字 (相对今天): 上月 / 上上月 / 7月
        "prev_month_label": prev_month_label,
        "completion": completion,
        "time": {
            "total_minutes": total_minutes,
            "avg_daily_minutes": cur_avg,
            "school_days": school_days,
            "by_subject": ordered_subject,
        },
        "subjects": subjects,
        "weeks": weeks,
        "prev_month": {
            "month": f"{prev_start.year:04d}-{prev_start.month:02d}",
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "days": (prev_end - prev_start).days + 1,
            "total_minutes": prev_total,
            "avg_daily_minutes": prev_avg,
            "school_days": prev_school_days,
            "done": prev_completion["settled"],
            "rate": prev_completion["rate"],
        },
        # 环比基准: same_period = 上月同期 (本月没过完时的对齐口径);
        # full_month = 上一个整月 (过去的整月比整月)。
        # 前端文案必须照着说 —— 写错基准家长会对不上账。
        "prev_month_basis": prev_month_basis,
        # 基准月是不是只记了后半段 (家里从这个月才开始用应用)。
        # 为真时环比数字会偏大, 前端要补一句说明, 别让家长误判。
        "prev_month_partial": prev_month_partial,
        # 基准月里有没有数据。压根没记录 (比开始用应用还早的月份) 与
        # "只记了后半段" 是两回事, 文案不能混着说。
        "prev_month_has_data": prev_total > 0,
        # 这个家庭最早的一条作业记录在哪天 (给上面那句说明用)。
        "first_record_date": first_date.isoformat() if first_date else None,
        "delta": {
            "minutes": total_minutes - prev_total,
            "minutes_pct": (round((total_minutes - prev_total) * 100 / prev_total, 1)
                            if prev_total else None),
            "rate": completion["rate"] - prev_completion["rate"],
            "avg_daily_minutes": round(cur_avg - prev_avg, 1),
        },
    }