"""日历双模式: 完成情况 / 用时热力图 (2026-10-30).

用户要求: "日历功能模块, 除了当前完成情况查看的模式, 另外还加个查看完成
时间热力图模式, 可以双模式切换, 完成时间长的话会更加的红。"

这里锁四件事:
  1. 页面有两个模式按钮, 且热力图图例默认收起 (默认仍是完成情况)
  2. heatLevel() 的分档阈值与页面图例逐档一致 —— 说 60-99 就得真的
     把 60 分涂成第 2 档; 图例和代码各自维护一套必然漂移
  3. 每档底色上的文字都要达到 WCAG AA 4.5:1 (颜色越深, 字色必须跟着换)
  4. 运行时: 点"用时热力图"后格子按 actual_total 分档上色, 完成状态类
     全部退场; 切回来复原; 且全程不多打一次 /assignments/calendar 请求
     (两种口径都在同一份响应里, 切模式不该再拉数据)
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
        subprocess.run([node, "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


def _strip(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


EDGES = [60, 100, 140, 180]
# 由阈值推出的图例文字: 1-59 / 60-99 / 100-139 / 140-179 / 180+
LEGEND_LABELS = ["1-59分", "60-99分", "100-139分", "140-179分", "180分+"]


# ====== 接线 ======

def test_two_mode_buttons(index_html):
    """两个按钮各带一个模式值, 且默认选中"完成情况"。"""
    assert 'data-cal-mode="completion"' in index_html
    assert 'data-cal-mode="heat"' in index_html
    m = re.search(r'data-cal-mode="completion"[\s\S]{0,80}?aria-pressed="true"', index_html)
    assert m, "默认模式应仍是完成情况 (aria-pressed=true)"


def test_heat_legend_hidden_by_default(index_html):
    """热力图图例必须默认 hidden —— 默认模式是完成情况, 两份图例同时出现
    会让家长以为两种颜色含义能叠加。"""
    m = re.search(r'id="cal-legend-heat"([^>]*)>', index_html)
    assert m and "hidden" in m.group(1), "热力图图例应默认 hidden"


def test_heat_legend_labels_match_thresholds(index_html):
    """图例文字必须由阈值唯一确定。改阈值却忘了改图例, 家长看到的就
    是"图例说 60-99, 实际 60 分却是第 2 档"这种对不上的读数。"""
    assert f"const HEAT_EDGES = [{', '.join(map(str, EDGES))}];" in app_js_text(), \
        "heatLevel 阈值应恰为 60/100/140/180 (与图例逐档对应)"
    for label in LEGEND_LABELS:
        assert label in index_html, f"图例缺少 {label}"


def app_js_text() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def test_heat_level_is_documented(app_js):
    """分档函数必须存在, 且 0 分不上色 (没投入的日子不该被涂成最浅一档)。"""
    m = re.search(r"function heatLevel\(minutes\) \{(.*?)\n\}", app_js, re.S)
    assert m, "缺少 heatLevel()"
    body = m.group(1)
    assert "return 0" in body, "0 分应返回 0 (不上色)"
    assert "HEAT_EDGES.length + 1" in body, "超过最高阈值应落到最后一档"


def test_mode_switch_does_not_refetch(app_js):
    """切模式不许重新请求 —— 数据里早就有 actual_total。"""
    m = re.search(r"function setCalendarMode\(mode\) \{(.*?)\n\}", app_js, re.S)
    assert m, "缺少 setCalendarMode()"
    body = m.group(1)
    assert "api(" not in body and "loadCalendar" not in body, \
        "切模式只该重画, 不该再拉一次日历数据"
    assert "renderCalendar()" in body


# ====== 样式 ======

@pytest.mark.parametrize("lvl", [1, 2, 3, 4, 5])
def test_every_level_has_background_and_ink(styles, lvl):
    css = _strip(styles)
    m = re.search(rf"\.cal-cell\.heat-l{lvl}\s*\{{([^}}]*)\}}", css)
    assert m, f"缺少 .cal-cell.heat-l{lvl} 规则"
    body = m.group(1)
    assert "background:" in body, f"第 {lvl} 档缺底色"
    assert "--heat-ink:" in body, f"第 {lvl} 档缺前景色"


def test_heat_ink_applies_to_text(styles):
    """前景色必须真的落到日期/用时/徽标上, 否则深底上的文字仍是一片黑。"""
    css = _strip(styles)
    assert re.search(r"\.cal-cell\.heat\s+\.cal-day[\s\S]{0,120}?color:\s*var\(--heat-ink\)", css), \
        "日期数字没用档位 ink"
    assert re.search(r"\.cal-cell\.heat\s+\.cal-time[\s\S]{0,120}?color:\s*var\(--heat-ink\)", css), \
        "用时数字没用档位 ink"


def test_weekend_date_color_does_not_win_over_heat(styles):
    """周末的淡红日期 (#C77B8A) 压在深橙底上几乎看不见。

    两条选择器权重相同, 只能靠顺序取胜 —— 热力规则必须在周末规则之后。
    """
    css = _strip(styles)
    we_pos = css.find(".cal-cell.we .cal-day")
    heat_pos = css.find(".cal-cell.heat .cal-day")
    assert we_pos != -1 and heat_pos != -1
    assert heat_pos > we_pos, "热力日期色必须写在 .cal-cell.we .cal-day 之后"


def test_heat_hover_keeps_the_background(styles):
    """悬停不能把热力底色刷成浅底 —— 底色就是数据。"""
    css = _strip(styles)
    assert ".cal-cell.heat:hover" in css, "热力格子需要自己的悬停反馈"
    # 通用 hover 必须把热力格子排除掉
    assert re.search(r"\.cal-cell:not\(\.heat\):hover", css), \
        "通用 hover 规则没排除 .heat, 划过时热力色会被 surface-2 盖掉"


def test_heat_legend_swatches_exist(styles):
    css = _strip(styles)
    for i in range(6):
        assert re.search(rf"\.lg-heat{i}\s*\{{", css), f"图例缺少第 {i} 档色块"


def test_heat_mode_marks_get_light_backdrop(styles):
    """假期/调休/周末角标在热力格子上必须换浅底。

    原来那层半透明蓝/红底叠在砖红上会糊成一片, 深蓝字只有约 2:1 ——
    格子越红, "这天是调休"的提示反而越看不见, 恰好是最该看清的场合。
    """
    css = _strip(styles)
    m = re.search(r"\.cal-cell\.heat\s+\.cal-mark\s*\{([^}]*)\}", css)
    assert m, "缺少 .cal-cell.heat .cal-mark 规则"
    bg = re.search(r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([\d.]+)\s*\)", m.group(1))
    assert bg and float(bg.group(1)) >= 0.9, \
        "热力格子上的角标底色不够实, 深色仍会透上来影响对比度"


@pytest.mark.parametrize("ink", ["#1F5C7A", "#A3144A", "#9C4A59"])
def test_heat_mode_mark_ink_is_readable_on_light_pill(ink):
    """三类角标的前景色叠到米白底 (#FFF8F0) 之后都要 >= 4.5:1。"""
    from tests.test_calendar_marks import _contrast as mark_contrast
    assert mark_contrast(ink, "#FFF8F0") >= 4.5, f"{ink} 在米白底上不足 AA"


def test_hidden_legend_actually_hides(styles):
    """作者样式的 display:flex 会压过浏览器对 [hidden] 的 display:none。"""
    assert re.search(r"\.cal-legend\[hidden\]\s*\{\s*display:\s*none", _strip(styles)), \
        "缺少 .cal-legend[hidden] { display: none }"


def test_heat_time_stays_visible_on_narrow_screens(styles):
    """窄屏收起用时标签, 但热力图里底色 + 数字才是完整读数, 必须保住数字。"""
    blocks = re.findall(r"@media \(max-width: 480px\) \{([\s\S]*?)\n\}", styles)
    assert any(".cal-cell.heat .cal-time" in b for b in blocks), \
        "窄屏应保留热力格子的用时数字"


# ====== 对比度 ======

def _srgb_lum(hex_color: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _srgb_lum(fg), _srgb_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("lvl", [1, 2, 3, 4, 5])
def test_heat_ink_contrast_meets_wcag_aa(styles, lvl):
    """格子里的日期与用时都是小字, 按正文标准要求 4.5:1。

    断言的是算出来的对比度, 不是"用了哪个变量" —— 将来有人把砖红调亮
    一点、却忘了换字色, 只有这条会拦住。
    """
    css = _strip(styles)
    m = re.search(rf"\.cal-cell\.heat-l{lvl}\s*\{{([^}}]*)\}}", css)
    body = m.group(1)
    bg = re.search(r"background:\s*(#[0-9A-Fa-f]{6})", body).group(1)
    ink = re.search(r"--heat-ink:\s*(#[0-9A-Fa-f]{6})", body).group(1)
    ratio = _contrast(ink, bg)
    assert ratio >= 4.5, f"第 {lvl} 档只有 {ratio:.2f}:1 ({ink} on {bg}), 不足 AA"


# ====== 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const mk = (id) => ({
  id: id, subject: '数学', task_name: '作业' + id, due_date: '2026-09-21',
  deadline_time: null, is_optional: false, status: 'done', est_minutes: 20,
  actual_minutes: null, task_type: 'other', confidence: 'high', source_span: 'x',
});
const ITEMS = [mk(1)];
const CAL = {
  year: 2026, month: 9, first_weekday: 1, days_in_month: 30,
  days: {
    // 五档全覆盖 + 一个 0 分 (不该上色)
    '2026-09-02': { total: 1, required: 1, required_done: 1, optional: 0, settled: 1, actual_total: 20 },
    '2026-09-07': { total: 1, required: 1, required_done: 1, optional: 0, settled: 1, actual_total: 60 },
    '2026-09-12': { total: 1, required: 1, required_done: 1, optional: 0, settled: 1, actual_total: 100 },
    '2026-09-17': { total: 1, required: 1, required_done: 0, optional: 0, settled: 0, actual_total: 145 },
    '2026-09-22': { total: 1, required: 1, required_done: 1, optional: 0, settled: 1, actual_total: 200 },
    '2026-09-27': { total: 1, required: 1, required_done: 0, optional: 0, settled: 0, actual_total: 0 },
  },
};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/#/list', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
let calendarHits = 0;
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/assignments/calendar')) { calendarHits++; return ok(CAL); }
  if (s.includes('/assignments?date=')) return ok({ items: ITEMS });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__go = () => switchTab("list");' +
        '\n;globalThis.__hl = heatLevel;');

