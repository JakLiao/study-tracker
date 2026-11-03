"""复盘页「上月」周期 + 历史月份回看 (2026-11-03)

用户反馈: 复盘页只有 本周/上周/本月 三个周期, 没有"上个月"的入口。
而「本月」是 month-to-date —— 11 月才过了 3 天, 柱状图上只有三根柱子,
家长真正想问的"上个月过得怎么样"无处可查 (11-03 当天库里 10 月有 173 条
作业, 11 月一条都没有, 本月 tab 看过去是全空的)。

本期做两件事:
  1. 加「上月」周期, 内容与「本月」同构 (用时统计 + 两张图 + 偏差表 +
     月报卡 + 按周对比 + 薄弱项)
  2. 「上月」里可往前翻历史月份

本用例守三件事:
  1. **区间口径**: 上月是**整月** (1 日~月末), 不是 month-to-date;
  2. **环比口径**: 本月比"上月同期", 过去的整月比"上一个整月" —— 两者
     混了就会得出"少用了 95%"这种假结论, 或者让家长对不上账;
  3. **前端交互**: 点「上月」出月报、步进器能翻月、翻完能一键跳回、
     切走再切回月份不丢。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.analytics_service import (
    _month_label,
    _period_range,
    build_month_analytics,
)

ROOT = Path(__file__).resolve().parents[1]

node = shutil.which("node")
jsdom_available = False
if node:
    try:
        subprocess.run(["node", "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False


# ====== 1. 区间口径: 上月是整月 ======

def test_last_month_defaults_to_previous_full_month():
    """不传 month 时, 上月 = 上个月的 1 日 ~ 上个月最后一天 (整月)。"""
    start, end = _period_range("last_month", date(2026, 11, 3))
    assert start == date(2026, 10, 1)
    assert end == date(2026, 10, 31)
    # 整月 —— 不是 month-to-date 那样截到今天。
    assert (end - start).days + 1 == 31


def test_last_month_accepts_explicit_month():
    """传 month=YYYY-MM 时看那个月的整月。"""
    start, end = _period_range("last_month", date(2026, 11, 3), "2026-10")
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 31))


def test_last_month_handles_february_and_leap_year():
    """2 月的天数由日历说了算, 不能写死 30 天。"""
    start, end = _period_range("last_month", date(2026, 11, 3), "2026-02")
    assert (start, end) == (date(2026, 2, 1), date(2026, 2, 28))
    start, end = _period_range("last_month", date(2026, 11, 3), "2024-02")
    assert (start, end) == (date(2024, 2, 1), date(2024, 2, 29))


def test_bad_month_param_falls_back_to_previous_month():
    """month 传坏了当没传 (缺省上个月), 不抛异常把整页打成 500。"""
    start, end = _period_range("last_month", date(2026, 11, 3), "不是月份")
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 31))


def test_other_periods_unchanged():
    """回归: 本周/上周/本月的区间没被「上月」改掉。"""
    today = date(2026, 11, 3)  # 周二
    assert _period_range("current", today) == (date(2026, 11, 2), date(2026, 11, 8))
    assert _period_range("last", today) == (date(2026, 10, 26), date(2026, 11, 1))
    # 本月是 month-to-date —— 截到今天, 不是整月。
    assert _period_range("month", today) == (date(2026, 11, 1), date(2026, 11, 3))


# ====== 2. 环比口径: 同期 vs 整月 ======

def test_month_label_relative_names():
    """月份的名字按"相对今天"给: 就近三个月用相对说法, 再往前用"N月"。"""
    today = date(2026, 11, 3)
    assert _month_label(date(2026, 11, 1), today) == "本月"
    assert _month_label(date(2026, 10, 1), today) == "上月"
    assert _month_label(date(2026, 9, 1), today) == "上上月"
    assert _month_label(date(2026, 7, 1), today) == "7月"


def test_current_month_compares_with_same_period():
    """本月没过完 -> 比"上月同期" (同样天数), 不是上月整月。"""
    a = build_month_analytics(date(2026, 11, 1), date(2026, 11, 3))
    assert a["month_label"] == "本月"
    assert a["prev_month_label"] == "上月"
    assert a["prev_month_basis"] == "same_period"
    # 基准区间 = 上月 1 日 ~ 上月 3 日 (同天数对齐)
    assert a["prev_month"]["start"] == "2026-10-01"
    assert a["prev_month"]["end"] == "2026-10-03"
    assert a["prev_month"]["days"] == 3


def test_past_month_compares_with_whole_previous_month():
    """过去的整月 -> 比**上一个整月**, 基准是全月。"""
    a = build_month_analytics(date(2026, 10, 1), date(2026, 11, 3))
    assert a["month_label"] == "上月"
    assert a["prev_month_label"] == "上上月"
    assert a["prev_month_basis"] == "full_month"
    assert a["is_partial"] is False
    # 10 月有 31 天, 9 月整月就是 30 天 —— 基准不能被截断。
    assert a["prev_month"]["start"] == "2026-09-01"
    assert a["prev_month"]["end"] == "2026-09-30"
    assert a["prev_month"]["days"] == 30


def test_shorter_month_does_not_truncate_previous_month():
    """9 月 (30 天) 比 8 月时, 基准是 8 月**整月** (31 天)。

    旧算法按"同期"截断: 8 月 1~30 日, 少算 8 月 31 日 —— 那是拿 30 天
    比 31 天, 日均与百分比都会偏。这条用例钉住修好的行为。
    """
    a = build_month_analytics(date(2026, 9, 1), date(2026, 11, 3))
    assert a["prev_month_basis"] == "full_month"
    assert a["prev_month"]["start"] == "2026-08-01"
    assert a["prev_month"]["end"] == "2026-08-31"
    assert a["prev_month"]["days"] == 31


def test_old_month_uses_numeric_label():
    """更早的月份用"N月"这种说法 (再往前说"上上上月"就没法读了)。"""
    a = build_month_analytics(date(2026, 7, 1), date(2026, 11, 3))
    assert a["month_label"] == "7月"
    assert a["prev_month_label"] == "6月"
    assert a["prev_month_basis"] == "full_month"


def _seed_done(due_date: str, subject: str = "语文", est: int = 20, actual: int = 25):
    """插一条已完成作业 (绕过 API, 直接 SQL)。"""
    from datetime import datetime as _dt

    import psycopg
    from psycopg.rows import dict_row

    from app import config
    from app.services.tenant import default_child_id, default_family_id

    now = _dt.utcnow()
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        conn.execute(
            """INSERT INTO assignments
               (subject, task_name, due_date, deadline_time, is_optional,
                est_minutes, actual_minutes, status, task_type, source_span,
                confidence, batch_id, created_at, updated_at,
                family_id, child_id, project_id, task_kind)
               VALUES (%s, %s, %s, NULL, %s, %s, %s, 'done', '其他', '',
                       'high', NULL, %s, %s, %s, %s, NULL, 'subject')""",
            (subject, f"seed {due_date}", date.fromisoformat(due_date),
             False, est, actual, now, now,
             default_family_id(), default_child_id()),
        )


def test_baseline_month_partial_is_flagged():
    """基准月只记了后半段时要标出来 —— 否则环比是个假涨幅。

    家里若是 9 月 21 日才开始用这个应用, 9 月就只剩 9/21 之后的数据,
    拿 10 月整月去比会算出"+329%"这种吓人的数字。不说清楚, 家长会
    以为孩子突然变勤快了。
    """
    _seed_done("2026-09-21")
    _seed_done("2026-09-25")
    a = build_month_analytics(date(2026, 10, 1), date(2026, 11, 3))
    assert a["prev_month_partial"] is True
    assert a["first_record_date"] == "2026-09-21"
    # 基准是 9 月 (比 9/21 更早), 所以缺前半段
    assert a["prev_month"]["start"] == "2026-09-01"


def test_baseline_month_not_partial_when_fully_recorded():
    """基准月是完整记录时不该标"缺前半段"。"""
    # 记录从 7 月就有 —— 比 9 月时基准是 8 月, 而 8 月 1 号之前就有记录,
    # 8 月是完整记录的, 环比数字可以当真, 不该加"缺一段"的说明。
    _seed_done("2026-07-20")
    _seed_done("2026-09-21")
    a = build_month_analytics(date(2026, 9, 1), date(2026, 11, 3))
    assert a["first_record_date"] == "2026-07-20"
    assert a["prev_month_partial"] is False


def test_baseline_month_partial_when_first_record_is_inside_it():
    """记录从基准月**中间**才开始 -> 那个月缺前半段, 要标出来。

    比 9 月时基准是 8 月; 家里 8 月 5 号才开始记, 8 月前 4 天是空的 ——
    拿 9 月整月去比这样的 8 月, 环比会偏大。
    """
    _seed_done("2026-08-05")
    a = build_month_analytics(date(2026, 9, 1), date(2026, 11, 3))
    assert a["first_record_date"] == "2026-08-05"
    assert a["prev_month_partial"] is True


def test_baseline_without_any_data_is_not_partial():
    """基准月压根没记录时, 那是"没有基准", 不是"缺前半段"。

    比 3 月时基准是 2 月, 而家里 9 月才开始用应用 —— 2 月一条记录都没有。
    这跟"9 月只记了后半段"是两回事, 文案不能混着说
    (说成"2 月自 09-21 起才有记录"是把 2 月说成了 9 月, 家长会看不懂)。
    """
    _seed_done("2026-09-21")
    a = build_month_analytics(date(2026, 3, 1), date(2026, 11, 3))
    assert a["prev_month_has_data"] is False
    assert a["prev_month"]["total_minutes"] == 0
    # 基准区间是完整的 2 月 (1 日 ~ 28 日)
    assert a["prev_month"]["start"] == "2026-02-01"
    assert a["prev_month"]["end"] == "2026-02-28"


# ====== 3. API ======

def test_api_accepts_last_month_period(app_client):
    """接口认得 period=last_month。"""
    assert app_client.get("/api/analytics/review-time?period=last_month").status_code == 200
    r = app_client.get("/api/analytics/review?period=last_month")
    # 样本不足时是 400 (4011 走异常处理), 不是 422 (参数没被接受)。
    assert r.status_code in (200, 400)


def test_api_rejects_invalid_period_and_month(app_client):
    """回归: 非法 period 仍被拒; 非法 month 也被拒。

    非法参数统一是 400 (不是 FastAPI 默认的 422) —— app/errors.py 有一层
    全局处理把校验错误归一化了, 这里跟着现状走。
    """
    assert app_client.get("/api/analytics/review?period=banana").status_code == 400
    assert app_client.get("/api/analytics/review-time?period=banana").status_code == 400
    assert app_client.get(
        "/api/analytics/review-time?period=last_month&month=banana").status_code == 400


def test_api_review_time_uses_month_range(app_client):
    """带上 month 时, 用时统计的区间就是那个月的整月。"""
    fake_today = datetime(2026, 11, 3, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/review-time?period=last_month&month=2026-10")
    assert r.status_code == 200
    data = r.json()
    assert data["period"] == "last_month"
    assert data["period_start"] == "2026-10-01"
    assert data["period_end"] == "2026-10-31"
    # 整月 31 天逐日明细
    assert len(data["days"]) == 31


def test_api_monthly_report_for_past_month(app_client):
    """月报能按月份取 (接口本来就支持, 这里钉住复盘页要用的那条)。"""
    r = app_client.get("/api/monthly-report?month=2026-10")
    assert r.status_code == 200
    data = r.json()
    assert data["month"] == "2026-10"
    assert data["month_start"] == "2026-10-01"
    assert data["month_end"] == "2026-10-31"
    # 文案依据要在 (前端照着拼标题与口径说明)
    assert "month_label" in data
    assert "prev_month_label" in data
    assert data["prev_month_basis"] in ("same_period", "full_month")


def test_api_monthly_report_labels_for_past_month(app_client):
    """看 10 月 (相对 11 月 = 上月) 时, 名字与基准都对得上。"""
    fake_now = patch("app.routers.report.datetime")
    with patch("app.services.report_service._today") as mock_today:
        mock_today.return_value = date(2026, 11, 3)
        r = app_client.get("/api/monthly-report?month=2026-10")
    assert r.status_code == 200
    data = r.json()
    assert data["month_label"] == "上月"
    assert data["prev_month_label"] == "上上月"
    assert data["prev_month_basis"] == "full_month"


# ====== 4. 前端代码层面 ======

def _app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _code(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


def test_index_html_has_last_month_button():
    """周期按钮行要有第 4 个按钮「上月」。"""
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert 'data-period="last_month"' in html
    assert re.search(r'data-period="last_month"[^>]*>\s*上月\s*<', html)


def test_period_vocabulary_is_shared():
    """period 的取值前后端共用一套词, 别处不要另起名字。"""
    app_js = _app_js()
    assert "last_month: '上月'" in app_js
    router = (ROOT / "app/routers/analytics.py").read_text(encoding="utf-8")
    assert "last_month" in router


def test_month_checks_go_through_helper():
    """所有"是不是月视图"的判断都走 isMonthPeriod()。

    漏改一处, 上月的分档卡就会按"整周合计"画 —— 与柱状图对不上账。
    """
    code = _code(_app_js())
    # 允许的裸判断只有两处: isMonthPeriod 的定义, 与"本月是不是 month-to-date"。
    bare = re.findall(r"[^\w.]period === 'month'", code)
    assert len(bare) <= 1, f"还有 {len(bare)} 处裸的 period === 'month' 没走 isMonthPeriod"
    assert 'return period === \'month\' || period === \'last_month\';' in code


def test_month_nav_hidden_attribute_is_honoured():
    """步进器要真的藏得住 —— 光设 hidden 属性不够。

    作者样式里的 display:flex 会盖掉浏览器对 [hidden] 的 display:none,
    于是步进器在「本周」「上周」「本月」三个周期下也一直露在外面
    (2026-11-03 浏览器实测踩到: DOM 上 hidden 了, 页面上还在)。

    jsdom 不加载 styles.css, 算不出最终样式, 所以这条只能钉住**规则
    本身在不在** —— 与 .quick-row[hidden] / .cal-legend[hidden] 同一套路。
    """
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.review-month-nav\[hidden\]\s*\{\s*display:\s*none", css), \
        "步进器必须有 .review-month-nav[hidden] { display: none } —— 否则它藏不住"


def test_month_report_marks_partial_and_missing_baselines():
    """前端按 payload 给出两种不同的说明。

    - 基准月只记了后半段 -> 说明"缺了一段, 环比偏大"
    - 基准月压根没记录 -> 说明"没有可比的基准"
    """
    code = _code(_app_js())
    assert "prev_month_partial" in code
    assert "prev_month_has_data" in code
    # "没有可比的基准" 与 "缺了一段" 是两条不同的文案
    assert "没有可比的基准" in code
    assert "不是孩子变勤快了" in code


def test_empty_month_does_not_show_zero_completion_rate():
    """空月份的"必做收尾率"要显示 — 而不是 0%。

    0% 读起来是"孩子一项都没做", 但空月份的真实意思是"压根没记录"。
    翻历史月份时会频繁遇到空月份, 这个差别要说准。
    """
    code = _code(_app_js())
    assert "(c.total || 0) === 0 ? '—'" in code


# ====== 加载遮罩 (2026-11-03) ======

def test_loading_mask_is_sibling_of_content():
    """遮罩不能放在 #review-content 里面。

    容器被 innerHTML='' 清空时, 里面的东西会被一起清掉 —— 而遮罩是要
    **活过**这次清空、盖在新内容上面的。放在容器里就等于没做遮罩。
    """
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    m = re.search(r'<div class="review-body">(.*?)</div>\s*</section>', html, re.S)
    assert m, "缺少 .review-body 容器"
    inner = m.group(1)
    assert 'id="review-loading"' in inner
    # 遮罩与内容容器是**兄弟** —— 容器自己不能包着遮罩
    assert re.search(
        r'<div id="review-content"></div>\s*<div class="review-loading"', inner), \
        "遮罩要跟内容容器平级, 不能塞在容器里面"


def test_loading_mask_is_hidden_by_default():
    """遮罩默认是藏着的 —— 不然每次进页面都先盖一层。"""
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    m = re.search(r'<div class="review-loading"[^>]*>', html)
    assert m
    assert "hidden" in m.group(0), "遮罩默认必须 hidden"


def test_loading_mask_has_hidden_css_rule():
    """作者样式里的 display:flex 会盖掉浏览器对 [hidden] 的规则。

    没有这条, 遮罩撤不掉 —— 页面永远停在"加载中"。同 .review-month-nav[hidden]
    踩过的坑 (2026-11-03 浏览器实测)。
    """
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    assert re.search(r"\.review-loading\[hidden\]\s*\{\s*display:\s*none", css), \
        "遮罩必须有 .review-loading[hidden] { display: none }"


def test_loading_mask_minimum_duration():
    """遮罩至少显示一小会儿。

    数据 30ms 就回来时, 遮罩闪一下比不显示还闹 —— 压到一个短但确定的
    时长, 让它像一次有意的过渡, 而不是抽搐。
    """
    code = _code(_app_js())
    assert "REVIEW_LOADING_MIN_MS" in code
    assert re.search(r"const REVIEW_LOADING_MIN_MS = \d+;", code)


def test_weak_card_uses_selected_month():
    """薄弱项读的晚间小结必须是**那个月**的, 不是"最近 31 天"。"""
    dual = (ROOT / "app/static/dual.js").read_text(encoding="utf-8")
    assert "reflectionTagDates(period, ctx.month)" in dual
    assert "period === 'last_month' && month" in dual


def test_empty_month_skips_reflection_requests():
    """空月份不发晚间小结请求 (一天一个, 空月份就是三十来个必然落空的请求)。"""
    code = _code(_app_js())
    assert "skipWeak" in code
    assert re.search(r"skipWeak = period === 'last_month'", code)


# ====== 5. 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const pad = n => String(n).padStart(2, '0');
const now = new Date();
const ymOf = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const CUR = ymOf(now);
const LAST = ymOf(new Date(now.getFullYear(), now.getMonth() - 1, 1));
const PREV2 = ymOf(new Date(now.getFullYear(), now.getMonth() - 2, 1));
const PREV3 = ymOf(new Date(now.getFullYear(), now.getMonth() - 3, 1));

// 与后端 _month_label 同一套规则
function labelOf(k) {
  if (k === CUR) return '本月';
  if (k === LAST) return '上月';
  if (k === PREV2) return '上上月';
  return Number(k.slice(5)) + '月';
}

const REPORT = {
  week_start: '2026-10-26', week_end: '2026-11-01', mode: 'normal',
  completion: { rate: 74, settled: 31, required: 42, done: 31, pending: 11, unfinished: [] },
  time: { total_minutes: 505, avg_daily_minutes: 101, by_subject: { '语文': 180 }, subject_share: { '语文': 36 } },
  deviation: { note: '样本不足', top_underestimate: [], top_overestimate: [], sample_size: 0 },
  blockers: { hard_days: [], bad_focus_days: [], bad_mood_days: [], slow_tasks: [] },
  training: { logs: 0, total_minutes: 0, by_project: [] },
  load: { fullest_day: null, overloaded_days: 0, conflict_days: 0 },
  trend: { prev_done: 0, done_delta: 0, minutes_delta: 0, prev_minutes: 0 },
  praise: [], coach_note: null, suggestions: [],
};

const DAYS = [
  { date: '2026-10-26', weekday: 0, weekday_label: '周一', kind: 'plain', kind_label: '周一',
    is_school_day: true, by_subject: { '语文': 55 }, total_minutes: 55 },
  { date: '2026-10-27', weekday: 1, weekday_label: '周二', kind: 'plain', kind_label: '周二',
    is_school_day: true, by_subject: { '数学': 40 }, total_minutes: 40 },
];

function timeStats(period) {
  return {
    period: period, period_start: '2026-10-01', period_end: '2026-10-31',
    days: DAYS,
    plain_avg: { days: 4, by_subject: {}, total_minutes: 46 },
    friday: { days: 0, total_minutes: 0, avg_minutes: 0 },
    weekend: { days: 0, total_minutes: 0, avg_minutes: 0 },
    makeup: { days: 0, total_minutes: 0, avg_minutes: 0 },
    school_day_count: 5, excluded_days: [],
    school_avg: { days: 5, by_subject: {}, total_minutes: 47 },
    subject_share: { est: { by_subject: {}, total_minutes: 0 }, actual: { by_subject: {}, total_minutes: 0 } },
  };
}

// 月报 mock: 按请求里的月份返回对应名字的报告。
function monthReport(monthKey) {
  const label = labelOf(monthKey);
  const isCur = monthKey === CUR;
  return {
    month: monthKey, month_start: monthKey + '-01', month_end: monthKey + '-28',
    data_end: monthKey + '-28', elapsed_days: isCur ? 3 : 28, is_partial: isCur,
    month_label: label,
    prev_month_label: labelOf(ymOf(new Date(Number(monthKey.slice(0, 4)), Number(monthKey.slice(5)) - 1, 1))),
    prev_month_basis: isCur ? 'same_period' : 'full_month',
    completion: { total: 42, optional: 0, required: 42, settled: 31, rate: 74 },
    time: { total_minutes: 505, avg_daily_minutes: 101, school_days: 5, by_subject: { '语文': 180 } },
    subjects: [{ subject: '语文', minutes: 180, pct: 35.6, avg_per_day: 36 }],
    weeks: [{ label: '第1周', start: monthKey + '-01', end: monthKey + '-04', days: 4,
              minutes: 150, avg_minutes: 37.5, done: 12, required: 13, settled: 12, rate: 92 }],
    prev_month: { month: '2026-09', start: '2026-09-01', end: '2026-09-30', days: 30,
                  total_minutes: 400, avg_daily_minutes: 80, school_days: 5, done: 28, rate: 70 },
    prev_month_basis: isCur ? 'same_period' : 'full_month',
    delta: { minutes: 105, minutes_pct: 26.3, rate: 4, avg_daily_minutes: 21 },
    highlights: [label + '作业用时 505 分钟, 上学日日均 101.0 分钟'],
    suggestions: [label + '节奏平稳, 保持这个安排就好'],
  };
}

const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/#/review',
  runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
const calls = [];
w.fetch = async (u) => {
  const s = String(u);
  calls.push(s);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/weekly-report')) return ok(REPORT);
  if (s.includes('/monthly-report')) {
    const m = (s.match(/[?&]month=(\d{4}-\d{2})/) || [])[1];
    return ok(monthReport(m || CUR));
  }
  if (s.includes('/analytics/review-time')) {
    const p = (s.match(/period=([^&]+)/) || [])[1];
    return ok(timeStats(p));
  }
  if (s.includes('/analytics/review')) {
    const p = (s.match(/period=([^&]+)/) || [])[1];
    return ok({ period: p, period_start: '2026-10-01', period_end: '2026-10-31',
                by_type: [], sample_size: 0 });
  }
  if (s.includes('/daily-wrap')) return ok({});
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(require('path').join(require('path').dirname(appJsPath), 'charts.js'), 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8'));

const wait = (ms) => new Promise(r => setTimeout(r, ms));
const kickers = () => [...doc.querySelectorAll('#review-content .report-kicker')].map(k => k.textContent.trim());
const monthLabel = () => (doc.getElementById('review-month-label') || {}).textContent;
const navHidden = () => (doc.getElementById('review-month-nav') || {}).hidden;
const click = (el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const reqs = (needle) => calls.filter(c => c.includes(needle));

(async () => {
  const out = { LAST, PREV2, PREV3, CUR };

  // 1. 点「上月」
  calls.length = 0;
  click(doc.querySelector('.period-btn[data-period="last_month"]'));
  await wait(500);
  out.lastMonthKickers = kickers();
  out.navHiddenInLastMonth = navHidden();
  out.monthLabelInLastMonth = monthLabel();
  out.monthlyReqs = reqs('/monthly-report');
  out.timeReqs = reqs('/analytics/review-time');
  out.weeklyReqs = reqs('/weekly-report').length;

  // 2. 步进器往前一个月
  calls.length = 0;
  click(doc.getElementById('review-month-prev'));
  await wait(500);
  out.labelAfterPrev = monthLabel();
  out.monthlyReqsAfterPrev = reqs('/monthly-report');
  out.kickersAfterPrev = kickers();

  // 3. 往前再一个月
  calls.length = 0;
  click(doc.getElementById('review-month-prev'));
  await wait(500);
  out.labelAfterPrev2 = monthLabel();
  out.monthlyReqsAfterPrev2 = reqs('/monthly-report');

  // 4. "回到上月"跳回来
  calls.length = 0;
  click(doc.getElementById('review-month-back'));
  await wait(500);
  out.labelAfterBack = monthLabel();

  // 5. 回到上个月时, "下一个月"应该 disabled (不覆盖本月)
  const nextBtn = doc.getElementById('review-month-next');
  out.nextDisabledAtLastMonth = nextBtn ? nextBtn.disabled : null;
  out.prevDisabledAtLastMonth = doc.getElementById('review-month-prev').disabled;

  // 6. 切走再切回: 月份要留住
  calls.length = 0;
  click(doc.getElementById('review-month-prev'));
  await wait(400);
  const keptBefore = monthLabel();
  click(doc.querySelector('#tabs button[data-tab="board"]'));
  await wait(300);
  click(doc.querySelector('#tabs button[data-tab="review"]'));
  await wait(500);
  out.keptLabel = keptBefore;
  out.labelAfterTabRoundTrip = monthLabel();

  // 7. 回归: 本周仍然出周报, 且不���月报
  calls.length = 0;
  click(doc.querySelector('.period-btn[data-period="current"]'));
  await wait(500);
  out.currentKickers = kickers();
  out.currentWeeklyReqs = reqs('/weekly-report').length;
  out.navHiddenInCurrent = navHidden();

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_last_month_harness.js"
    h.parent.mkdir(exist_ok=True)
    h.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(h), str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(ROOT / "app/static/vendor/flatpickr.min.js"),
             str(ROOT / "app/static/vendor/flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1500:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if h.exists():
            h.unlink()


needs_jsdom = pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")


@needs_jsdom
def test_last_month_shows_month_report(live):
    """点「上月」要出「上月课后月报」卡, 且**不**出本周周报。"""
    assert "上月课后月报" in live["lastMonthKickers"], \
        f"「上月」周期该出月报, 实际: {live['lastMonthKickers']}"
    assert not any("本周课后周报" in k for k in live["lastMonthKickers"]), \
        "「上月」周期不该出现本周周报"
    assert live["weeklyReqs"] == 0, "「上月」周期不该去拉本周周报"


@needs_jsdom
def test_last_month_requests_the_right_month(live):
    """月报与用时统计都要带上月份参数, 且带的是上个月。"""
    assert any(f"month={live['LAST']}" in u for u in live["monthlyReqs"]), \
        f"月报请求该带 month={live['LAST']}, 实际: {live['monthlyReqs']}"
    assert any(f"month={live['LAST']}" in u for u in live["timeReqs"]), \
        f"用时统计该带 month={live['LAST']}, 实际: {live['timeReqs']}"


@needs_jsdom
def test_month_nav_visible_only_in_last_month(live):
    """步进器只在「上月」周期出现。"""
    assert live["navHiddenInLastMonth"] is False, "「上月」周期要显示月份步进器"
    assert live["navHiddenInCurrent"] is True, "「本周」周期不该显示月份步进器"


@needs_jsdom
def test_stepper_moves_one_month_back(live):
    """‹ 点一次退一个月, 标题与请求都跟着变。"""
    assert live["labelAfterPrev"] != live["labelAfterPrev2"], "‹ 点了两次应该退两个月"
    assert any(f"month={live['PREV2']}" in u for u in live["monthlyReqsAfterPrev"]), \
        f"退一个月后该请求 month={live['PREV2']}, 实际: {live['monthlyReqsAfterPrev']}"
    assert any(f"month={live['PREV3']}" in u for u in live["monthlyReqsAfterPrev2"]), \
        f"退两个月后该请求 month={live['PREV3']}, 实际: {live['monthlyReqsAfterPrev2']}"


@needs_jsdom
def test_back_button_returns_to_last_month(live):
    """「回到上月」一键跳回最近的一个完整月。"""
    assert live["labelAfterBack"] == live["monthLabelInLastMonth"], \
        f"「回到上月」该跳回 {live['monthLabelInLastMonth']}, 实际 {live['labelAfterBack']}"


@needs_jsdom
def test_next_disabled_at_last_month(live):
    """往后只能到上个月 —— 本月有独立的 tab, 同一月份不能出两套口径。"""
    assert live["nextDisabledAtLastMonth"] is True, "在上月时「下个月」该是禁用的"
    assert live["prevDisabledAtLastMonth"] is False, "往前翻不该被禁 (允许翻到空月份)"


@needs_jsdom
def test_month_survives_tab_switch(live):
    """切走再切回复盘页, 月份要留住 (别把家长刚翻到的月份弹回去)。"""
    assert live["labelAfterTabRoundTrip"] == live["keptLabel"], \
        f"切 tab 回来月份该还是 {live['keptLabel']}, 实际 {live['labelAfterTabRoundTrip']}"


@needs_jsdom
def test_current_period_still_shows_weekly_report(live):
    """回归: 本周周期照旧出周报, 且不出月报。"""
    assert any("本周课后周报" in k for k in live["currentKickers"]), \
        f"本周周期该出周报, 实际: {live['currentKickers']}"
    assert live["currentWeeklyReqs"] == 1, "本周周期该拉一次周报"
