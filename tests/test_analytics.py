"""周复盘 + 设置 + 导出测试."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest


# ====== Analytics ======

def _seed_done_record(subject, task_type, est, actual, due_date_str, is_optional=False, ttype_override=None):
    """直接 SQL 插一条已完成作业 (绕过 PATCH)."""
    return _seed_record(subject, task_type, est, actual, due_date_str,
                        is_optional=is_optional, ttype_override=ttype_override,
                        status="done")


def _seed_record(subject, task_type, est, actual, due_date_str,
                 is_optional=False, ttype_override=None, status="done"):
    """插一条任意状态的作业。actual 传 None 即"未填实际用时"。"""
    import sqlite3
    from app import config
    conn = sqlite3.connect(config.DB_PATH)
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO assignments
           (subject, task_name, due_date, deadline_time, is_optional,
            est_minutes, actual_minutes, status, task_type, source_span,
            confidence, batch_id, created_at, updated_at)
           VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, '', 'high', NULL, ?, ?)""",
        (subject, f"test {task_type} {now}", due_date_str,
         1 if is_optional else 0, est, actual, status,
         ttype_override or task_type, now, now),
    )
    conn.commit()
    conn.close()


def test_analytics_sample_too_small_rejected(app_client):
    # 只有 2 条完成记录
    _seed_done_record("语文", "背诵", 15, 20, "2026-09-21")
    _seed_done_record("数学", "计算", 10, 12, "2026-09-22")
    r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


