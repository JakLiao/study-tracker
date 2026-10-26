"""月历格子的特殊日角标 (2026-10-26).

用户要求: "日历上标注下一些特殊的日子和周末, 小文字角标就行。"

这个角标解决的是一个具体的困惑: 假期那几天格子里是空的, 家长分不清
是放假了、还是自己漏登记了作业。角标挂在格子右上角, 小字, 不抢日期
数字 —— 月历上家长扫的是"几号"。

三类:
  holiday   法定假期 (国庆/中秋...), 红
  makeup    调休上班日 (周末补班, 这天要上学), 蓝
  weekend   普通周末, 玫瑰色, 最弱的一档

接口契约在 app/services/assignment_service.py 的 calendar_summary
("special" 字段); 渲染在 app/static/app.js 的 renderCalendar。
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


def _strip(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


# ====== 静态检查: 接线 ======

def test_calendar_reads_special_map(app_js):
    """renderCalendar 必须从接口返回的 special 表取角标数据。

    角标内容来自后端校历 (法定假期每年重排), 前端不该自己再维护一份 ——
    两份表必然会漂移。
    """
    block = re.search(r"function renderCalendar\(\) \{(.*?)\n\}", app_js, re.S)
    assert block, "找不到 renderCalendar"
    assert "r.special" in block.group(1), "renderCalendar 没读接口返回的 special"


def test_calendar_renders_mark_element(app_js):
    block = re.search(r"function renderCalendar\(\) \{(.*?)\n\}", app_js, re.S)
    body = block.group(1)
    assert 'class="cal-mark' in body, "缺少 .cal-mark 角标元素"
    # 三类角标共用一个类 + kind 修饰类, 不能各写一个标签
    assert "cal-mark-${" in body, "角标应按 kind 拼修饰类, 不该写死三种标签"


def test_mark_is_escaped(app_js):
    """角标文字进了 HTML, 必须转义 —— 节日名来自校历表, 但仍是外部数据。"""
    block = re.search(r"function renderCalendar\(\) \{(.*?)\n\}", app_js, re.S)
    body = block.group(1)
    assert "escapeAttr(sp.label)" in body and "escapeAttr(sp.name)" in body


def test_mark_does_not_break_focus_restore(app_js):
    """角标是格子里的子元素, 不能把 focus 还原那段代码挤变形。"""
    block = re.search(r"function renderCalendar\(\) \{(.*?)\n\}", app_js, re.S)
    body = block.group(1)
    assert "keepFocus" in body and "preventScroll" in body


# ====== 静态检查: 样式 ======

def test_mark_is_corner_badge(styles):
    """角标必须绝对定位在角落 —— 一旦占行, 格子里就多出一行, 月历变高。"""
    m = re.search(r"\.cal-mark\s*\{([^}]*)\}", _strip(styles))
    assert m, "缺少 .cal-mark 规则"
    body = m.group(1)
    assert "position: absolute" in body, "角标必须绝对定位, 否则会撑高格子"
    assert "top:" in body and "right:" in body, "角标应贴在右上角"


def test_mark_font_is_small(styles):
    """角标是辅助信息, 字号必须小于日期数字 —— 数字才是主角。"""
    m = re.search(r"\.cal-mark\s*\{([^}]*)\}", _strip(styles))
    size = float(re.search(r"font-size:\s*(\d+)px", m.group(1)).group(1))
    assert size <= 11, f"角标 {size}px 偏大, 会跟日期数字抢视线"


def test_every_kind_has_its_own_color(styles):
    """三类角标必须各有配色 —— 家长靠颜色一眼分出"放假/补班/周末"。"""
    css = _strip(styles)
    for kind in ("holiday", "makeup", "weekend"):
        assert re.search(rf"\.cal-mark-{kind}\s*\{{[^}}]*color:", css), \
            f"{kind} 角标缺前景色"


def test_variant_class_matches_the_rendered_class(app_js, styles):
    """CSS 里的类名必须与渲染出来的类名逐字一致。

    实测踩过的坑: HTML 渲染成 class="cal-mark cal-mark-weekend", CSS 却写成
    .cal-mark.weekend —— 那条选择器要求元素同时带 cal-mark 与 weekend 两个
    类, 而元素只有一个 "cal-mark-weekend" 这个类, 于是规则永远不生效:
    角标只剩下一行灰字, 颜色和窄屏收起全部失效, 界面上却看不出任何异常。

    所以这里直接把"渲染用的类名"和"CSS 里的选择器"摆在一起比对。
    """
    css = _strip(styles)
    m = re.search(r"cal-mark-\$\{[^}]*\}", app_js)
    assert m, "app.js 里找不到角标的类名模板"
    for kind in ("holiday", "makeup", "weekend"):
        cls = f"cal-mark-{kind}"
        assert re.search(rf"\.{re.escape(cls)}\s*\{{", css), \
            f"styles.css 里没有 {cls} 规则 (或写成了别的类名, 规则不会命中)"


def test_weekend_mark_hidden_on_narrow_screens(styles):
    """窄屏收起"周末"角标, 但假期/调休必须留着。

    7 列格子里 10px 的角标已经很小, 周末角标信息量最低 (数字本身是淡红的),
    假期/调休才是家长真正缺的信息 —— 少标一条等于把"放假"说成了"漏登记"。
    """
    blocks = re.findall(r"@media \(max-width: 480px\) \{([\s\S]*?)\n\}", styles)
    assert blocks, "缺少 max-width: 480px 媒体查询"
    assert any(".cal-mark-weekend" in b for b in blocks), \
        "窄屏应收起 .cal-mark-weekend"
    assert not any(".cal-mark" in b and "holiday" in b for b in blocks), \
        "窄屏不能收起假期角标"


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


def _blend(fg: str, alpha: float, bg: str = "#FFFFFF") -> str:
    """把半透明底色叠到白底上, 算出实际呈现的颜色。"""
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    mix = [round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3)]
    return "#%02X%02X%02X" % tuple(mix)


MARK_RULES = [
    # (kind, 前景色, 背景色 + 透明度)
    ("holiday", "#A3144A", "#C2185B", 0.10),
    ("makeup", "#1F5C7A", "#2E7A9E", 0.12),
    ("weekend", "#9C4A59", "#C77B8A", 0.14),
]


@pytest.mark.parametrize("kind,ink,bg,alpha", MARK_RULES)
def test_mark_contrast_meets_wcag_aa(styles, kind, ink, bg, alpha):
    """角标是 10px 小字, 按正文标准要求 4.5:1。

    底色是半透明, 所以对比度要按"叠到白底之后"的实际颜色算, 而不是
    直接拿 rgba 的原始色 —— 那是算不准的。
    """
    css = _strip(styles)
    m = re.search(rf"\.cal-mark-{kind}\s*\{{([^}}]*)\}}", css)
    assert m, f"缺少 .cal-mark-{kind} 规则"
    body = m.group(1)
    assert ink in body, f"{kind} 角标前景色应为 {ink}, 实际: {body.strip()}"
    bgm = re.search(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", body)
    assert bgm, f"{kind} 角标缺半透明底色"
    r, g, b = (int(bgm.group(i)) for i in (1, 2, 3))
    a = float(bgm.group(4))
    real_bg = _blend("#%02X%02X%02X" % (r, g, b), a)
    ratio = _contrast(ink, real_bg)
    assert ratio >= 4.5, (
        f"{kind} 角标只有 {ratio:.2f}:1 ({ink} on {real_bg}), 不足 AA 4.5:1")


# ====== 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

// 2026 年 10 月: 1 号是周四 (first_weekday = 3)
const CAL = {
  year: 2026, month: 10, first_weekday: 3, days_in_month: 31,
  days: {
    '2026-10-13': { total: 3, required: 2, required_done: 1, optional: 1,
                    settled: 1, actual_total: 0 },
  },
  special: {
    '2026-10-01': { kind: 'holiday', label: '国庆', name: '国庆' },
    '2026-10-03': { kind: 'holiday', label: '国庆', name: '国庆' },
    '2026-10-10': { kind: 'makeup', label: '调休', name: '国庆调休' },
    '2026-10-11': { kind: 'weekend', label: '周末', name: '周末' },
  },
};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/#/list', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
w.fetch = async (u) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/assignments/calendar')) return ok(CAL);
  if (s.includes('/assignments?date=')) return ok({ items: [] });
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

  const markOf = (ds) => {
    const c = doc.querySelector('.cal-cell[data-date="' + ds + '"]');
    if (!c) return null;
    const m = c.querySelector('.cal-mark');
    if (!m) return null;
    return {
      text: m.textContent.trim(),
      cls: m.className,
      title: m.getAttribute('title'),
      aria: c.getAttribute('aria-label'),
    };
  };

  out.holiday = markOf('2026-10-01');
  out.holidayOnSaturday = markOf('2026-10-03');
  out.makeup = markOf('2026-10-10');
  out.weekend = markOf('2026-10-11');
  out.weekday = markOf('2026-10-13');
  out.markCount = doc.querySelectorAll('.cal-mark').length;
  out.cellCount = doc.querySelectorAll('.cal-cell[data-date]').length;
  // 角标不能挡住格子里的日期数字
  out.dayText = doc.querySelector('.cal-cell[data-date="2026-10-01"] .cal-day').textContent.trim();
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_cal_marks_harness.js"
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
def test_holiday_cell_shows_holiday_name(live):
    """假期角标写节日名 —— 家长要能直接看出"国庆", 不是"放假"。"""
    m = live["holiday"]
    assert m is not None, "10/1 没有角标"
    assert m["text"] == "国庆", m
    assert "cal-mark-holiday" in m["cls"].split(), m


@needs_jsdom
def test_holiday_on_saturday_is_not_just_weekend(live):
    """10/3 是周六也在国庆里 —— 角标必须是假期, 不能是"周末"。"""
    m = live["holidayOnSaturday"]
    assert m is not None
    assert m["text"] == "国庆", m
    assert "cal-mark-holiday" in m["cls"].split(), m


@needs_jsdom
def test_makeup_cell_shows_makeup_with_full_name(live):
    """调休角标只写"调休", 完整节日名 ("国庆调休") 留给悬停提示。"""
    m = live["makeup"]
    assert m is not None, "10/10 没有角标"
    assert m["text"] == "调休", m
    assert m["title"] == "国庆调休", m
    assert "cal-mark-makeup" in m["cls"].split(), m


@needs_jsdom
def test_weekend_cell_shows_weekend(live):
    m = live["weekend"]
    assert m is not None, "10/11 没有角标"
    assert m["text"] == "周末", m
    assert "cal-mark-weekend" in m["cls"].split(), m


@needs_jsdom
def test_plain_weekday_has_no_mark(live):
    """平日不挂角标 —— 满屏小字就等于没有重点。"""
    assert live["weekday"] is None, live["weekday"]


@needs_jsdom
def test_only_special_days_get_marks(live):
    assert live["markCount"] == 4, (
        f"应只有 4 个角标 (10/1、10/3、10/10、10/11), 实际 {live['markCount']}")
    assert live["cellCount"] == 31, f"应渲染 31 个格子, 实际 {live['cellCount']}"


@needs_jsdom
def test_mark_does_not_cover_day_number(live):
    """角标是独立元素, 日期数字必须原样还在 (角标只补一句说明)。"""
    assert live["dayText"] == "1", live["dayText"]


@needs_jsdom
def test_special_name_in_aria_label(live):
    """读屏用户也要知道那天是假期 —— 角标是纯视觉的, aria 负责补上。"""
    assert "国庆调休" in live["makeup"]["aria"], live["makeup"]["aria"]
