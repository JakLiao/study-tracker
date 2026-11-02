"""复盘页: 周报只在「本周」下展示 (2026-10-31)

用户反馈: 切到「上周」「本月」标签时, 页面顶部仍挂着「本周课后周报」。

原因: /weekly-report 永远算的是本周这一周, 与上面的周期按钮无关。
前端无条件把它 append 上去, 于是选「上周」时, 顶上这块写的是本周的
完成率/用时, 下面画的却是上周的柱状图 —— 两套区间混在一页, 家长
会以为数字对不上。

修法: 非本周周期不请求也不渲染周报 (请求都不发, 免得白拉一次)。
本用例守两件事:
  1. 代码层面: 周报的渲染由 period === 'current' 把关
  2. 运行时 (jsdom): 点「上周」后 .report-card 消失, 点回「本周」又出现
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


def _app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _code(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


# ====== 1. 代码层面 ======

def test_report_gated_on_current_period():
    """周报必须由 period === 'current' 把关, 不能无条件 append。"""
    code = _code(_app_js())
    m = re.search(r"async function loadReview\(period\) \{(.*?)\n\}", code, re.S)
    assert m, "缺少 loadReview(period)"
    body = m.group(1)
    assert "period === 'current'" in body, \
        "周报只在本周展示, 必须显式判断 period === 'current'"
    # 周报的 append 要跟着这个判断走, 不能无条件 appendChild
    assert re.search(r"if \(report\) root\.appendChild\(report\)", body), \
        "周报必须条件 append —— 否则非本周周期下仍会画出来"


def test_non_current_period_does_not_request_report():
    """非本周周期连请求都不该发 —— /weekly-report 算的是本周, 拉回来也没用。"""
    code = _code(_app_js())
    m = re.search(r"async function loadReview\(period\) \{(.*?)\n\}", code, re.S)
    body = m.group(1)
    # loadWeeklyReport() 必须挂在 period === 'current' 的三元里
    assert re.search(r"period === 'current' \? loadWeeklyReport\(\)", body), \
        "周报请求必须挂在 period === 'current' 上, 否则白拉一次本周数据"


def test_report_still_first_in_current_period():
    """本周周期下周报仍在最上面 (先于用时统计 append)。"""
    code = _code(_app_js())
    # 匹配到 "loadReviewTime(period" 就停 (不写右括号): 用时统计后来多了
    # 一个月份参数 (2026-11-03 加「上月」周期), 写死右括号会让这条在
    # 签名变化时误报 —— 它要守的是**调用顺序**, 不是参数个数。
    assert code.index("loadWeeklyReport()") < code.index("loadReviewTime(period")


# ====== 2. 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

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
  { date: '2026-10-28', weekday: 2, weekday_label: '周三', kind: 'friday', kind_label: '周五',
    is_school_day: true, by_subject: { '语文': 50 }, total_minutes: 50 },
  { date: '2026-10-29', weekday: 3, weekday_label: '周四', kind: 'plain', kind_label: '周四',
    is_school_day: true, by_subject: { '英语': 60 }, total_minutes: 60 },
  { date: '2026-10-30', weekday: 4, weekday_label: '周五', kind: 'plain', kind_label: '周五',
    is_school_day: true, by_subject: { '语文': 30 }, total_minutes: 30 },
];

const TIME_STATS = {
  period: 'current', period_start: '2026-10-26', period_end: '2026-11-01',
  days: DAYS,
  plain_avg: { days: 4, by_subject: {}, total_minutes: 46 },
  friday: { days: 0, total_minutes: 0, avg_minutes: 0 },
  weekend: { days: 0, total_minutes: 0, avg_minutes: 0 },
  makeup: { days: 0, total_minutes: 0, avg_minutes: 0 },
  school_day_count: 5, excluded_days: [],
  school_avg: { days: 5, by_subject: {}, total_minutes: 47 },
  subject_share: { est: { by_subject: {}, total_minutes: 0 }, actual: { by_subject: {}, total_minutes: 0 } },
};

// 本月课后月报 (2026-11-02)。「本月」周期会拉它。
const MONTH_REPORT = {
  month: '2026-10', month_start: '2026-10-01', month_end: '2026-10-31',
  data_end: '2026-10-31', elapsed_days: 31, is_partial: false,
  completion: { total: 42, optional: 0, required: 42, settled: 31, rate: 74 },
  time: { total_minutes: 505, avg_daily_minutes: 101, school_days: 5, by_subject: { '语文': 180 } },
  subjects: [{ subject: '语文', minutes: 180, pct: 35.6, avg_per_day: 36 }],
  weeks: [{ label: '第1周', start: '2026-10-01', end: '2026-10-04', days: 4,
            minutes: 150, avg_minutes: 37.5, done: 12, required: 13, settled: 12, rate: 92 }],
  prev_month: { month: '2026-09', start: '2026-09-01', end: '2026-09-30', days: 30,
                total_minutes: 400, avg_daily_minutes: 80, school_days: 5, done: 28, rate: 70 },
  prev_month_basis: 'same_period',
  delta: { minutes: 105, minutes_pct: 26.3, rate: 4, avg_daily_minutes: 21 },
  highlights: ['本月作业用时 505 分钟, 上学日日均 101.0 分钟'],
  suggestions: ['本月节奏平稳, 保持这个安排就好'],
};

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
  if (s.includes('/monthly-report')) return ok(MONTH_REPORT);
  if (s.includes('/analytics/review-time')) return ok(TIME_STATS);
  if (s.includes('/analytics/review')) return ok({ period: 'current', by_type: [], sample_size: 0 });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
// charts.js 必须先于 app.js 加载 (与 index.html 的顺序一致): 复盘页的图
// 走 charts.js 的封装。jsdom 没有 canvas 包, 图表会走"退化成表格"那条
// 降级路径 —— 这正是降级逻辑要守的行为, 所以这里**不**加载 echarts.min.js。
w.eval(fs.readFileSync(require('path').join(require('path').dirname(appJsPath), 'charts.js'), 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8'));

const wait = (ms) => new Promise(r => setTimeout(r, ms));
// 只数**周报**那一张卡。
// 2026-11-02 复盘页多了「本月课后月报」, 它也用 .report-card 这套样式,
// 但它不是周报 —— 按"卡片数"判会把月报算进来。认标题最准。
const reportCards = () => [...doc.querySelectorAll('#review-content .report-kicker')]
  .filter(k => k.textContent.includes('本周课后周报')).length;
const monthCards = () => [...doc.querySelectorAll('#review-content .report-kicker')]
  .filter(k => k.textContent.includes('本月课后月报')).length;

(async () => {
  const out = {};
  await wait(500);
  out.currentCards = reportCards();
  out.currentRequested = calls.filter(c => c.includes('/weekly-report')).length;

  // 点「上周」-> 周报必须消失, 且不再请求 /weekly-report
  calls.length = 0;
  const last = doc.querySelector('.period-btn[data-period="last"]');
  last.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await wait(500);
  out.lastCards = reportCards();
  out.lastRequested = calls.filter(c => c.includes('/weekly-report')).length;

  // 点「本月」-> 同样不展示
  calls.length = 0;
  const month = doc.querySelector('.period-btn[data-period="month"]');
  month.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await wait(500);
  out.monthCards = reportCards();
  out.monthReportCards = monthCards();
  out.monthRequested = calls.filter(c => c.includes('/weekly-report')).length;
  out.monthMonthlyRequested = calls.filter(c => c.includes('/monthly-report')).length;

  // 切回「本周」-> 周报要回来
  calls.length = 0;
  const cur = doc.querySelector('.period-btn[data-period="current"]');
  cur.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await wait(500);
  out.backCards = reportCards();
  out.backRequested = calls.filter(c => c.includes('/weekly-report')).length;

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_period_report_harness.js"
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
def test_current_period_shows_report(live):
    assert live["currentCards"] == 1, "本周周期下应当展示周报"


@needs_jsdom
def test_last_period_hides_report(live):
    assert live["lastCards"] == 0, "选「上周」时不该出现「本周课后周报」"


@needs_jsdom
def test_month_period_hides_report(live):
    assert live["monthCards"] == 0, "选「本月」时不该出现「本周课后周报」"


@needs_jsdom
def test_month_period_shows_monthly_report(live):
    """选「本月」时要出现月报卡, 而且只请求一次月报接口。"""
    assert live["monthReportCards"] == 1, "选「本月」时该出现「本月课后月报」"
    assert live["monthMonthlyRequested"] == 1, \
        f"月报接口被请求了 {live['monthMonthlyRequested']} 次 (应该只 1 次)"


@needs_jsdom
def test_non_current_period_does_not_fetch_report(live):
    assert live["lastRequested"] == 0, "选「上周」时不该再拉本周周报"
    assert live["monthRequested"] == 0, "选「本月」时不该再拉本周周报"


@needs_jsdom
def test_back_to_current_restores_report(live):
    assert live["backCards"] == 1, "切回「本周」后周报要回来"
    assert live["backRequested"] == 1, "切回「本周」要重新拉一次周报"
