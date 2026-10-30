"""复盘偏差分析 (周 / 月)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .. import config
from ..db import get_conn
from ..errors import SampleTooSmallError

# 周一=0 ... 周日=6, 与 date.weekday() 对齐
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 统计周期的取值与中文名。前端按钮与后端参数用同一套词 (current/last/month),
# 文案里的"本周/上周/本月"也集中在这里 —— 散落在各处各写一遍, 迟早会漏改。
PERIOD_LABELS = {"current": "本周", "last": "上周", "month": "本月"}


def _period_range(period: str, today: date) -> tuple[date, date]:
    """统计区间.

    - current / last: 自然周, 周一为一周开始, 固定 7 天。
    - month: 本月 1 日至**今日** (month-to-date)。

    为什么本月不到月底: 本月尚未到来的日子没有数据, 算进分母会把
    "平日日均"稀释成假的 —— 比如 10 月 5 日看本月, 4 个平日里有 3 个
    还没发生, 日均只剩真实值的三分之一。家长拿这个数去估"每天要写
    多久", 会被带偏。本周虽是整周 (含未到的日子), 但一周里未到的
    日子最多 3 天且多半落在周末档, 影响远小于整月。
    """
    monday = today - timedelta(days=today.weekday())
    if period == "last":
        last_mon = monday - timedelta(days=7)
        return last_mon, last_mon + timedelta(days=6)
    if period == "month":
        return today.replace(day=1), today
    # 默认 current
    return monday, monday + timedelta(days=6)


def _empty_result(period: str, today: date, sample_size: int = 0, note: str | None = None):
    start, end = _period_range(period, today)
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


def build_period_analytics(period: str, today: date) -> dict:
    """周期维度偏差: 预估 vs 实际, 按 科目 x 任务类型 聚合.

    业务规则:
    - 只统计 status=done 且 not is_optional 的记录
    - 实际用时缺省时按预估计入 (COALESCE), 不再因为没填就丢弃样本
    - sample_size < 5 -> 抛 SampleTooSmallError (前端展示"样本不足")
    - sample_size >= 5 但没有可执行任务的, 返回 note 提示
    """
    start, end = _period_range(period, today)
    label = PERIOD_LABELS.get(period, "本周")

    # 未填实际用时的已完成项, 用 est_minutes 顶替。
    # 家长点"完成"时不必再被逼着填一次时长 —— 漏填的代价不该是
    # 这条彻底不参与统计, 否则样本量被系统性削小, 复盘长期显示"样本不足"。
    # 代价是这类样本的偏差恒为 0, 会把该科目的均值往 0 拉; 这是有意的,
    # "没填"表示"确实按预估做的", 而不是"数据缺失"。
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT subject, task_type, est_minutes,
                   COALESCE(actual_minutes, est_minutes) AS effective_minutes
            FROM assignments
            WHERE due_date BETWEEN ? AND ?
              AND status = 'done'
              AND is_optional = 0
              AND (task_type IS NULL OR task_type != 'observation')
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    sample_size = len(rows)
    if sample_size < 5:
        raise SampleTooSmallError(
            f"{label}完成数 {sample_size} < 5, 样本不足, 不生成偏差表"
        )

    if not rows:
        return _empty_result(period, today, sample_size=0,
                             note=f"{label}有完成但无实际耗时数据")

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


def build_daily_time_stats(period: str, today: date) -> dict:
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
    """
    start, end = _period_range(period, today)
    holidays = _calendar_holidays()
    makeup = _calendar_makeup()

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT due_date, subject, est_minutes,
                   COALESCE(actual_minutes, est_minutes) AS minutes
            FROM assignments
            WHERE due_date BETWEEN ? AND ?
              AND status = 'done'
              AND (task_type IS NULL OR task_type != 'observation')
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    # 先按 (日期, 科目) 累加 —— 一次查询, 不按天循环查库
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # 各科时间占用 (给"占比饼图"用): 预估与实际分开累加。
    # 预估按 est_minutes; 实际按上面那个 minutes (即 COALESCE(actual, est)),
    # 与逐日用时同一口径 —— 这样饼图两个环的合计与柱状图的总用时对得上账。
    share_est: dict[str, int] = defaultdict(int)
    share_actual: dict[str, int] = defaultdict(int)
    for r in rows:
        subj = r["subject"] or "其他"
        per_day[r["due_date"]][subj] += int(r["minutes"] or 0)
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