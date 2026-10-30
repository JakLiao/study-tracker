"""页头中文化 + flatpickr 日历头部布局测试 (2026-09-28).

用户截图批注了三件事:
  1. 左上角 "study-tracker v1" 改中文名, 去掉版本号
  2. 右上角 "DB ok · 模型 MiniMax-M3 · AI ✓ 可用" 改为当天日期+星期
  3. flatpickr 日历的 "九月 2026" 压住了星期表头

第 3 条是本文件的主要价值: 它曾经是一处真实的布局 bug ——
.flatpickr-current-month 是 absolute + height:34px, 而 .flatpickr-months
是纯 flex 容器没有高度; 主题里给 months 加了 padding-top 又给
current-month 加了 top, 等于整体下移 14px 而容器高度没涨,
下方的星期表头就被盖住了。这类 bug 只有把布局契约锁住才不会复发。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "app" / "static" / "vendor"

node = shutil.which("node")
jsdom_available = False
if node:
    try:
        subprocess.run([node, "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False

pytestmark = pytest.mark.skipif(
    not jsdom_available, reason="需要 node 与 jsdom"
)


# ---------- 静态检查 (不需要 jsdom, 但同文件便于对照) ----------

@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def theme_css() -> str:
    return (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8")


def _rule(css: str, sel: str) -> str:
    """取某条规则的内容。

    必须允许行首空白: 主题里 .flatpickr-current-month 是逗号分组的
    续行(前面带缩进)。也不能命中注释 —— 注释里出现的类名后面跟的是
    空格加文字, 而不是 " {"。
    """
    esc = re.escape(sel)
    m = re.search(r"^[ \t]*" + esc + r" \{", css, re.M)
    if not m:
        return ""
    end = css.find("\n}", m.start())
    return css[m.start():end] if end != -1 else ""


# ====== 1. 左上角中文名 ======

def test_logo_is_chinese_name(index_html):
    m = re.search(r"<h1>(.*?)</h1>", index_html, re.S)
    assert m, "缺少 header h1"
    text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    assert text == "作业小助手", text


def test_logo_has_no_version(index_html):
    h1 = re.search(r"<h1>(.*?)</h1>", index_html, re.S).group(1)
    assert "v1" not in h1.lower()
    # 任何数字都可疑 —— 版本号是唯一可能的数字来源
    assert not re.search(r"\d", h1), h1


def test_logo_has_no_english_codename(index_html):
    h1 = re.search(r"<h1>(.*?)</h1>", index_html, re.S).group(1)
    assert "study-tracker" not in h1.lower()
    assert "tracker" not in h1.lower()


def test_title_is_chinese(index_html):
    title = re.search(r"<title>(.*?)</title>", index_html, re.S).group(1)
    assert "作业小助手" in title
    assert "study-tracker" not in title.lower()


def test_footer_does_not_leak_api_path(index_html):
    """页脚原本显示 API 路径, 对家长无意义且暴露技术细节。"""
    foot = re.search(r"<footer>(.*?)</footer>", index_html, re.S).group(1)
    assert "/api/health" not in foot


def _tab_label(btn_inner: str) -> str:
    """取出 Tab 按钮里的可见文字。

    2026-10-12 起按钮里多了 <svg> 图标与 <span> 包裹, 整体比对会失败;
    而这里要守的恰恰是"文字没被改坏", 所以必须先把标签剥掉, 只留汉字。
    """
    return re.sub(r"<svg.*?</svg>|<[^>]+>", "", btn_inner, flags=re.S).strip()


# ====== 1b. Tab 名称 (2026-09-28: 列表 -> 日历) ======

def test_list_tab_renamed_to_calendar(index_html):
    m = re.search(r'<button[^>]*data-tab="list"[^>]*>(.*?)</button>', index_html)
    assert m, "缺少日历 Tab"
    assert _tab_label(m.group(1)) == "日历", m.group(1)


def test_tab_labels_all_chinese(index_html):
    """五个 Tab 都不该出现英文代号或版本号。"""
    labels = [_tab_label(x) for x in
              re.findall(r'<button[^>]*data-tab="[^"]+"[^>]*>(.*?)</button>', index_html)]
    # 「解析」(entry) 于 2026-09-28 改名为「登记」: Tab 是给家长看的,
    # "解析" 描述的是内部机制(AI 解析原文), 不是这一步要做的事 ——
    # 家长做的事是"把今天的作业登记进来"。
    assert labels == ["看板", "登记", "日历", "复盘", "设置"], labels
    for l in labels:
        assert not re.search(r"[A-Za-z]", l), l


# ====== 1c. Tab 图标 (2026-10-12) ======

def test_every_tab_has_an_icon(index_html):
    """五个 Tab 各配一个图形: 低龄用户先认形再认字。"""
    for tab, sym in [("board", "i-tab-board"), ("entry", "i-tab-entry"),
                     ("list", "i-tab-calendar"), ("review", "i-tab-review"),
                     ("settings", "i-tab-settings")]:
        m = re.search(r'<button[^>]*data-tab="%s"[^>]*>(.*?)</button>' % tab, index_html)
        assert m, f"缺少 {tab} Tab"
        assert f'href="#{sym}"' in m.group(1), f"{tab} 缺少图标 {sym}"
        assert '<symbol id="%s"' % sym in index_html, f"{sym} 未在 svg-defs 中定义"


def test_tab_icons_inherit_current_color(index_html):
    """图标描边必须是 currentColor: 选中态只改 button 的 color,
    图标才能跟着变色, 不必为每个 Tab 各画一份彩色版。"""
    syms = re.findall(r'<symbol id="i-tab-[^"]+".*?</symbol>', index_html, re.S)
    assert len(syms) == 5, len(syms)
    for s in syms:
        assert "currentColor" in s, s[:60]
        assert "#4A3728" not in s and "stroke=\"#" not in s, "图标不应写死颜色"


def test_tab_icons_hidden_from_a11y_tree(index_html):
    """图标纯装饰, 读屏只该念出文字, 不能念两遍。"""
    assert len(re.findall(r'<svg class="tab-ico" aria-hidden="true">', index_html)) == 5
    # 文字必须包在 span 里, 否则图标与汉字会挤在一行
    assert len(re.findall(r'<span>[^<]+</span>', index_html)) >= 5


def test_view_id_unchanged(index_html):
    """用户可见名改了, 但内部 id 必须保持 view-list。

    switchTab('list')、CSS 与测试都按这个 id 定位; 改它牵连多处而无收益。
    """
    assert 'id="view-list"' in index_html
    assert 'data-tab="list"' in index_html


def test_no_stale_list_wording_in_ui(index_html, app_js):
    """界面上不应再出现旧的「列表」叫法。

    只查用户可见部分: 内部 id(view-list / data-tab="list")保留不动。
    """
    for src in (index_html, app_js):
        # 去掉 HTML 注释与 JS 行注释后再查
        s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        s = "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))
        # 保留中文引号内的用法, 只找裸的「列表」/列表页/列表」
        assert "「列表」" not in s, "空状态引导文案仍指向旧 Tab 名"
        assert "列表页" not in s, "注释或文案仍称列表页"


# ====== 2. 右上角日期 + 星期 ======

def test_health_bar_no_longer_shows_dev_info(app_js):
    code = "\n".join(l for l in app_js.splitlines() if not l.strip().startswith("//"))
    assert "DB ok" not in code, "不应再显示 DB 状态"
    assert "模型 ${" not in code, "不应再显示模型名"


def test_render_today_exists(app_js):
    assert "function renderToday()" in app_js
    assert "星期" in app_js


def test_health_bar_uses_today_label(app_js):
    assert 'class="today-label"' in app_js
    assert "health-warn" in app_js


def test_today_reuses_todaystr(app_js):
    """不要自己再拼一遍日期。

    todayStr() 已经处理了 UTC+8 凌晨会退到前一天的问题,
    两套实现迟早漂移。
    """
    block = app_js.split("function renderToday()")[1].split("\n}")[0]
    assert "todayStr()" in block, "renderToday 应复用 todayStr()"
    assert "getFullYear" not in block, "不应重复实现本地日期换算"


def test_date_shown_even_when_health_fails(app_js):
    """接口挂了也要显示"今天是几号" —— 跟后端通不通无关。"""
    assert "连接异常" in app_js
    # 必须定位到 loadHealth 内部的 catch: 文件里还有多处 catch,
    # 取 [-1] 会拿到最后一个(导出失败那个), 断言就失去意义了。
    fn = app_js.split("async function loadHealth()")[1]
    catch = fn.split("} catch (e) {")[1]
    assert "today-label" in catch, "catch 分支也必须渲染日期"
    assert "连接异常" in catch
    assert "e.message" not in catch, "不应把原始错误抛给家长"


def test_ai_warning_only_when_unavailable(app_js):
    assert "AI 未配置" in app_js
    # 可用时不该出现警告文案拼接
    assert "aiOk ? ''" in app_js


def test_health_bar_styles_present():
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    assert "#health-bar .today-label" in css
    assert "#health-bar .health-warn" in css
    assert "font-variant-numeric: tabular-nums" in css


def test_health_bar_color_stable(app_js):
    """AI 状态变化不应让日期颜色闪变。"""
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    ok_true = re.search(r"#health-bar\.ok-true[^{]*\{([^}]*)\}", css)
    ok_false = re.search(r"#health-bar\.ok-false[^{]*\{([^}]*)\}", css)
    if ok_true and ok_false:
        assert ok_true.group(1).strip() == ok_false.group(1).strip(), \
            "两种状态的日期色应一致, 否则 AI 恢复时日期会闪一下"


# ====== 3. flatpickr 日历头部布局 ======

def test_months_has_explicit_height(theme_css):
    """.flatpickr-months 是纯 flex 容器, 库没给高度。

    不显式给, 上面的 absolute 标题就会压住下面的星期表头。
    """
    block = _rule(theme_css, ".flatpickr-months")
    m = re.search(r"height:\s*(\d+)px", block)
    assert m, "months 必须显式声明 height"
    assert int(m.group(1)) >= 34, "至少要托住库给 current-month 的 34px 槽位"


def test_current_month_not_offset_down(theme_css):
    """回归: 曾经给了 top:6px, 叠加 months 的 padding 后整体下移 14px,
    容器高度却没涨, 星期表头被盖住。"""
    block = _rule(theme_css, ".flatpickr-current-month")
    assert "top" in block, "应显式声明 top"
    m = re.search(r"(?:^|\s)top:\s*(-?\d+)(px)?\s*;", block)
    assert m, "top 声明格式异常"
    assert int(m.group(1)) == 0, f"top 应为 0 而非 {m.group(1)}"


def test_current_month_has_height(theme_css):
    block = _rule(theme_css, ".flatpickr-current-month")
    assert re.search(r"height:\s*34px", block), "应与库约定的 34px 槽位一致"


def test_year_input_no_underline(theme_css):
    """库默认给年份输入加下划线, 和月份并排显得杂乱。"""
    block = _rule(theme_css, ".flatpickr-current-month input.cur-year")
    assert "border-bottom: none" in block


def test_year_input_fixed_width(theme_css):
    """不定宽的年份输入在切月时会左右跳动。"""
    block = _rule(theme_css, ".flatpickr-current-month input.cur-year")
    assert re.search(r"width:\s*[\d.]+em", block)


def test_calendar_z_index(theme_css):
    """弹层会盖到录入区的 textarea, 必须显式抬层级。"""
    assert re.search(r"\.flatpickr-calendar \{[\s\S]{0,500}?z-index:\s*\d+", theme_css)


def test_theme_note_allows_layout_fixes():
    """文件头注释原写"不动布局", 但我们确实修过布局。

    注释与代码不符会误导下一个改这里的人, 故锁住这条说明。
    """
    head = (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8").split("*/")[0]
    assert "不动布局" not in head
    assert "DOM 结构" in head, "仍应保留不改 DOM 结构的约束"


# ====== 4. 运行时行为 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, jsPath, fpPath, zhPath] = process.argv.slice(2);
const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
let health = { ai_model: 'MiniMax-M3', ai_reachable: true };
let failHealth = false;
w.fetch = async (u) => {
  if (String(u).includes('/health')) {
    if (failHealth) throw new Error('boom');
    return { ok: true, status: 200, text: async () => JSON.stringify(health) };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify({}) };
};
w.confirm = () => true;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(jsPath, 'utf8') + '\n;globalThis.__h = () => loadHealth();');

(async () => {
  const doc = w.document;
  const out = {};
  const now = new Date();
  const week = '日一二三四五六'[now.getDay()];
  out.week = week;
  out.h1 = doc.querySelector('header h1').textContent.trim();
  out.title = doc.title;
  out.footer = doc.querySelector('footer').textContent.trim();

  await w.__h();
  await new Promise(r => setTimeout(r, 120));
  const bar = doc.getElementById('health-bar');
  out.okText = bar.textContent;
  out.hasTodayLabel = !!bar.querySelector('.today-label');

  health = { ai_model: '-', ai_reachable: false };
  await w.__h();
  await new Promise(r => setTimeout(r, 120));
  out.aiDownText = bar.textContent;

  health = { ai_model: 'x', ai_reachable: true };
  failHealth = true;
  await w.__h();
  await new Promise(r => setTimeout(r, 120));
  out.errText = bar.textContent;

  // 日历结构
  const entry = doc.getElementById('entry-date');
  out.hasInstance = !!entry._fpInstance;
  if (out.hasInstance) {
    entry._fpInstance.open();
    await new Promise(r => setTimeout(r, 80));
    const cal = doc.querySelector('.flatpickr-calendar');
    out.calOpen = !!(cal && cal.classList.contains('open'));
    out.weekdays = [...cal.querySelectorAll('.flatpickr-weekday')].map(x => x.textContent.trim());
    out.hasMonths = !!cal.querySelector('.flatpickr-months');
    out.hasCurrentMonth = !!cal.querySelector('.flatpickr-current-month');
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    import json
    harness = ROOT / "tmp" / "_hdr_harness.js"
    harness.parent.mkdir(exist_ok=True)
    harness.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(harness),
             str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(VENDOR / "flatpickr.min.js"),
             str(VENDOR / "flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90,
        )
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1200:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if harness.exists():
            harness.unlink()


def test_live_logo(live):
    assert live["h1"] == "作业小助手"
    assert "study-tracker" not in live["title"].lower()


def test_live_footer(live):
    assert "/api/health" not in live["footer"]


def test_live_health_shows_date_and_weekday(live):
    assert f"星期{live['week']}" in live["okText"]
    assert live["hasTodayLabel"] is True
    assert "DB ok" not in live["okText"]
    assert "MiniMax" not in live["okText"]


def test_live_health_silent_when_ai_ok(live):
    assert "AI 未配置" not in live["okText"]


def test_live_health_warns_when_ai_down(live):
    assert f"星期{live['week']}" in live["aiDownText"]
    assert "AI 未配置" in live["aiDownText"]


def test_live_health_error_keeps_date(live):
    assert f"星期{live['week']}" in live["errText"]
    assert "连接异常" in live["errText"]
    assert "boom" not in live["errText"], "不应把原始错误抛给用户"


def test_live_calendar_structure(live):
    assert live["hasInstance"] is True
    assert live["calOpen"] is True
    assert live["hasMonths"] is True
    assert live["hasCurrentMonth"] is True
    assert live["weekdays"] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
