"""复盘页去重回归 (2026-11-02)

2026-11-01 的双角色改造落地后, 家长端复盘页一度有**四张图**:
    app.js  「每日作业用时」   (堆叠柱状)  ─┐
    app.js  「各科时间占比」   (预计/实际两张饼) ─┤
    dual.js 「学科用时分配」   (环形图)        ─┴ 与"实际占比"饼图是同一份数据
    dual.js 「每日用时趋势」   (折线)          ─┴ 与柱状图是同一批 days

四张图只有两份独立数据 —— 家长滚两屏看到的是同一件事的四种画法, 直觉
就是"这页出 bug 了"。本次收敛成**两张图**: 每日用时柱状 + 学科占比环。

这里守四件事:
  1. 页面上只剩两张图 (不多不少);
  2. 同一份数据只画一次 —— 预估/实际共用**一个**环, 用切换换数据;
  3. `/analytics/review-time` 每次切周期**只请求一次** (以前被 app.js 与
     dual.js 各打一遍);
  4. 薄弱项卡跟着**当前周期**走 (以前永远读本周的 /weekly-report)。
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


def _dual_js() -> str:
    return (ROOT / "app/static/dual.js").read_text(encoding="utf-8")


def _code(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


# ====== 1. 静态断言: 重复的实现已经删掉 ======

def test_duplicate_chart_implementations_are_gone():
    """手写的环形图与折线图必须删掉 —— 它们与柱状图/饼图是同一份数据。"""
    dual = _dual_js()
    assert "function renderPDonut(" not in dual, "环形图还在: 与「实际占比」饼图重复"
    assert "function renderPLineChart(" not in dual, "折线图还在: 与柱状图重复"
    assert "p-donut" not in dual
    assert "p-line-chart" not in dual


def test_app_js_no_longer_has_handwritten_pie_geometry():
    """手写 SVG 的扇形几何与标签分档逻辑随 ECharts 一起退休。"""
    js = _app_js()
    for gone in ("function piePoint(", "function pieSlice(",
                 "function pieLabelHalfAngle(", "function pieLabelTier("):
        assert gone not in js, f"{gone} 应该随 SVG 实现一起删掉"


def test_share_chart_has_a_mode_switch():
    """预估 / 实际必须共用一个环, 用切换换数据。"""
    body = _code(_app_js()).split("function renderSubjectShare(r) {")[1].split("\nfunction ")[0]
    assert "预计时间占比" in body
    assert "实际时间占比" in body
    assert body.count("buildSubjectShareOption(") == 1, \
        "两种口径必须共用一个环 (切换换数据), 不能各画一张"


def test_review_time_is_requested_once():
    """dual.js 不能再自己打一遍 /analytics/review-time。

    以前 app.js 的 loadReviewTime 与 dual.js 的 renderParentReviewExtras
    各打一遍, 每次切周期多一次白跑的往返。
    """
    dual = _code(_dual_js())   # 剥注释: 说明文字里提到这个接口是正常的
    assert "/analytics/review-time" not in dual, \
        "dual.js 不该自己拉用时统计 —— 数据由 app.js 传进来"
    app = _code(_app_js())
    assert "renderParentReviewExtras(period, {" in app, \
        "结论区必须把数据传给 dual.js, 而不是让 dual.js 自己拉"


def test_weak_items_follow_the_current_period():
    """薄弱项必须用当前周期的偏差数据, 不能永远读本周。

    以前 renderPWeak 吃的是 /weekly-report (永远算本周), 于是选「上周」时
    分析的是本周的作业 —— 实测上周有 705 分钟数据, 薄弱项却显示
    "这一段还没有足够的记录"。
    """
    weak = _code(_dual_js()).split("function renderPWeak(")[1].split("\nfunction ")[0]
    assert "/weekly-report" not in weak, \
        "薄弱项不该再读 /weekly-report (那个接口永远算本周)"
    assert "deviation" in weak, "薄弱项必须吃当前周期的 deviation"
    assert "renderPWeak(ctx.deviation" in _code(_dual_js()), \
        "renderParentReviewExtras 必须把当前周期的 deviation 传进去"


# ====== 2. 运行时 (jsdom): 页面上到底有几张图 ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const DAYS = [
  { date: '2026-10-19', weekday: 0, weekday_label: '周一', kind: 'plain', kind_label: '周一',
    is_school_day: true, by_subject: { '语文': 55 }, total_minutes: 55 },
  { date: '2026-10-20', weekday: 1, weekday_label: '周二', kind: 'plain', kind_label: '周二',
    is_school_day: true, by_subject: { '数学': 40 }, total_minutes: 40 },
  { date: '2026-10-21', weekday: 2, weekday_label: '周三', kind: 'friday', kind_label: '周五',
    is_school_day: true, by_subject: { '语文': 50 }, total_minutes: 50 },
];

const TIME_STATS = {
  period: 'current', period_start: '2026-10-19', period_end: '2026-10-25',
  days: DAYS,
  plain_avg: { days: 2, by_subject: { '语文': 52.5 }, total_minutes: 52.5 },
  friday: { days: 1, total_minutes: 50, avg_minutes: 50 },
  weekend: { days: 0, total_minutes: 0, avg_minutes: 0 },
  makeup: { days: 0, total_minutes: 0, avg_minutes: 0 },
  school_day_count: 3, excluded_days: [],
  school_avg: { days: 3, by_subject: {}, total_minutes: 48 },
  subject_share: {
    est: { by_subject: { '语文': 85, '数学': 40 }, total_minutes: 125 },
    actual: { by_subject: { '语文': 55, '数学': 40 }, total_minutes: 95 },
  },
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
  if (s.includes('/analytics/review-time')) return ok(TIME_STATS);
  if (s.includes('/analytics/review')) return ok({
    period: 'current', by_type: [], sample_size: 0, top_underestimate: [],
  });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(require('path').join(require('path').dirname(appJsPath), 'charts.js'), 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') +
  '\n;globalThis.__setRole = (r) => { document.documentElement.dataset.role = r; };');

(async () => {
  const out = {};
  await new Promise(r => setTimeout(r, 500));
  const root = doc.querySelector('#review-content');
  // 图表**卡片** (不是 canvas): jsdom 没有 canvas, 页面走的是"退化成表格"
  // 那条路, 画布容器会被 remove 掉。卡片数才是"有几张图"的准数。
  out.chartCount = root.querySelectorAll('.wt-chart-card').length
                 + root.querySelectorAll('.wt-share-card').length;
  // 环图卡片 (学科占比) —— 只能有一个
  out.donutCount = root.querySelectorAll('.wt-share-card').length;
  // 切换按钮: 预估 / 实际 两个
  out.modeButtons = [...root.querySelectorAll('.wt-share-mode')].map(b => b.textContent.trim());
  // 切一次「上周」, 数一数 /analytics/review-time 被请求了几次
  calls.length = 0;
  const last = doc.querySelector('.period-btn[data-period="last"]');
  last.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 500));
  out.reviewTimeCalls = calls.filter(c => c.includes('/analytics/review-time')).length;

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_dedup_harness.js"
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
def test_review_page_has_exactly_two_charts(live):
    """复盘页只有两张图: 每日用时 + 学科占比。"""
    assert live["chartCount"] == 2, f"复盘页上的图表数应为 2, 实际 {live['chartCount']}"


@needs_jsdom
def test_subject_share_has_one_donut(live):
    """预估与实际共用一个环, 不是两个。"""
    assert live["donutCount"] == 1, f"占比环图应为 1 个, 实际 {live['donutCount']}"


@needs_jsdom
def test_share_mode_switch_offers_both(live):
    """切换按钮必须同时给「预估」与「实际」。"""
    labels = " ".join(live["modeButtons"])
    assert "预计时间占比" in labels, live["modeButtons"]
    assert "实际时间占比" in labels, live["modeButtons"]


@needs_jsdom
def test_review_time_is_fetched_once_per_period(live):
    """切一次周期, /analytics/review-time 只能被请求一次。"""
    assert live["reviewTimeCalls"] == 1, \
        f"review-time 被请求了 {live['reviewTimeCalls']} 次 (应该只 1 次)"


def test_weak_item_distinguishes_no_samples_from_no_deviation():
    """薄弱项必须区分"样本不够"与"估得准"两句话。

    2026-11-02 实测: 上周 41 条记录、偏差全为 0, 薄弱项却写
    "这一段还没有足够的记录" —— 家长会以为还欠着记录, 其实估得很准。

    文案本身的逐条断言在 `tests/test_weak_item.js` (node 脚本, 抠出
    dual.js 的 renderPWeak 直接跑); 这里只钉住"必须看样本数"这件事。
    """
    weak = _code(_dual_js()).split("function renderPWeak(")[1].split("\nfunction ")[0]
    assert "sample_size" in weak, \
        "薄弱项必须看样本数: 样本够但没偏差 ≠ 样本不够"
