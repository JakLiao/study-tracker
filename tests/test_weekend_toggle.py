"""复盘页: 周末显示开关 (2026-10-30)

用户反馈: 复盘页右侧总有两根空柱 (周六/周日) 和一张 "周末 0 分钟" 的卡。
实际场景作业都是上学最后一天 (通常周五) 布置, 周末既没作业也没有
"上了学却没写"的情况 —— 那两根柱和那张卡是纯噪音, 看着还像漏统计。

这个开关只改**前端展示**:
  1. 柱状图里不画周末那两天
  2. 分档对照里不画「周末」卡 (「长档」= 周五 + 周末合计, 分母含周末,
     周末不画时它也说不清, 一起去掉)
  3. 口径说明图例里去掉「休息」一档
  4. 「已剔除 (不上学)」里不列周末

后端统计口径一律不动 (平日日均 / 占比饼图 / 偏差表的算法与数值不变) ——
所以这些用例守的是"开关只影响画什么", 而不是"开关改了数字"。

最要紧的一条: 过滤必须按后端给的 kind === 'off' 精确判定, 不能按
"星期几"过滤。调休上班的周六 (kind='makeup') 与法定假期 (kind='holiday')
都落在周六周日, 但它们是有真实作业的上学日, 按星期几过滤会连着把
真数据一起藏掉。
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


def _index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


def _code(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


# ====== 1. 开关本身 ======

def test_toggle_exists_with_label():
    """复盘页必须有「显示周末」开关。"""
    html = _index_html()
    assert 'id="chk-weekend"' in html, "缺少周末开关"
    m = re.search(r'<label[^>]*class="weekend-toggle"[^>]*>(.*?)</label>', html, re.S)
    assert m, "开关必须包在 label 里 (点文字也能切换)"
    label = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    assert label == "显示周末", label


def test_toggle_is_unchecked_by_default():
    """默认不展示 —— 勾选框不能预先打勾。"""
    html = _index_html()
    m = re.search(r'<input[^>]*id="chk-weekend"[^>]*>', html)
    assert m, "缺少周末开关"
    assert "checked" not in m.group(0), "默认不勾选 (不展示周末)"


def test_toggle_default_is_hidden_in_code():
    """没存过偏好时必须返回"不展示"。

    localStorage 里没这个键 = 用户没设置过 = 走默认 (不展示)。
    读成 true 就等于把"默认不展示"倒过来了。
    """
    code = _code(_app_js())
    m = re.search(r"function showWeekend\(\) \{(.*?)\n\}", code, re.S)
    assert m, "缺少 showWeekend()"
    body = m.group(1)
    assert "getItem" in body
    # 取出值后必须与 '1' 比对: 没存过 (null) 不等于 '1' -> false
    assert "=== '1'" in body, "未设置时必须视为 false (默认不展示)"


def test_toggle_state_persisted_in_local_storage():
    """偏好要记住, 否则家长每次进来都要重新勾。"""
    code = _code(_app_js())
    assert "localStorage.setItem(" in code, "开关状态必须存起来"
    assert "localStorage.getItem(" in code


# ====== 2. 过滤口径: 必须按 kind, 不能按星期几 ======

def test_filter_uses_kind_off_not_weekday():
    """周末的判定必须用后端给的 kind === 'off'。

    按星期几 (getDay() >= 5) 过滤会误伤:
      - 调休上班的周六 (kind='makeup', 有真实作业)
      - 落在周末的法定假期 (kind='holiday', 有真实作业)
    实测数据里 10/10 (周六, 调休) 有 10 条作业, 藏起来就是丢数据。
    """
    code = _code(_app_js())
    assert "kind !== 'off'" in code, "必须按 kind === 'off' 过滤周末"
    # 不能出现按星期几过滤的写法
    assert not re.search(r"getDay\(\)\s*[>>=]", code), "不能按星期几判断周末"


def test_filter_does_not_touch_backend_statistics():
    """开关不能改后端算出来的统计口径。

    平日日均 / 分档 / 偏差表都是后端算好的, 前端不能自己重算 ——
    一旦前端复制一遍口径, 两边迟早漂移。

    **subject_share 是唯一的例外** (2026-11-02 改): 后端给的是**整段**
    的合计, 而柱状图按开关把周末藏了。周末不上学但可能真写了作业
    (实测 10/31 与 11/01 各 10 分钟语文), 于是柱状图 685、环图 705,
    同一屏上两个数对不上, 家长只会以为图画错了。

    所以占比改由"看得见的那几天"重算 —— 但重算只能**加总后端给的逐日
    字段** (by_subject / est_by_subject), 不能在前端另立一套"预算是
    怎么算的"规则。这条断言守的就是这个区别。
    """
    code = _code(_app_js())
    m = re.search(r"function reviewWeekendView\(r\) \{(.*?)\n\}", code, re.S)
    assert m, "缺少 reviewWeekendView (展示用数据)"
    body = m.group(1)
    # 只允许覆盖 days 与 excluded_days 两个"要画出来"的字段
    overrides = re.findall(r"^\s*(\w+):", body, re.M)
    for field in overrides:
        assert field in ("days", "excluded_days", "subject_share"), \
            f"不能改 {field} —— 后端统计口径不许前端重算"
    # 数字字段不能被覆盖
    for field in ("plain_avg", "friday", "weekend", "makeup", "school_avg"):
        assert f"{field}:" not in body, f"{field} 是后端算的统计口径, 不能被前端改写"
    # 占比的重算必须走 shareOfDays (加总后端逐日字段), 不能自己写一套
    assert "shareOfDays(" in body, \
        "占比重算必须用 charts.js 的 shareOfDays (加总后端逐日字段)"


def test_share_reconciliation_uses_backend_per_day_fields():
    """重算只能加总后端给的逐日字段, 不能在前端另立口径。"""
    charts = (ROOT / "app/static/charts.js").read_text(encoding="utf-8")
    body = re.search(r"function shareOfDays\(days\) \{(.*?)\n\}", charts, re.S)
    assert body, "charts.js 缺少 shareOfDays"
    inner = body.group(1)
    assert "by_subject" in inner, "实际合计必须来自后端的逐日 by_subject"
    assert "est_by_subject" in inner, "预估合计必须来自后端的逐日 est_by_subject"
    # 不能出现"把实际当成预估"这种前端自造口径的写法
    assert "est_by_subject" in inner and "est_minutes" not in inner, \
        "前端不能自己去推预估值"


def test_weekend_bucket_hidden_when_off():
    """周末不展示时, 「周末」那张卡不能画出来。"""
    code = _code(_app_js())
    body = re.search(r"function renderBuckets\(r, withWeekend\) \{(.*?)\n\}", code, re.S)
    assert body, "缺少 renderBuckets"
    inner = body.group(1)
    m = re.search(r"if \(withWeekend\) \{(.*?)\n  \}", inner, re.S)
    assert m, "周末那两档必须由 withWeekend 把关"
    assert "周末" in m.group(1), "周末档应在 withWeekend 分支里"
    assert "长档" in m.group(1), "长档 (= 周五 + 周末合计) 与周末同进同出"


def test_legend_drops_rest_entry_when_off():
    """图例不能解释一个看不见的东西 —— 周末不展示时去掉「休息」。"""
    code = _code(_app_js())
    body = re.search(r"function renderLegend\(withWeekend\) \{(.*?)\n\}", code, re.S)
    assert body, "缺少 renderLegend(withWeekend)"
    assert "k !== 'off'" in body.group(1), "图例要按 withWeekend 过滤「休息」"


# ====== 3. 周期与开关的联动 ======

def test_period_highlight_and_data_stay_in_sync():
    """按钮高亮与画出来的数据必须是同一个周期。

    以前高亮由点击事件单独改, 而进页面时固定 loadReview('current'),
    两者会脱节: 家长选了「本月」, 切去看板再回来, 数据变「本周」而
    按钮还亮着「本月」。
    """
    code = _code(_app_js())
    assert "function setReviewPeriod(" in code, "周期切换必须走统一入口"
    body = re.search(r"function setReviewPeriod\(period\) \{(.*?)\n\}", code, re.S)
    assert body
    inner = body.group(1)
    assert "classList.toggle('primary'" in inner, "高亮必须在同一个函数里改"
    assert "loadReview(period)" in inner, "数据必须用同一个 period 加载"
    # switchTab 进页面时不能再写死 'current'
    assert "loadReview('current')" not in code, "进页面要保留上次选的周期"


def test_toggle_redraws_current_period():
    """切换开关后按当前周期重画, 不能跳回本周。"""
    code = _code(_app_js())
    assert "loadReview(_reviewPeriod)" in code, "切换开关后要按当前周期重画"


# ====== 4. 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

// 一周的七天: 周一~周五有作业, 周六周日是"不上学的普通休息日"。
// 10/10 (周六) 特意做成 makeup (调休上班, 有真实作业) —— 它也落在周六,
// 但绝不能被当成周末藏掉。
// 周末那两天也给了 10 分钟语文: 真实数据里周末确实可能有作业
// (2026-11-02 实测: 10/31 与 11/01 各 10 分钟语文)。这样才能验住
// "柱状图与占比环必须对得上账" —— 周末藏起来时两张图都要少这 20 分钟。
const DAYS = [
  { date: '2026-10-19', weekday: 0, weekday_label: '周一', kind: 'plain', kind_label: '周一',
    is_school_day: true, by_subject: { '语文': 55 }, est_by_subject: { '语文': 60 }, total_minutes: 55 },
  { date: '2026-10-20', weekday: 1, weekday_label: '周二', kind: 'plain', kind_label: '周二',
    is_school_day: true, by_subject: { '数学': 40 }, est_by_subject: { '数学': 40 }, total_minutes: 40 },
  { date: '2026-10-21', weekday: 2, weekday_label: '周三', kind: 'friday', kind_label: '周五',
    is_school_day: true, by_subject: { '语文': 50 }, est_by_subject: { '语文': 50 }, total_minutes: 50 },
  { date: '2026-10-22', weekday: 3, weekday_label: '周四', kind: 'makeup', kind_label: '国庆调休',
    is_school_day: true, by_subject: { '英语': 60 }, est_by_subject: { '英语': 60 }, total_minutes: 60 },
  { date: '2026-10-23', weekday: 4, weekday_label: '周五', kind: 'plain', kind_label: '周五',
    is_school_day: true, by_subject: { '语文': 30 }, est_by_subject: { '语文': 35 }, total_minutes: 30 },
  { date: '2026-10-24', weekday: 5, weekday_label: '周六', kind: 'off', kind_label: '周末',
    is_school_day: false, by_subject: { '语文': 10 }, est_by_subject: { '语文': 10 }, total_minutes: 10 },
  { date: '2026-10-25', weekday: 6, weekday_label: '周日', kind: 'off', kind_label: '周末',
    is_school_day: false, by_subject: { '语文': 10 }, est_by_subject: { '语文': 10 }, total_minutes: 10 },
];

const TIME_STATS = {
  period: 'current', period_start: '2026-10-19', period_end: '2026-10-25',
  days: DAYS,
  plain_avg: { days: 3, by_subject: { '语文': 28.3, '数学': 13.3 }, total_minutes: 41.7 },
  friday: { days: 1, total_minutes: 50, avg_minutes: 50 },
  weekend: { days: 2, total_minutes: 20, avg_minutes: 10 },
  makeup: { days: 1, total_minutes: 60, avg_minutes: 60 },
  school_day_count: 5,
  excluded_days: [
    { date: '2026-10-24', kind: 'off', kind_label: '周末' },
    { date: '2026-10-25', kind: 'off', kind_label: '周末' },
  ],
  school_avg: { days: 5, by_subject: {}, total_minutes: 47 },
  // 后端给的是**整段**的合计 (含周末那 20 分钟)
  subject_share: {
    est: { by_subject: { '语文': 155, '数学': 40, '英语': 60 }, total_minutes: 255 },
    actual: { by_subject: { '语文': 155, '数学': 40, '英语': 60 }, total_minutes: 255 },
  },
};

const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/#/review',
  runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
w.fetch = async (u) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
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
w.eval(fs.readFileSync(appJsPath, 'utf8') +
  // eval 里的顶层函数声明不会变成 window 的属性 (它们落在 eval 自己的
  // 作用域里), 所以要用同一个 eval 里的裸名把它们导出来 —— 与
  // test_edit_assignment.py 的 __b / __setBaseline 同一套路。
  '\n;globalThis.__reviewWeekendView = reviewWeekendView;');

const snapshot = () => {
  const c = doc.querySelector('#view-review');
  // 2026-11-02: 柱状图从手写 SVG 换成了 ECharts (canvas), DOM 里不再有
  // <rect>。所以直接读**图表 option** —— 走的是页面自己的
  // reviewWeekendView() + buildDailyTimeOption(), 与真实渲染同一条路径。
  // jsdom 没有 canvas, 页面走的是"退化成表格"那条路, 但 option 一样算得出来。
  const view = w.__reviewWeekendView(TIME_STATS);
  const opt = w.StudyCharts.buildDailyTimeOption(view, {});
  const offSeries = (opt.series || []).find(s => s.name === '不上学');
  // 两张图的合计必须是同一个数: 柱状图各学科堆叠加起来 === 占比环的合计。
  // 周末藏起来时两张图都要少那 20 分钟 —— 藏一半就等于告诉家长图画错了。
  const barTotal = (opt.series || [])
    .filter(s => s.name !== '不上学')
    .reduce((a, s) => a + (s.data || []).reduce((x, y) => x + (y || 0), 0), 0);
  const shareTotal = (view.subject_share && view.subject_share.actual.total_minutes) || 0;
  return {
    xdates: (opt.xAxis && opt.xAxis.data) || [],
    offBarCount: offSeries ? offSeries.data.filter(v => v != null).length : 0,
    barCount: (opt.series || []).filter(s => s.name !== '不上学').length,
    barTotal,
    shareTotal,
    bucketTitles: [...c.querySelectorAll('.wt-bucket-title')].map(t => t.textContent.trim()),
    legendLabels: [...c.querySelectorAll('.wt-legend .wt-lg')].map(t => t.textContent.trim()),
    excluded: (c.querySelector('.wt-excluded') || {}).textContent || '',
    // jsdom 不实现 innerText, 用 textContent
    head: (c.querySelector('.wt-head') || {}).textContent || '',
  };
};

(async () => {
  const out = {};
  await new Promise(r => setTimeout(r, 400));
  out.defaultOn = doc.getElementById('chk-weekend').checked;
  out.defaultState = snapshot();

  // 勾上 -> 周末那两天要画出来
  const box = doc.getElementById('chk-weekend');
  box.checked = true;
  box.dispatchEvent(new w.Event('change', { bubbles: true }));
  await new Promise(r => setTimeout(r, 400));
  out.checkedState = snapshot();
  out.stored = w.localStorage.getItem('review_show_weekend');

  // 取消勾选 -> 周末又要藏起来
  box.checked = false;
  box.dispatchEvent(new w.Event('change', { bubbles: true }));
  await new Promise(r => setTimeout(r, 400));
  out.uncheckedState = snapshot();

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_wk_harness.js"
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
def test_default_is_unchecked(live):
    assert live["defaultOn"] is False


@needs_jsdom
def test_default_hides_weekend_bars(live):
    """默认不展示: 那两根虚线的"不上学"柱不能画出来。"""
    assert live["defaultState"]["offBarCount"] == 0, \
        f"默认仍在画周末柱: {live['defaultState']['xdates']}"


@needs_jsdom
def test_default_hides_weekend_bucket_card(live):
    titles = live["defaultState"]["bucketTitles"]
    assert "周末" not in titles, titles
    assert "长档" not in titles, "长档分母含周末, 周末不画时它也说不清"


@needs_jsdom
def test_default_excluded_note_omits_weekend(live):
    assert "周末" not in live["defaultState"]["excluded"], \
        live["defaultState"]["excluded"]


@needs_jsdom
def test_default_legend_omits_rest(live):
    labels = " ".join(live["defaultState"]["legendLabels"])
    assert "休息" not in labels, labels


@needs_jsdom
def test_default_statistics_unchanged(live):
    """开关不能改数字: 平日日均仍是 41.7, 上学日 5 天。"""
    head = live["defaultState"]["head"]
    assert "41.7" in head, head
    assert "5 天" in head, head


@needs_jsdom
def test_checked_shows_weekend_bars(live):
    """勾上后: 周末那两根虚线柱要画出来。"""
    assert live["checkedState"]["offBarCount"] == 2, \
        live["checkedState"]["xdates"]


@needs_jsdom
def test_checked_shows_weekend_bucket_card(live):
    titles = live["checkedState"]["bucketTitles"]
    assert "周末" in titles, titles
    assert "长档" in titles, titles


@needs_jsdom
def test_checked_shows_weekend_in_legend(live):
    labels = " ".join(live["checkedState"]["legendLabels"])
    assert "休息" in labels, labels


@needs_jsdom
def test_makeup_saturday_is_never_hidden(live):
    """调休上班的周六 (10/22) 是有真实作业的上学日, 任何时候都不能被藏掉。

    它落在周六, 若按星期几过滤就会连着把这份数据一起藏掉 —— 周末开关
    只能藏 kind === 'off' 的普通休息日。
    """
    for state in ("defaultState", "checkedState", "uncheckedState"):
        dates = live[state]["xdates"]
        assert "10-22" in dates, f"{state}: 调休的周六被藏掉了: {dates}"


@needs_jsdom
def test_toggle_persists(live):
    assert live["stored"] == "1", "勾选状态没存进 localStorage"


@needs_jsdom
def test_unchecking_restores_hidden(live):
    """取消勾选后回到"不展示周末"。"""
    assert live["uncheckedState"]["offBarCount"] == 0
    assert "周末" not in live["uncheckedState"]["bucketTitles"]


# ====== 5. 两张图必须对得上账 (2026-11-02) ======

@needs_jsdom
@pytest.mark.parametrize("state", ["defaultState", "checkedState", "uncheckedState"])
def test_share_total_equals_bar_total(live, state):
    """占比环的合计 === 柱状图的合计, 两种开关状态下都得成立。

    2026-11-02 实测踩过: 默认视图 (周末藏着) 下柱状图合计 685、环图 705,
    差的是周末那 20 分钟 —— 同一屏上两个数对不上, 家长只会以为图画错了。
    """
    s = live[state]
    assert s["shareTotal"] == s["barTotal"], \
        f"{state}: 占比环 {s['shareTotal']} != 柱状图 {s['barTotal']}"


@needs_jsdom
def test_hidden_weekend_minutes_leave_both_charts(live):
    """周末藏起来时, 那 20 分钟两张图都要没有。

    上学的五天是 55+40+50+60+30 = 235; 周末那两天各 10 分钟。
    """
    s = live["defaultState"]
    assert s["barTotal"] == 235, s
    assert s["shareTotal"] == 235, s


@needs_jsdom
def test_shown_weekend_minutes_enter_both_charts(live):
    """勾上「显示周末」后, 两张图都要把这 20 分钟算进去 (235 + 20 = 255)。"""
    s = live["checkedState"]
    assert s["barTotal"] == 255, s
    assert s["shareTotal"] == 255, s