const levelOf = (ds) => {
  const c = doc.querySelector('.cal-cell[data-date="' + ds + '"]');
  const m = c && c.className.match(/heat-l(\d)/);
  return m ? Number(m[1]) : 0;
};

(async () => {
  const out = {};
  w.__go();
  await new Promise(r => setTimeout(r, 400));

  out.hitsAfterLoad = calendarHits;
  // 默认模式: 完成情况 —— 格子带状态类, 不带热力类
  out.defaultClass = doc.querySelector('.cal-cell[data-date="2026-09-22"]').className;
  out.defaultHeat = levelOf('2026-09-22');
  out.completionLegendHidden = doc.getElementById('cal-legend-completion').hidden;
  out.heatLegendHidden = doc.getElementById('cal-legend-heat').hidden;
  out.filledCellHasBadge = !!doc.querySelector('.cal-cell[data-date="2026-09-22"] .cal-badge');

  // 切到热力图
  doc.querySelector('.cal-mode-btn[data-cal-mode="heat"]').dispatchEvent(
    new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 200));

  out.hitsAfterSwitch = calendarHits;
  out.levels = {
    '2026-09-02': levelOf('2026-09-02'),
    '2026-09-07': levelOf('2026-09-07'),
    '2026-09-12': levelOf('2026-09-12'),
    '2026-09-17': levelOf('2026-09-17'),
    '2026-09-22': levelOf('2026-09-22'),
    '2026-09-27': levelOf('2026-09-27'),
  };
  out.heatClass = doc.querySelector('.cal-cell[data-date="2026-09-22"]').className;
  out.completionLegendHidden2 = doc.getElementById('cal-legend-completion').hidden;
  out.heatLegendHidden2 = doc.getElementById('cal-legend-heat').hidden;
  out.heatAria = doc.querySelector('.cal-cell[data-date="2026-09-22"]').getAttribute('aria-label');
  out.heatTimeLabel = doc.querySelector('.cal-cell[data-date="2026-09-22"] .cal-time')?.textContent.trim();
  out.modeBtnPressed = doc.querySelector('.cal-mode-btn[data-cal-mode="heat"]').getAttribute('aria-pressed');

  // 边界值直接问函数本身, 免得只验界面
  out.hl60 = w.__hl(60);
  out.hl59 = w.__hl(59);
  out.hl0 = w.__hl(0);

  // 切回完成情况
  doc.querySelector('.cal-mode-btn[data-cal-mode="completion"]').dispatchEvent(
    new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 200));
  out.backHeat = levelOf('2026-09-22');
  out.backClass = doc.querySelector('.cal-cell[data-date="2026-09-22"]').className;
  out.hitsAtEnd = calendarHits;

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_cal_heatmap_harness.js"
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
def test_default_mode_is_completion(live):
    """默认仍是完成情况: 热力类一个都不该出现, 完成情况图例可见。"""
    assert live["defaultHeat"] == 0, "默认模式不该把格子涂成热力色"
    assert live["completionLegendHidden"] is False
    assert live["heatLegendHidden"] is True
    assert live["filledCellHasBadge"] is True, "完成情况模式仍要看得到 1/1 徽标"


