"""日历点选日期后不应跳页 / 不应把焦点甩进输入框 (2026-09-29).

用户反馈: 在 #/list 点「9月28日」, 页面直接滚到底部。

真凶不是任何滚动代码 —— 全项目没有一处 scrollIntoView / scrollTo 参与
日历路径。真正的原因是 renderAssignment 里给"待补实际用时"的输入框
无条件加了 setTimeout(focus, 50):

    setTimeout(() => input.focus(), 50);

翻到 9月28日会渲染出 8 个这种输入框, 于是 8 个定时器一起抢焦点,
最后一个抢到就把页面滚到它那里 —— 页面跳到底部, 家长紧接着敲的键
还会落进某个作业的用时框。

这条自动聚焦本身是有意为之 (设计文档 §9 坑 2: 点完"完成"要能直接
敲用时), 错在它对"每一条待补用时的作业"都触发, 而只应在"家长刚点了
这一条的完成"时触发。现在由 _focusActualId 限定到具体那一条。

这里锁住三件事:
  1. input.focus() 必须被 focusActualFor === item.id 约束
  2. 只翻看某一天时, 待补用时的输入框一个都不许自动聚焦
  3. renderCalendar 重建月历后要把焦点还回被点的格子 (键盘可用)

2026-10-15 追加: 顺带在同一个 harness 里验每日总用时标签 (.cal-time) 的
渲染 —— 它与完成度徽标渲染在同一个循环里, 拆两个 harness 纯属重复搭建。
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
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


# ====== 静态检查 ======

def test_actual_input_autofocus_is_gated(app_js):
    """核心回归: 自动聚焦必须被"刚点完成的那一条"约束。

    无条件 setTimeout(focus) 会让翻看任意一天都跳页, 且抢走键盘焦点。

    2026-10-12: 输入框改为按需展开, 那个 if 里多了一行 expand() ——
    正因为它, "刚点完成"的那条会自动展开并聚焦, 而其他条仍是收起的。
    """
    m = re.search(
        r"if \(focusActualFor === item\.id\) \{(.*?)\n    \}",
        app_js, re.S,
    )
    assert m, "找不到 `if (focusActualFor === item.id)` 分支"
    body = m.group(1)
    assert "expand()" in body, "刚点完成的那条应自动展开用时输入"
    assert re.search(r"setTimeout\(\(\) => input\.focus\(\), 50\);", body), \
        "input.focus() 必须包在 `if (focusActualFor === item.id)` 里"


def test_pending_actual_input_is_collapsed_by_default(app_js):
    """2026-10-12 密度优化: 待补用时的条目默认只留「补用时」按钮。

    输入行必须先放进空容器, 由 expand() 搬进去 —— 而不是直接 append
    到 actions 里常驻显示。
    """
    assert "<div class=\"actual-fill\"></div>" in app_js, "缺少按需展开的容器"
    assert "fillRow.appendChild(inputWrap)" in app_js, "输入行应挂到容器里"
    assert 'class="status-btn fill-actual">补用时' in app_js, "缺少展开入口按钮"
    # 容器在未展开时必须为空, 不能一开始就 append 输入行
    assert "actions.appendChild(inputWrap)" not in app_js, \
        "输入行又变回常驻显示了, 密度优化会白做"


def test_only_one_actual_input_focus_timer(app_js):
    """全文件只应剩这一处 input.focus() 定时器, 不能有第二个无条件入口。"""
    timers = re.findall(r"setTimeout\(\(\) => input\.focus\(\), 50\);", app_js)
    assert len(timers) == 1, f"应恰好 1 处, 实际 {len(timers)} 处"


def test_all_render_assignment_calls_pass_focus_id(app_js):
    """3 处调用都必须把 _focusActualId 传下去, 漏一处就等于没修。

    2026-10-29: 原为 4 处 —— 看板与日历详情各写了一份选做分组的渲染。
    现在选做分组收敛到共享的 optionalGroupCard(), 两处都走它, 调用点
    从 4 处减到 3 处。断言跟着改, 但"每处都传 _focusActualId"这条
    契约不变 —— 减一处是因为合并, 不是因为漏传。
    """
    # 只取调用点: 函数定义的参数列表里含 "withStatus", 据此排除。
    calls = [c for c in re.findall(r"renderAssignment\(([^)]*)\)", app_js)
             if "withStatus" not in c]
    assert len(calls) == 3, f"应有 3 处调用, 实际 {len(calls)}: {calls}"
    missing = [c for c in calls if c.count(",") < 3]
    assert not missing, f"以下调用没传 focusActualFor: {missing}"


def test_refresh_calendar_detail_returns_promise(app_js):
    """refreshCalendarDetail 必须 return Promise。

    它要等 loadList 的异步渲染跑完, patchStatus 才能安全清空
    _focusActualId; 不返回的话标志会被提前清掉, 刚点完"完成"就聚焦不到。
    """
    m = re.search(r"function refreshCalendarDetail\(\) \{(.*?)\n\}", app_js, re.S)
    assert m, "找不到 refreshCalendarDetail"
    assert "return Promise.all" in m.group(1), \
        "refreshCalendarDetail 必须 return Promise.all, 否则 _focusActualId 会被提前清空"


def test_render_calendar_restores_focus(app_js):
    """重建月历后焦点还回原格子, 且用 preventScroll 避免再次跳页。"""
    assert "keepFocus" in app_js
    assert "preventScroll" in app_js


def test_patch_path_has_no_double_api_prefix(app_js):
    """api() 已会补 /api 前缀, 路径里再写一遍会变成 /api/api/..."""
    assert "/api/assignments/${item.id}/status" not in app_js


# ====== 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

// 8 条全部 done 且未填实耗 —— 复刻 9月28日: 满屏都是"待补用时"的输入框
const mk = (id) => ({
  id: id, subject: '数学', task_name: '作业' + id, due_date: '2026-09-28',
  deadline_time: null, is_optional: false, status: 'done', est_minutes: 20,
  actual_minutes: null, task_type: 'other', confidence: 'high', source_span: 'x',
});
const ITEMS = [1,2,3,4,5,6,7,8].map(mk);
const CAL = {
  year: 2026, month: 9, first_weekday: 1, days_in_month: 30,
  days: {
    '2026-09-21': { total: 4, required: 4, required_done: 4, optional: 0, settled: 4,
                    actual_total: 130 },
    '2026-09-28': { total: 8, required: 7, required_done: 7, optional: 1, settled: 7,
                    actual_total: 0 },
  },
};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/#/list', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/assignments/calendar')) return ok(CAL);
  if (s.includes('/assignments?date=')) return ok({ items: ITEMS });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__go = () => switchTab("list");');

(async () => {
  const out = {};
  w.__go();
  await new Promise(r => setTimeout(r, 400));
  const cell = doc.querySelector('.cal-cell[data-date="2026-09-28"]');
  out.cellExists = !!cell;
  // 2026-10-15: 每日总用时标签
  out.timeWithMinutes =
    doc.querySelector('.cal-cell[data-date="2026-09-21"] .cal-time')?.textContent.trim() || null;
  out.timeWithoutMinutes =
    doc.querySelector('.cal-cell[data-date="2026-09-28"] .cal-time')?.textContent.trim() || null;

  cell.focus();
  cell.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  // 等过 50ms 的 focus 定时器
  await new Promise(r => setTimeout(r, 300));

  out.totalInputs = doc.querySelectorAll('#list-content input.actual-input').length;
  out.focusedInputs = doc.querySelectorAll('#list-content input.actual-input:focus').length;
  // 2026-10-12: 输入框按需展开, 收起状态下入口按钮仍在
  out.fillButtons = doc.querySelectorAll('#list-content .fill-actual').length;
  const a = doc.activeElement;
  out.activeTag = a ? a.tagName : null;
  out.activeClass = a ? (a.className || '') : null;
  out.focusStaysOnCell = !!(a && a.classList && a.classList.contains('cal-cell'));
  out.detailTitle = doc.getElementById('list-detail-title').textContent.trim();
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_cal_focus_harness.js"
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
def test_browsing_a_day_does_not_autofocus_any_time_input(live):
    """只翻看历史日期时, 满屏的"填实际用时"输入框都不许自动聚焦。

    2026-10-12: 输入框改为按需展开, 翻看日期时一个都不该渲染出来 ——
    这比"渲染了但没聚焦"更彻底, 也是这条回归真正想要的结果。
    """
    assert live["cellExists"] is True
    assert live["totalInputs"] == 0, (
        f"待补用时的输入框不该默认渲染, 实际 {live['totalInputs']} 个")
    assert live["focusedInputs"] == 0, (
        f"翻看日期时不该有任何输入框自动聚焦, 实际 {live['focusedInputs']} 个")
    # 入口必须还在: 收起不等于消失
    assert live["fillButtons"] > 0, "收起后仍需留「补用时」入口"


@needs_jsdom
def test_focus_stays_on_the_clicked_day_cell(live):
    assert live["focusStaysOnCell"] is True, (
        f"焦点应留在被点的日期格子上, 实际 {live['activeTag']}.{live['activeClass']}")


@needs_jsdom
def test_day_detail_still_loads(live):
    """修焦点不能修坏详情加载。"""
    assert "2026-09-28" in live["detailTitle"]


@needs_jsdom
def test_day_cell_shows_aggregated_minutes(live):
    """格子上要显出当天总用时, 数字与看板概览卡那个一致。

    不加后缀的裸数字在 7 列格子里会被误认成日期的一部分。
    """
    assert live["timeWithMinutes"] == "130分", (
        f"应显示 130分, 实际 {live['timeWithMinutes']!r}")


@needs_jsdom
def test_zero_minutes_cell_has_no_time_label(live):
    """还没做完的那天不挂 "0分" —— 纯噪声, 还会挤掉日期数字。"""
    assert live["timeWithoutMinutes"] is None, (
        f"不该显示时间标签, 实际 {live['timeWithoutMinutes']!r}")


# ====== 每日用时标签的可读性 (2026-10-16 截图复核) ======
# 家长反馈"数字太小、颜色看不清"。实测确认: 原用 --text-3(#BFA894) 在
# 四种格子底色上只有 1.80~2.00:1, 远低于 WCAG AA 的 4.5:1。
# 改用各状态自带的 --cal-*-ink 后升到 5.61~7.55:1。

CAL_TIME_STATES = [
    # (状态类, 格子底色, 该状态的 ink 变量)
    ("has", "#FFE7D0", "--cal-has-ink"),
    ("partial", "#FFF0BE", "--cal-part-ink"),
    ("all-done", "#D8F0D2", "--cal-done-ink"),
    ("optional-only", "#E8E2F7", "--cal-opt-ink"),
]


def _srgb_lum(hex_color: str) -> float:
    """WCAG 相对亮度。"""
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _srgb_lum(fg), _srgb_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("state,bg,ink_var", CAL_TIME_STATES)
def test_cal_time_contrast_meets_wcag_aa(styles, state, bg, ink_var):
    """每日用时标签在每种格子底色上都必须达到 AA (4.5:1)。

    回归: 原先统一用 --text-3, 实测 1.80~2.00:1 —— 语法上"有颜色",
    视觉上等于没有。断言的是**具体对比度**, 而不是"用了哪个变量":
    将来有人把 ink 调浅一点, 只有算出来的数字才能拦住。
    """
    root = re.search(r":root\s*\{([^}]*)\}", styles, re.S).group(1)
    ink = re.search(rf"{re.escape(ink_var)}:\s*(#[0-9A-Fa-f]{{6}})", root).group(1)
    ratio = _contrast(ink, bg)
    assert ratio >= 4.5, (
        f"{state} 状态下 --cal-time 只有 {ratio:.2f}:1 ({ink} on {bg}), 不足 AA 4.5:1")


def test_cal_time_uses_state_ink_not_muted_text(styles):
    """四种状态都要各自覆盖前景色, 不能只靠基础规则的兜底色。"""
    css = _strip(styles)
    for state, _, ink_var in CAL_TIME_STATES:
        m = re.search(
            rf"\.cal-cell\.{re.escape(state)}\s+\.cal-time\s*\{{([^}}]*)\}}", css)
        assert m, f"{state} 状态缺少 .cal-time 前景色规则"
        assert ink_var in m.group(1), \
            f"{state} 的 .cal-time 应取 {ink_var}, 实际: {m.group(1).strip()}"


def test_cal_time_font_is_large_enough(styles):
    """字号不能太小。12px 是"能一眼扫到"的下限。

    用户明确要求"数字大一些"; 回到 11px 会让整月的分钟数变成需要
    凑近才能读的杂讯, 等于这个功能白做。
    """
    m = re.search(r"\.cal-time\s*\{([^}]*)\}", _strip(styles))
    assert m, "缺少 .cal-time 规则"
    size = float(re.search(r"font-size:\s*(\d+)px", m.group(1)).group(1))
    assert size >= 12, f".cal-time 字号 {size}px 偏小, 应 >= 12px"


def _strip(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def test_cal_time_still_hidden_on_narrow_screens(styles):
    """窄屏收起用时标签的规则不能被这次改动碰掉。

    7 列格子在 375px 下每格仅约 46px, 塞不下第三行数字。

    文件里有多个 @media (max-width: 480px) 块, 必须**全部**扫描 ——
    只查第一个会漏掉真正管日历的那个, 然后测试却"通过"了。
    """
    blocks = re.findall(r"@media \(max-width: 480px\) \{([\s\S]*?)\n\}", styles)
    assert blocks, "缺少 max-width: 480px 媒体查询"
    assert any(".cal-time" in b for b in blocks), \
        "没有任何窄屏规则收起 .cal-time, 375px 下会挤爆格子"
    # 徽标与用时标签要一起收起: 两者都是格子里第三行的辅助信息
    assert any(".cal-badge, .cal-time" in b for b in blocks), \
        "窄屏应同时收起 .cal-badge 与 .cal-time"
