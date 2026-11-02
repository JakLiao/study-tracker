"""本月课后月报 (2026-11-02) 接口与聚合口径测试.

要守住的四件事:
  1. **环比是"同期对齐"** —— 本月才过了 2 天却拿上月整月比, 会得出
     "少用了 95%" 这种把"月还没过完"当成"孩子变懒了"的假结论;
  2. **合计对得上账** —— 月报的合计必须等于复盘页柱状图/环图的合计
     (同一个口径, 否则家长对着两张图能看出差);
  3. **月内按周切分** —— 周次必须按自然周切, 且覆盖当月全部已过的日子;
  4. **参数非法要报错** —— 4006, 不能静默当成当月。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest


def _seed(subject, est, actual, due_date, status="done", is_optional=False, task_type=None):
    """直接 SQL 插一条作业 (绕过 PATCH)。actual 传 None 即"未填实际用时"。"""
    import psycopg
    from psycopg.rows import dict_row

    from app import config
    from app.services.tenant import default_child_id, default_family_id

    now = datetime.utcnow()
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        conn.execute(
            """INSERT INTO assignments
               (subject, task_name, due_date, deadline_time, is_optional,
                est_minutes, actual_minutes, status, task_type, source_span,
                confidence, batch_id, created_at, updated_at,
                family_id, child_id, project_id, task_kind)
               VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, %s, '', 'high', NULL, %s, %s, %s, %s, NULL, 'subject')""",
            (subject, f"test {due_date}", date.fromisoformat(due_date),
             bool(is_optional), est, actual, status,
             task_type or "背诵", now, now,
             default_family_id(), default_child_id()),
        )


# ====== 1. 参数校验 ======

def test_monthly_report_rejects_bad_month(app_client):
    r = app_client.get("/api/monthly-report?month=abc")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4006"


def test_monthly_report_rejects_bad_month_shape(app_client):
    """2026-13 是坏月份, 不能当成当月糊弄过去。"""
    r = app_client.get("/api/monthly-report?month=2026-13")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4006"


def test_monthly_report_defaults_to_current_month(app_client):
    """不传 month 就看当月。"""
    r = app_client.get("/api/monthly-report")
    assert r.status_code == 200
    body = r.json()
    today = date.today()
    assert body["month"] == f"{today.year:04d}-{today.month:02d}"
    assert body["month_start"] == f"{today.year:04d}-{today.month:02d}-01"


def test_monthly_report_accepts_both_month_forms(app_client):
    """'2026-10' 与 '2026-10-15' 都表示"看 10 月"。"""
    a = app_client.get("/api/monthly-report?month=2026-10").json()
    b = app_client.get("/api/monthly-report?month=2026-10-15").json()
    assert a["month"] == b["month"] == "2026-10"
    assert a["month_start"] == b["month_start"] == "2026-10-01"


# ====== 2. 聚合口径 ======

def test_month_aggregates_total_and_subject_split(app_client):
    _seed("语文", 20, 30, "2026-10-05")
    _seed("数学", 10, 12, "2026-10-05")
    _seed("英语", 15, 15, "2026-10-06")
    r = app_client.get("/api/monthly-report?month=2026-10").json()
    assert r["time"]["total_minutes"] == 57
    assert r["time"]["by_subject"] == {"语文": 30, "数学": 12, "英语": 15}
    # 各科占比之和必须等于合计 (对得上账)
    assert sum(s["minutes"] for s in r["subjects"]) == r["time"]["total_minutes"]
    assert r["subjects"][0]["subject"] == "语文", "科目顺序必须固定 语→数→英"


def test_month_ignores_non_done_and_observation(app_client):
    """口径与复盘页一致: 只算 done; 观察作业不计。"""
    _seed("语文", 20, 30, "2026-10-05", status="todo")
    _seed("语文", 20, 30, "2026-10-05", status="skipped")
    _seed("数学", 10, 99, "2026-10-06", task_type="observation")
    r = app_client.get("/api/monthly-report?month=2026-10").json()
    assert r["time"]["total_minutes"] == 0


def test_month_uses_est_when_actual_missing(app_client):
    """没填实际用时按预估计入 —— 漏填不该让样本消失。"""
    _seed("语文", 25, None, "2026-10-05")
    r = app_client.get("/api/monthly-report?month=2026-10").json()
    assert r["time"]["total_minutes"] == 25


def test_month_weekly_split_covers_all_elapsed_days(app_client):
    """10 月整月: 周次必须按自然周切, 且天数加起来等于整月天数。"""
    r = app_client.get("/api/monthly-report?month=2026-10").json()
    weeks = r["weeks"]
    assert weeks, "必须有周次"
    assert sum(w["days"] for w in weeks) == 31
    assert weeks[0]["label"] == "第1周"
    # 周次按自然周 (周一~周日) 切: 10/1 是周四, 属于第 1 周 (10/1~10/4)
    assert weeks[0]["start"] == "2026-10-01"
    assert weeks[0]["end"] == "2026-10-04"
    assert weeks[1]["start"] == "2026-10-05"


def test_month_partial_marks_is_partial(app_client):
    """本月没过完 -> is_partial 必须为真, 并带上已过天数。"""
    r = app_client.get("/api/monthly-report").json()
    assert r["is_partial"] is True
    assert r["elapsed_days"] >= 1
    assert r["elapsed_days"] <= 31


# ====== 3. 环比: 必须同期对齐 ======

def test_prev_month_comparison_is_same_period(app_client):
    """本月过了 2 天 -> 环比基准是上月**同期** (1~2 日), 不是上月整月。

    这条是本次最要紧的一条: 拿上月整月比, 会得出"少用了 95%"这种假结论,
    家长会以为孩子这个月突然变懈怠了。
    """
    # 上月同期 (10/1~10/2) 有 100 分钟
    _seed("语文", 50, 50, "2026-10-01")
    _seed("数学", 50, 50, "2026-10-02")
    # 本月同期 (11/1~11/2) 有 20 分钟
    _seed("语文", 10, 10, "2026-11-01")
    _seed("语文", 10, 10, "2026-11-02")
    # 上月**后面**的日子有作业 —— 若环比拿整月比, 就会被算进来
    _seed("语文", 100, 100, "2026-10-20")

    from app.services import analytics_service
    today = date(2026, 11, 2)
    a = analytics_service.build_month_analytics(date(2026, 11, 2), today)
    assert a["prev_month_basis"] == "same_period"
    assert a["prev_month"]["start"] == "2026-10-01"
    assert a["prev_month"]["end"] == "2026-10-02", "环比基准必须是上月同期, 不是整月"
    assert a["prev_month"]["total_minutes"] == 100, "上月 10/20 那 100 分钟不该进环比基准"
    assert a["delta"]["minutes"] == 20 - 100


def test_prev_month_comparison_caps_at_prev_month_length(app_client):
    """上月天数比本月少时, 同期区间不能越过上个月底。"""
    from app.services import analytics_service
    # 2027-03-01 往前一个月是 2027-02 (28 天); 让"今天"是 3/31, elapsed=31
    a = analytics_service.build_month_analytics(date(2027, 3, 31), date(2027, 3, 31))
    assert a["prev_month"]["month"] == "2027-02"
    assert a["prev_month"]["end"] == "2027-02-28"
    assert a["prev_month"]["days"] == 28


# ====== 4. 文案 ======

def test_month_highlights_say_same_period(app_client):
    """环比的文案必须写明"与上月同期比", 家长才知道基准是什么。"""
    # 先造出"有环比"的前提: 本月与上月同期都有用时
    _seed("语文", 60, 60, "2026-10-01")
    _seed("语文", 30, 30, "2026-11-01")
    from app.services import report_service
    r = report_service.build_monthly_report(date(2026, 11, 2), date(2026, 11, 2))
    joined = " ".join(r["highlights"])
    assert any("上月同期" in h for h in r["highlights"]), joined


def test_month_suggestions_are_bounded(app_client):
    """建议最多 3 条 —— 超过就变成任务清单, 不是建议。"""
    from app.services import report_service
    r = report_service.build_monthly_report(date(2026, 11, 2), date(2026, 11, 2))
    assert len(r["suggestions"]) <= 3
    assert len(r["highlights"]) <= 3


def test_month_report_no_data_does_not_crash(app_client):
    """空月份也要返回一份完整结构, 不能 500。"""
    r = app_client.get("/api/monthly-report?month=2026-01")
    assert r.status_code == 200
    body = r.json()
    assert body["time"]["total_minutes"] == 0
    assert body["weeks"]
    assert body["highlights"] is not None


# ====== 5. 与复盘页对得上账 ======

def test_month_totals_match_review_time_endpoint(app_client):
    """月报合计 == 复盘页柱状图/环图的合计。

    两处口径必须一致, 否则家长在同一页上看到两个不同的"本月总用时"。
    """
    _seed("语文", 20, 30, "2026-10-05")
    _seed("数学", 10, 12, "2026-10-05")
    _seed("英语", 15, 15, "2026-10-06")

    from app.services import analytics_service
    month = analytics_service.build_month_analytics(date(2026, 10, 15), date(2026, 11, 30))
    # 复盘页的月视图: period=month 用的是"本月 1 日至今日",
    # 这里用等价的区间直接跑 build_daily_time_stats 的底层口径来比。
    daily = analytics_service.build_daily_time_stats("month", date(2026, 10, 15))
    assert daily["subject_share"]["actual"]["total_minutes"] == \
        month["time"]["total_minutes"]
    assert daily["subject_share"]["actual"]["by_subject"] == month["time"]["by_subject"]