@needs_jsdom
def test_heat_levels_follow_minutes(live):
    """五档全部按用时落位: 20/60/100/145/200 -> 1/2/3/4/5。"""
    assert live["levels"] == {
        "2026-09-02": 1,   # 20 分
        "2026-09-07": 2,   # 60 分 —— 边界归入 60-99
        "2026-09-12": 3,   # 100 分 —— 边界归入 100-139
        "2026-09-17": 4,   # 145 分
        "2026-09-22": 5,   # 200 分
        "2026-09-27": 0,   # 0 分不上色
    }, live["levels"]


@needs_jsdom
def test_heat_boundaries(live):
    assert live["hl59"] == 1
    assert live["hl60"] == 2
    assert live["hl0"] == 0


@needs_jsdom
def test_completion_classes_are_gone_in_heat_mode(live):
    """热力模式下不该残留 has/partial/all-done 状态类 —— 两层颜色语义叠加
    会让格子同时讲两个故事。"""
    cls = live["heatClass"]
    assert "heat-l5" in cls
    for state in ("has", "partial", "all-done", "optional-only"):
        assert state not in cls.split(), f"热力模式残留状态类 {state}: {cls}"


@needs_jsdom
def test_heat_mode_reads_minutes_out_loud(live):
    """颜色深浅读不出数字, aria-label 与格子上的 "200分" 要把它说清楚。"""
    assert live["heatTimeLabel"] == "200分"
    assert "200" in live["heatAria"]


