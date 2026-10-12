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
    """4 处调用都必须把 _focusActualId 传下去, 漏一处就等于没修。"""
    # 只取调用点: 函数定义的参数列表里含 "withStatus", 据此排除。
    calls = [c for c in re.findall(r"renderAssignment\(([^)]*)\)", app_js)
             if "withStatus" not in c]
    assert len(calls) == 4, f"应有 4 处调用, 实际 {len(calls)}: {calls}"
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
    '2026-09-21': { total: 4, required: 4, required_done: 4, optional: 0, settled: 4 },
    '2026-09-28': { total: 8, required: 7, required_done: 7, optional: 1, settled: 7 },
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