def test_analytics_aggregates_by_subject_and_type(app_client):
    # 6 条完成: 语文背诵 估 15 实 25 (低估), 数学订正 估 30 实 20 (高估)
    monday = date(2026, 9, 21)  # 周一
    for i in range(3):
        _seed_done_record("语文", "背诵", 15, 25, (monday + timedelta(days=i)).isoformat())
    for i in range(3):
        _seed_done_record("数学", "订正", 30, 20, (monday + timedelta(days=i)).isoformat())
    # mock router 里的 datetime.now() (router 决定 today)
    fake_today = datetime(2026, 9, 25, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 200
    data = r.json()
    assert data["sample_size"] == 6
    types = {f"{x['subject']}|{x['task_type']}": x for x in data["by_type"]}
    assert "语文|背诵" in types
    assert "数学|订正" in types
    assert types["语文|背诵"]["diff_minutes"] > 0  # 低估
    assert types["数学|订正"]["diff_minutes"] < 0  # 高估


def test_observation_excluded_from_analytics(app_client):
    monday = date(2026, 9, 21)
    for i in range(5):
        _seed_done_record("数学", "observation", 10, 30, (monday + timedelta(days=i)).isoformat())
    r = app_client.get("/api/analytics/weekly?week=current")
    # observation 不计入, 实际样本为 0
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


def test_optional_excluded_from_analytics(app_client):
    monday = date(2026, 9, 21)
    for i in range(5):
        _seed_done_record("数学", "计算", 10, 30, (monday + timedelta(days=i)).isoformat(), is_optional=True)
    r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


def test_analytics_last_week(app_client):
    # 上周 5 条 (today 设为 2026-09-25, last_mon = 2026-09-14)
    last_mon = date(2026, 9, 14)
    for i in range(5):
        _seed_done_record("语文", "阅读", 20, 22, (last_mon + timedelta(days=i)).isoformat())
    fake_today = datetime(2026, 9, 25, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/weekly?week=last")
    assert r.status_code == 200
    assert r.json()["week"] == "last"


def test_analytics_invalid_week(app_client):
    r = app_client.get("/api/analytics/weekly?week=banana")
    # Query pattern regex 失败 -> RequestValidationError -> 4003
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_analytics_missing_actual_falls_back_to_est(app_client):
    """未填实际用时的已完成项, 按预估计入 (用户口径: 缺省即与预估一致)。

    这类样本的偏差恒为 0: 估 15 就按实 15 算, 不再因为漏填而整条丢弃。
    """
    monday = date(2026, 9, 21)
    for i in range(5):
        _seed_done_record("语文", "背诵", 15, None, (monday + timedelta(days=i)).isoformat())
    fake_today = datetime(2026, 9, 25, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 200
    data = r.json()
    # 关键: 以前会被 actual_minutes IS NOT NULL 滤掉而报 4011
    assert data["sample_size"] == 5
    row = data["by_type"][0]
    assert row["avg_actual_minutes"] == 15
    assert row["diff_minutes"] == 0


def test_analytics_mixed_filled_and_missing_actual(app_client):
    """有填的按实算, 没填的按预估算, 两者进同一个均值。"""
    monday = date(2026, 9, 21)
    # 3 条填了实际 (估15 实25)
    for i in range(3):
        _seed_done_record("数学", "计算", 15, 25, (monday + timedelta(days=i)).isoformat())
    # 2 条没填 (估10 -> 按实10)
    for i in range(2):
        _seed_done_record("数学", "计算", 10, None, (monday + timedelta(days=3 + i)).isoformat())
    fake_today = datetime(2026, 9, 25, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 200
    row = r.json()["by_type"][0]
    assert row["sample_size"] == 5
    # 实 = (25*3 + 10*2) / 5 = 19, 估 = (15*3 + 10*2) / 5 = 13
    assert row["avg_est_minutes"] == 13
    assert row["avg_actual_minutes"] == 19
    assert row["diff_minutes"] == 6


def test_analytics_missing_actual_not_double_counted(app_client):
    """未完成的项即便没填用时也不能进样本 —— 回退只针对 done。"""
    monday = date(2026, 9, 21)
    for i in range(5):
        _seed_record("语文", "阅读", 15, None, (monday + timedelta(days=i)).isoformat(),
                     status="todo")
    r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


# ====== 每日各科用时 / 校历口径 (2026-10-15) ======
#
# 这组用例守的是"哪天算上学日"。判错的后果不是数字不好看, 而是结论
# 直接说反: 国庆那周把 5 天假期当分母, 日均会被稀释掉一大截, 家长
# 看着以为孩子这周轻松了很多。
#
# 校历数据取自《国务院办公厅关于2026年部分节假日安排的通知》
# (国办发明电〔2025〕7号)。这里的断言同时也在锁那份数据本身 ——
# 明年国务院重排, 改 config 里的表时这些用例会提醒你一并更新。

from app.services.analytics_service import (  # noqa: E402
    build_daily_time_stats, classify_day, _calendar_holidays, _calendar_makeup,
)

_H = _calendar_holidays()
_M = _calendar_makeup()


def _kind(d) -> str:
    return classify_day(d, _H, _M)[0]


def _kinds(mon):
    """一整周 7 天的 kind, 周一为首。"""
    return [_kind(mon + timedelta(days=i)) for i in range(7)]


# ====== 校历数据本身的完整性 ======

def test_national_day_seven_days_are_all_holiday():
    """国庆 10/1(周四)~10/7(周三) 连续七天全部不上学。

    关键是 10/2 与 10/5 落在周五与周一 —— 只按"周末不上学"判断的话,
    这两天会被当成普通上学日, 国庆周的平日档就会凭空多出 2 天。
    """
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(7)]
    assert all(_kind(d) == "holiday" for d in days)
    assert days[0].weekday() == 3, "10/1 应是周四"
    assert days[-1].weekday() == 2, "10/7 应是周三"
    # 落在工作日的那几天必须判假期, 不能判成平日/周五
    assert _kind(date(2026, 10, 2)) == "holiday"   # 周五
    assert _kind(date(2026, 10, 5)) == "holiday"   # 周一


def test_makeup_saturday_is_school_day_not_weekend():
    """核心回归: 调休上班的周六 (10/10) 必须算上学日。

    错判的后果很具体: 国庆调休那天孩子要上学, 作业照做, 但统计把它
    当周末剔掉 —— 于是"国庆周只上了 1 天学"被算成"上了 3 天",
    平日日均直接缩水三分之一, 而界面上看不出任何异常。
    """
    sat = date(2026, 10, 10)
    assert sat.weekday() == 5, "10/10 应是周六"
    assert _kind(sat) == "makeup"
    assert classify_day(sat, _H, _M)[1] == "国庆调休"


def test_sunday_after_national_day_is_off():
    assert _kind(date(2026, 10, 11)) == "off"


def test_makeup_sunday_is_school_day():
    """调休也会落在周日 (9/20), 同样不能按周末剔除。"""
    sun = date(2026, 9, 20)
    assert sun.weekday() == 6, "9/20 应是周日"
    assert _kind(sun) == "makeup"


def test_normal_week_classification():
    """普通周: 周一~周四平日, 周五单列, 周末休息。"""
    assert _kinds(date(2026, 10, 12)) == [
        "plain", "plain", "plain", "plain", "friday", "off", "off",
    ]


def test_mid_autumn_on_friday_is_holiday_not_friday():
    """中秋 9/25 落在周五 —— 必须判假期, 不能判成"周五档"。

    顺序反了会把三天假期的第一天当成普通周五, 周五档凭空多出一天。
    """
    assert _kind(date(2026, 9, 25)) == "holiday"


def test_spring_festival_covers_nine_days():
    """春节 2/15~2/23 共 9 天, 且跨了两周。"""
    days = [date(2026, 2, 15) + timedelta(days=i) for i in range(9)]
    assert all(_kind(d) == "holiday" for d in days)
    assert days[0].weekday() == 6 and days[-1].weekday() == 0


def test_makeup_days_are_all_weekends():
    """调休上班日按定义全落在周末。

    若某天被填成周一, 那它本来就上学, 根本不需要这条规则 —— 说明
    数据填错了, 而错误的调休日会掩盖一个真实的上学日。
    """
    for ds in _M:
        d = date.fromisoformat(ds)
        assert d.weekday() >= 5, f"{ds} 是调休上班日却不在周末"


def test_makeup_and_holiday_do_not_overlap():
    for ds in _M:
        assert ds not in _H, f"{ds} 同时是假期和调休上班日, 判定会二义"


# ====== 用时聚合口径 ======

def _week_of_holiday():
    """today=2026-10-15 (周四) 时, '上周' = 10/5~10/11, 正好覆盖国庆尾巴
    与调休周六, 是最值得验的一段真实区间。"""
    return build_daily_time_stats("last", date(2026, 10, 15))


def test_holiday_week_plain_avg_excludes_holidays():
    """国庆周里只有 10/8(周四) 是平日档。

    10/5~10/7 是假期、10/10 调休、10/9 周五、10/11 周末 —— 若不剔除,
    分母会从 1 变成 5, 日均被摊薄成 1/5。
    """
    r = _week_of_holiday()
    assert r["plain_avg"]["days"] == 1, "平日档应只剩 10/8 一天"
    assert r["friday"]["days"] == 1
    assert r["makeup"]["days"] == 1, "调休 10/10 要计入上学日"
    assert r["school_day_count"] == 3


def test_excluded_days_are_reported():
    """被剔除的日子必须回传给前端, 否则家长以为漏统计了 4 天。"""
    r = _week_of_holiday()
    excluded = {e["date"]: e["kind"] for e in r["excluded_days"]}
    assert excluded == {
        "2026-10-05": "holiday", "2026-10-06": "holiday", "2026-10-07": "holiday",
        "2026-10-11": "off",
    }


def test_makeup_day_minutes_counted_separately():
    """调休上班日的用时要单独成档, 不能混进平日日均。"""
    _seed_done_record("语文", "阅读", 40, 40, "2026-10-10")
    r = _week_of_holiday()
    assert r["makeup"]["total_minutes"] == 40
    assert r["makeup"]["days"] == 1
    # 平日档只有 10/8, 那天的数不该被 10/10 污染
    assert r["plain_avg"]["total_minutes"] == 0


def test_friday_is_not_in_plain_avg():
    """周五含周末作业量, 混进平日日均会得到一个不存在的数。

    周五那天的量用两条 100 分钟凑成 200 —— 单条 est_minutes 上限是 180
    (DB CHECK 约束), 想造一个"明显更大的周五"必须分条。
    """
    for i in range(4):
        _seed_done_record("语文", "阅读", 20, 20,
                          (date(2026, 10, 12) + timedelta(days=i)).isoformat())
    # 周五 10/16, 故意很大
    _seed_done_record("数学", "计算", 100, 100, "2026-10-16")
    _seed_done_record("英语", "背诵", 100, 100, "2026-10-16")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    assert r["plain_avg"]["days"] == 4
    assert r["plain_avg"]["total_minutes"] == 20, "周五的 200 分钟不该进平日日均"
    assert r["friday"]["total_minutes"] == 200


def test_weekend_minutes_reported_separately():
    _seed_done_record("英语", "背诵", 30, 30, "2026-10-18")  # 周日
    r = build_daily_time_stats("current", date(2026, 10, 15))
    assert r["weekend"]["total_minutes"] == 30
    assert r["plain_avg"]["total_minutes"] == 0


def test_school_day_with_zero_minutes_still_counted_in_denominator():
    """上了学却没写作业的那天, 用时确实是 0, 该进平均的分母。

    分母若改成"有作业的天数", 就等于把提前做完的那天从统计里删掉,
    日均只反映"最忙的那些天", 家长会高估孩子的日常负担。
    """
    _seed_done_record("语文", "阅读", 40, 40, "2026-10-12")
    # 10/13~10/15 上学但无作业
    r = build_daily_time_stats("current", date(2026, 10, 15))
    assert r["plain_avg"]["days"] == 4
    assert r["plain_avg"]["total_minutes"] == 10.0  # 40 / 4


def test_by_subject_breakdown_per_day():
    """每日各科要分开, 这是这块功能的核心价值。"""
    _seed_done_record("语文", "阅读", 30, 30, "2026-10-12")
    _seed_done_record("数学", "计算", 20, 20, "2026-10-12")
    _seed_done_record("英语", "背诵", 10, 10, "2026-10-12")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert d12["by_subject"] == {"语文": 30, "数学": 20, "英语": 10}
    assert d12["total_minutes"] == 60


def test_plain_avg_by_subject_is_per_subject_average():
    """各科日均要各自除以天数, 不能拿总数除。

    两语文各 30, 共 60/4=15 是错的 —— 语文日均应是 30 (只有 10/12 有)。
    """
    for day, mins in ((12, 30), (13, 30)):
        _seed_done_record("语文", "阅读", mins, mins, f"2026-10-{day}")
    r = build_daily_time_stats("current", date(2026, 10, 15))
    assert r["plain_avg"]["by_subject"]["语文"] == 15.0  # 60 / 4 天
    assert r["plain_avg"]["total_minutes"] == 15.0


def test_skipped_and_unfinished_excluded_from_time():
    """跳过与未完成的都不产生用时。"""
    _seed_done_record("语文", "阅读", 30, 30, "2026-10-12")
    _seed_record("数学", "计算", 25, None, "2026-10-12", status="skipped")
    _seed_record("英语", "背诵", 15, None, "2026-10-12", status="todo")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert d12["by_subject"] == {"语文": 30}


def test_missing_actual_falls_back_to_estimate():
    """未填实耗按预估计 —— 与看板、日历一致, 三处数字才对得上。"""
    _seed_done_record("语文", "阅读", 25, None, "2026-10-12")
    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert d12["total_minutes"] == 25


def test_observation_excluded_from_time_stats():
    """观察作业不产生书写负担, 与偏差表口径一致。"""
    _seed_done_record("语文", "observation", 30, 30, "2026-10-12")
    _seed_done_record("数学", "计算", 20, 20, "2026-10-12")
    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert "语文" not in d12["by_subject"]
    assert d12["total_minutes"] == 20


def test_optional_included_in_time_stats():
    """选做计入用时统计。

    与偏差表排除选做并不矛盾: 偏差表在评估"估时准不准", 用时统计在回答
    "总共花了多久"。选了又做了, 时间就是花掉了。
    """
    _seed_done_record("语文", "阅读", 30, 30, "2026-10-12", is_optional=True)
    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert d12["total_minutes"] == 30


def test_days_have_seven_entries_with_kind_labels():
    """永远返回 7 天, 含假期 —— 前端要按固定 7 个格子画图。"""
    r = build_daily_time_stats("current", date(2026, 10, 15))
    assert len(r["days"]) == 7
    assert r["days"][0]["weekday_label"] == "周一"
    assert r["days"][6]["weekday_label"] == "周日"


def test_is_school_day_flag_matches_kind():
    r = build_daily_time_stats("current", date(2026, 10, 15))
    for d in r["days"]:
        assert d["is_school_day"] == (d["kind"] in ("plain", "friday", "makeup"))


def test_empty_week_returns_zeros_not_error():
    """没有数据时返回全 0, 不能抛错 —— 复盘页不该整块崩掉。

    today=2026-07-06 (周一), 该周 7/6~7/12 无任何节假日, 是最"干净"的一周:
    平日档 4 天 (周一至周四) + 周五 1 天 + 周末 2 天。
    """
    assert date(2026, 7, 6).weekday() == 0, "7/6 应是周一"
    r = build_daily_time_stats("current", date(2026, 7, 6))
    assert r["plain_avg"]["days"] == 4
    assert r["plain_avg"]["total_minutes"] == 0
    assert r["school_day_count"] == 5
    assert all(d["total_minutes"] == 0 for d in r["days"])


# ====== API ======

def test_weekly_time_endpoint_returns_200(app_client):
    """独立接口: 不受偏差表"样本 < 5 就 4011"的连累。

    一周只完成 3 条作业时偏差表算不出来, 但用时统计照样有结论 ——
    挂在同一条接口后面会被 4011 一起藏掉。
    """
    r = app_client.get("/api/analytics/weekly-time?week=current")
    assert r.status_code == 200
    data = r.json()
    for k in ("days", "plain_avg", "friday", "weekend", "makeup",
              "school_day_count", "excluded_days", "school_avg"):
        assert k in data, f"缺少字段 {k}"


def test_weekly_time_works_with_fewer_than_five_samples(app_client):
    """只有 3 条完成时偏差表报 4011, 用时接口必须照常返回。

    日期按真实"本周一"推算 —— 直接写死 2026-09-21 的话, 那些数据落在
    当前区间之外, 偏差表会因为样本为 0 而报 4011, 断言照样通过, 但
    根本没验到"3 条样本"这个场景。
    """
    import datetime as _dt
    today = _dt.date.today()
    monday = today - _dt.timedelta(days=today.weekday())
    for i in range(3):
        _seed_done_record("语文", "阅读", 20, 20, (monday + _dt.timedelta(days=i)).isoformat())

    assert app_client.get("/api/analytics/weekly?week=current").status_code == 400, \
        "3 条样本应触发偏差表的样本不足"
    r = app_client.get("/api/analytics/weekly-time?week=current")
    assert r.status_code == 200
    assert r.json()["plain_avg"]["total_minutes"] > 0, "用时统计应照常给出结论"


def test_weekly_time_invalid_week_rejected(app_client):
    r = app_client.get("/api/analytics/weekly-time?week=banana")
    assert r.status_code == 400


# ====== 前端渲染接线 ======

def _app_js() -> str:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    return (root / "app/static/app.js").read_text(encoding="utf-8")


def _code(js: str) -> str:
    """去掉注释行, 避免注释里的文字被当成实现 (同 test_row_actions 的约定)."""
    return "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))


def test_load_weekly_clears_container_before_append():
    """切「本周 / 上周」必须先清空容器。

    回归: 用时统计与偏差表都是 appendChild 进去的, 不清空的话点「上周」
    会在旧的一周下面**再接一周** —— 页面上同时出现两周的柱子与分档卡,
    家长会以为那是"两周合计"。
    """
    body = _code(_app_js()).split("async function loadWeekly(week) {")[1].split("\n}")[0]
    clear = body.find("root.innerHTML = ''")
    first_append = body.find("root.appendChild")
    assert clear >= 0, "loadWeekly 必须先清空 weekly-content"
    assert first_append > clear, "清空必须发生在 appendChild 之前"


def test_load_weekly_has_no_stuck_loading_placeholder():
    """不要用 innerHTML 塞「加载中…」占位。

    回归: 两块内容都用 appendChild 追加, appendChild 顶不掉先放进去的
    占位节点, 于是页面上会永远留着一行"加载中…"。

    必须先剥注释 —— 这条断言本身就得在注释里解释"为什么不能写加载中",
    不剥的话它会匹配到自己。
    """
    body = _code(_app_js()).split("async function loadWeekly(week) {")[1].split("\n}")[0]
    assert "加载中" not in body, "占位不会被 appendChild 顶掉, 会永久残留"


def test_deviation_table_still_renders():
    """偏差表那块不能因为重构整块丢失。"""
    js = _app_js()
    assert "function renderDeviationTable(" in js
    assert '<th>偏差</th>' in js


# ====== 科目顺序固定 (语 -> 数 -> 英) ======
# 用户要求: 每天各科按固定顺序堆叠。不固定的话色块位置逐日变化
# (取决于入库顺序), 家长没法把颜色对应到科目, 堆叠图就失去意义。


def test_by_subject_order_is_fixed_regardless_of_insert_order():
    """核心回归: by_subject 的键序必须是 语文->数学->英语。

    故意倒着插入 (英->数->语)。dict 的键序来自 SQL 返回顺序,
    不显式排序的话这里就会输出 英语/数学/语文, 柱子里最底那段
    从"语文"变成"英语", 家长看图会直接读错。
    """
    for subj in ("英语", "数学", "语文"):
        _seed_done_record(subj, "阅读", 10, 10, "2026-10-12")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert list(d12["by_subject"].keys()) == ["语文", "数学", "英语"]


def test_order_holds_when_a_subject_is_missing():
    """缺一科时其余科目顺序不变 (语文在底, 不因为数学缺席而上移)。"""
    _seed_done_record("英语", "阅读", 10, 10, "2026-10-12")
    _seed_done_record("语文", "阅读", 10, 10, "2026-10-12")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert list(d12["by_subject"].keys()) == ["语文", "英语"]


def test_order_matches_config_subjects():
    """顺序来源是 config.SUBJECTS, 加科目时改那一处即可。"""
    from app import config
    _seed_done_record("英语", "阅读", 10, 10, "2026-10-12")
    _seed_done_record("语文", "阅读", 10, 10, "2026-10-12")
    _seed_done_record("数学", "阅读", 10, 10, "2026-10-12")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    d12 = next(x for x in r["days"] if x["date"] == "2026-10-12")
    assert list(d12["by_subject"].keys()) == list(config.SUBJECTS)


def test_plain_avg_uses_same_subject_order():
    """日均里的科目顺序也必须一致。

    原实现用 sorted(), 按 Unicode 码位排出来是 数学 -> 语文 -> 英语,
    与逐日的语数英对不上, 图例与 chips 的颜色就会跟柱子错位。
    """
    for subj in ("英语", "数学", "语文"):
        _seed_done_record(subj, "阅读", 10, 10, "2026-10-12")

    r = build_daily_time_stats("current", date(2026, 10, 15))
    assert list(r["plain_avg"]["by_subject"].keys()) == ["语文", "数学", "英语"]


def test_subject_column_is_constrained_to_three_subjects(app_client):
    """subject 有 CHECK 约束, 只有语数英三种值。

    这条不是为了测排序, 而是替下面那件事兜底: 既然库里不可能出现
    表外科目, 排序函数里的"其余科目按名称排"分支就是**不可达**的。
    真要加第四科, 得同时改 schema 的 CHECK、config.SUBJECTS 与 AI
    提示词 —— 那时这条会先失败, 提醒一并处理。
    """
    r = app_client.post("/api/assignments/batch", json={"batch_id": None, "items": [{
        "subject": "道法", "task_name": "第四科", "due_date": "2026-10-12",
        "deadline_time": None, "is_optional": False, "est_minutes": 10,
        "task_type": "other", "source_span": "x", "confidence": "high",
    }]})
    assert r.status_code == 400, "库里不该出现第四科"


def test_plain_avg_order_and_chart_order_agree():
    """日均 chips 与柱状图堆叠必须用同一套顺序 (语->数->英)。

    两处各排一次的话, 有一天缺了数学就会错位: 柱子里英语上移到了
    中间, chips 里英语还在最前, 家长按 chips 找颜色就找错段。
    """
    code = _code(_app_js())
    chart = code.split("function renderTimeChart(r) {")[1].split("\nfunction ")[0]
    buckets = code.split("function renderBuckets(r) {")[1].split("\nfunction ")[0]
    assert "orderedSubjects(d.by_subject)" in chart
    assert "orderedSubjects(pa.by_subject)" in buckets


# ====== 图表渲染 ======

def test_chart_uses_fixed_subject_order():
    """柱状图必须走 orderedSubjects, 不能用 Object.entries (键序不定)。"""
    code = _code(_app_js())
    body = code.split("function renderTimeChart(r) {")[1].split("\nfunction ")[0]
    assert "orderedSubjects(d.by_subject)" in body, "堆叠顺序必须走固定科目顺序"
    assert "Object.entries(d.by_subject)" not in body, \
        "Object.entries 的键序来自入库顺序, 不能用来定堆叠顺序"


def test_chart_labels_each_segment():
    """每个够高的色块都要标出分钟数。"""
    code = _code(_app_js())
    body = code.split("function renderTimeChart(r) {")[1].split("\nfunction ")[0]
    assert 'class="wt-seg"' in body, "段内分钟数标签缺失"
    # 太窄的段不能塞数字, 否则会压到相邻段上
    assert "if (h >= 13)" in body, "需要按段高决定是否标注"


def test_chart_has_subject_legend():
    """柱子下方要有科目图例, 否则颜色对不上名字。"""
    code = _code(_app_js())
    assert 'class="wt-legend wt-subjects"' in code


def test_subject_order_defined_exactly_once():
    """回归: SUBJECT_ORDER 只能有一处定义。

    复盘页曾自己又声明一次 const SUBJECT_ORDER, 而文件头部已有同名
    常量 —— const 不允许重复声明, 整个 app.js 抛 SyntaxError,
    **全站白屏** (不只是复盘页坏)。这类错误肉眼极难发现, 故用
    `node --check` 守住。需要 node, 没有就跳过。
    """
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 node")
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    js = root / "app/static/app.js"
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"app.js 语法错误:\n{r.stderr[-800:]}"
    # 常量本身也数一遍, 给出比 SyntaxError 更直接的提示
    decls = re.findall(r"^const SUBJECT_ORDER\s*=", _app_js(), re.M)
    assert len(decls) == 1, f"SUBJECT_ORDER 有 {len(decls)} 处定义, 会重复声明"


# ====== Settings ======
def test_settings_default_seeded(app_client):
    r = app_client.get("/api/settings")
    assert r.status_code == 200
    items = {it["key"]: it["value"] for it in r.json()["items"]}
    assert "grade" in items
    assert "subject_baseline_minutes" in items


def test_settings_update_baseline(app_client):
    new_val = json.dumps({"语文": 25, "数学": 20, "英语": 20})
    r = app_client.patch("/api/settings/task_type_baseline_minutes", json={"value": new_val})
    assert r.status_code == 200
    r2 = app_client.get("/api/settings")
    items = {it["key"]: it["value"] for it in r2.json()["items"]}
    assert items["task_type_baseline_minutes"] == new_val


def test_settings_invalid_key_rejected(app_client):
    r = app_client.patch("/api/settings/not-a-key", json={"value": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4012"


def test_settings_invalid_json_value_rejected(app_client):
    r = app_client.patch("/api/settings/subject_baseline_minutes", json={"value": "not json {"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4013"


def test_settings_invalid_time_format_rejected(app_client):
    r = app_client.patch("/api/settings/dinner_time", json={"value": "下午六点半"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4013"


# ====== Export ======

def test_export_returns_json(app_client):
    _seed_done_record("语文", "背诵", 15, 20, "2026-09-21")
    r = app_client.get("/api/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.content)
    assert body["schema_version"] == 1
    assert body["counts"]["assignments"] >= 1
    assert "exported_at" in body


def test_export_empty_db(app_client):
    r = app_client.get("/api/export")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["counts"]["assignments"] == 0


# ====== Health ======

def test_health_response_shape(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    for k in ("status", "ai_reachable", "ai_model", "db_ok"):
        assert k in data