@needs_jsdom
def test_legends_swap_with_mode(live):
    assert live["completionLegendHidden2"] is True
    assert live["heatLegendHidden2"] is False


@needs_jsdom
def test_mode_button_state_is_exposed(live):
    assert live["modeBtnPressed"] == "true"


@needs_jsdom
def test_switching_mode_does_not_refetch(live):
    """切模式只是重画 —— 两次切换都不该再打日历接口。

    首次加载的次数受路由初始化影响 (可能 >1), 这里只锁"切换前后不增加",
    因为真正的契约是切模式不额外请求。
    """
    assert live["hitsAfterSwitch"] == live["hitsAfterLoad"], \
        f"切到热力图时又请求了日历数据 ({live['hitsAfterLoad']} -> {live['hitsAfterSwitch']})"
    assert live["hitsAtEnd"] == live["hitsAfterLoad"], \
        f"切回完成情况时又请求了日历数据 ({live['hitsAfterLoad']} -> {live['hitsAtEnd']})"


@needs_jsdom
def test_switching_back_restores_completion(live):
    assert live["backHeat"] == 0, "切回完成情况后不该残留热力底色"
    assert "has" in live["backClass"] or "all-done" in live["backClass"], \
        f"切回后应是完成状态类, 实际 {live['backClass']}"
