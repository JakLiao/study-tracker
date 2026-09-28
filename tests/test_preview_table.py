"""登记页预览表紧凑化测试 (2026-09-28).

用户截图批注五处:
  1. Tab「解析」改名为「登记」
  2. 表格右侧多出一截没利用(左边空间也没用上)
  3. 「置信」列文字换行, 竖着排三行
  4. 「原文」列影响行高
  5. 删除操作是红色, 危险

根因: 预览表 10 列挤在 main 的 720px 里, 浏览器按内容分配宽度,
长文本把宽度吃光, 末尾几列只能折行, 行高暴涨。

这些用例锁住:
  1. 固定列宽 + 不换行, 行高稳定
  2. 置信度改用单字符图标
  3. collectRows 仍能正确读回置信度与原文 —— 这是最容易踩的坑,
     标记 class 从 confidence-pill 换成 conf-dot, 读取处必须同步,
     否则低置信度会静默退化成 medium, "需核对"提醒在入库时丢掉
  4. 删行按钮为中性色
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


@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


def _code(src: str) -> str:
    s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))


def _block(css: str, sel: str) -> str:
    css_no_comment = re.sub(r"/\*[\s\S]*?\*/", "", css)
    m = re.search(r"^[ \t]*" + re.escape(sel) + r" \{", css_no_comment, re.M)
    if not m:
        return ""
    end = css_no_comment.find("\n}", m.start())
    return css_no_comment[m.start():end] if end != -1 else ""


# ====== 1. Tab 改名 ======

def test_entry_tab_renamed(index_html):
    """「解析」描述的是 AI 侧机制, 家长做的事是"把作业登记进来"。"""
    m = re.search(r'<button[^>]*data-tab="entry"[^>]*>(.*?)</button>', index_html)
    assert m, "缺少登记 Tab"
    assert m.group(1).strip() == "登记", m.group(1)


def test_internal_tab_id_unchanged(index_html):
    """改名只动可见文案, data-tab / view id 必须不动。"""
    assert 'data-tab="entry"' in index_html
    assert 'id="view-entry"' in index_html


def test_action_wording_kept(index_html):
    """页面内动作文案保持"解析" —— 那是按钮, 不是 Tab 名。"""
    assert 'id="btn-parse"' in index_html
    assert ">解析</button>" in index_html


# ====== 2. 固定列宽 ======

def test_table_layout_fixed(styles):
    """按内容分配宽度是行高暴涨的根源, 必须改成固定布局。"""
    b = _block(styles, ".preview-table")
    assert "table-layout: fixed" in b
    assert re.search(r"width:\s*100%", b)


def test_all_columns_have_width(styles):
    """10 列全部定宽, 任务名列 auto 吃掉剩余空间。"""
    ws = re.findall(
        r"\.preview-table th:nth-child\(\d+\),\s+\.preview-table td:nth-child\(\d+\)\s+\{ width: (\w+)", styles)
    assert len(ws) == 10, ws
    assert ws[2] == "auto", "第 3 列(任务名)应为 auto"
    assert all(re.fullmatch(r"\d+px", w) for i, w in enumerate(ws) if i != 2), ws


def test_cells_no_wrap(styles):
    b = _block(styles, ".preview-table td")
    assert "white-space: nowrap" in b
    assert "text-overflow: ellipsis" in b
    assert "overflow: hidden" in b


def test_header_no_wrap(styles):
    """表头也不能换行。

    之前只给 td 加了 nowrap, th 漏在外面。「预估分」三字在 66px 里
    刚好放得下, 换成「预计用时」四字后表头会折成两行, 把刚压下去的
    行高又顶回来 —— 属于改文案才暴露的隐性回归, 必须锁住。
    """
    b = _block(styles, ".preview-table th")
    assert "white-space: nowrap" in b, "表头缺少 nowrap, 长表头会折行"


def test_est_column_wide_enough(styles):
    """「预计用时」四字 + 单元格内边距, 列宽要放得下。

    四字约 56px, 加左右各 12px 内边距约 80px, 故下限取 80px。
    注意第 3 列(任务名)是 auto, 正则只取 px 值会让索引前移,
    所以这里按列号(1-based)取值, 不依赖数组下标。
    """
    widths = {}
    for col, w in re.findall(
            r"\.preview-table th:nth-child\((\d+)\),\s+\.preview-table td:nth-child\(\d+\)\s+\{ width: (\w+)", styles):
        widths[int(col)] = w
    assert 7 in widths, widths
    # 抓到的值带 px 后缀(如 "82px"), 先剥掉再比数值
    est = re.sub(r"px$", "", widths[7])
    assert est.isdigit(), f"第 7 列宽度应为固定像素: {widths[7]}"
    assert int(est) >= 80, f"第 7 列(预计用时) 宽度 {est}px 不足以容纳四字表头"


# ====== 3b. 「预估分」->「预计用时」(2026-09-28) ======

def test_no_stale_old_wording(app_js):
    """同一个字段在两个界面不能叫不同名字。"""
    code = _code(app_js)
    assert "预估分" not in code, "仍残留旧叫法「预估分」"


def test_preview_header_uses_new_name(app_js):
    code = _code(app_js)
    assert "<th>预计用时</th>" in code


def test_manual_form_label_uses_new_name(app_js):
    """手动添加弹窗的标签同步 —— 改一处不改另一处会让人以为
    是两个不同的字段。"""
    code = _code(app_js)
    assert "<label>预计用时" in code


def test_validation_toast_uses_new_name(app_js):
    code = _code(app_js)
    assert "预计用时必须在 1~180 分钟" in code
    assert "预估分钟必须在" not in code


def test_source_modal_uses_new_name(app_js):
    """原文弹窗是完整句式, 应与表头一致。"""
    code = _code(app_js)
    assert "预计用时: ${item.est_minutes} 分钟" in code
    assert "预估用时: ${item.est_minutes} 分钟" not in code


def test_board_inline_label_kept(app_js):
    """看板行内保持简写「预估」—— 它紧贴数字(预估 30)且与
    「实际用时」成对, 展开成「预计用时 30」反而冗余。"""
    code = _code(app_js)
    assert '<span class="est-label">预估</span>' in code


def test_settings_labels_keep_baseline_wording(app_js):
    """设置页说的是 AI 估算基准(各科预估用时), 与家长填的
    预计用时不是一回事, 不应跟着改。"""
    code = _code(app_js)
    assert "各科预估用时（分钟）" in code
    assert "各作业类型预估用时（分钟）" in code


def test_source_column_truncates(styles):
    b = _block(styles, ".preview-table .col-src small")
    assert "white-space: nowrap" in b
    assert "text-overflow: ellipsis" in b
    assert "display: block" in b


def test_narrow_screen_scrolls(styles):
    """窄屏 10 列压不动, 让外层横向滚动而不是压更窄。"""
    assert re.search(r"@media \(max-width: 760px\)[\s\S]{0,200}?overflow-x: auto", styles)
    assert re.search(r"@media \(max-width: 760px\)[\s\S]{0,200}?min-width: 640px", styles)


def test_inputs_fill_column(styles):
    """fixed 布局下 input 不铺满会留空洞。"""
    b = _block(styles, ".preview-table input, .preview-table select")
    assert re.search(r"width:\s*100%", b)
    assert "min-width: 0" in b


# ====== 3. 置信度图标 ======

def test_confidence_dot_defined(app_js):
    assert "function confidenceDot(" in app_js
    assert "CONFIDENCE_META" in app_js


def test_meta_declared_before_use(app_js):
    """先定义再用。顺序颠倒因函数体延迟求值不会真抛错, 但容易误读。"""
    assert app_js.index("const CONFIDENCE_META") < app_js.index("function confidenceDot")


def test_confidence_meta_has_all_levels(app_js):
    m = re.search(r"const CONFIDENCE_META = \{(.*?)\};", app_js, re.S)
    assert m
    for lvl in ("high", "medium", "low"):
        assert lvl in m.group(1)


def test_pill_kept_for_wide_layouts(app_js):
    """看板与原文弹窗有宽度, 仍需带文字的标签。"""
    assert "function confidencePill(" in app_js


def test_pill_and_dot_both_styled(styles):
    assert re.search(r"\.confidence-pill \{", styles)
    assert re.search(r"\.conf-dot \{", styles)
    for lvl in ("high", "medium", "low"):
        assert re.search(r"\.conf-dot\." + lvl, styles)


def test_conf_dot_is_single_char(app_js):
    """多字标签必然折行, 必须是单字符图标。"""
    m = re.search(r"const CONFIDENCE_META = \{(.*?)\};", app_js, re.S)
    icons = re.findall(r"icon: '(.+?)'", m.group(1))
    assert icons and all(len(i) <= 2 for i in icons), icons


def test_conf_dot_has_title_and_aria(app_js):
    """图标化后信息不能丢: title 给悬停, aria 给读屏。"""
    b = app_js.split("function confidenceDot(")[1].split("\n}")[0]
    assert 'title="${conf.tip}"' in b
    assert "aria-label=" in b


# ====== 4. collectRows 必须同步 (关键回归) ======

def test_collect_rows_uses_new_conf_class(app_js):
    """标记 class 换了, 读取处必须跟着换。

    否则 classList 永远匹配不到 -> 全部落到 medium ->
    低置信度的"需核对"提醒在入库时静默丢失。
    """
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert ".col-conf .conf-dot" in body, "应从 .col-conf .conf-dot 读取置信度"
    assert ".confidence-pill" not in body, "不应再读旧的 confidence-pill"


def test_collect_rows_uses_named_source_cell(app_js):
    """原文不能用 querySelector('small')(取第一个 small), 要用 .col-src。"""
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert ".col-src small" in body
    assert re.search(r"querySelector\('small'\)", body) is None, \
        "仍取'第一个 small', 以后行内多出个 small 就会读错字段"


def test_collect_rows_has_fallback(app_js):
    """缺标记时要有兜底, 不能抛错。"""
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert ": 'medium'" in body


def test_rows_have_named_classes(app_js):
    code = _code(app_js)
    for cls in ("col-conf", "col-src", "col-del"):
        assert f'class="{cls}"' in code, f"缺少 {cls}"


def test_source_cell_has_title(app_js):
    """截断了就得能看全文。"""
    code = _code(app_js)
    assert 'class="col-src"><small title=' in code


# ====== 5. 删行按钮中性色 ======

def test_row_del_no_danger_class(app_js):
    code = _code(app_js)
    assert not re.search(r'class="[^"]*danger[^"]*row-del', code)
    assert re.search(r'class="ghost row-del"', code)


def test_row_del_neutral_colors(styles):
    b = _block(styles, ".preview-table .row-del")
    assert b, "缺少 .preview-table .row-del 规则"
    for danger in ("--status-overdue", "--status-skipped", "#C0392B", "red"):
        assert danger not in b, f"不应使用危险色 {danger}"


def test_row_del_has_hover(styles):
    assert re.search(r"\.preview-table \.row-del:hover \{", styles)
    h = _block(styles, ".preview-table .row-del:hover")
    assert "var(--border)" in h or "var(--surface-2)" in h


def test_row_del_aria_label(app_js):
    code = _code(app_js)
    assert 'aria-label="删除这一行"' in code


# ====== 6. 运行时 (jsdom) ======

HARNESS = r"""
const http = require('http');
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const ITEMS = { items: [
  { subject: '语文', task_name: '完成课时作业P18-19,订正2题', due_date: '2026-09-28',
    deadline_time: null, is_optional: false, est_minutes: 25, confidence: 'low',
    source_span: '1、完成课时作业P18-19,订正2题。2.练字10 小(抄写词句)。' },
  { subject: '数学', task_name: '预习卡', due_date: '2026-09-28',
    deadline_time: null, is_optional: false, est_minutes: 15, confidence: 'high',
    source_span: '2.预习卡。' },
  { subject: '英语', task_name: '阅读课外书', due_date: '2026-09-28',
    deadline_time: null, is_optional: true, est_minutes: 20, confidence: 'medium',
    source_span: '3.阅读课外书。' },
]};

const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
let batch = null;
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/parse') && o && o.method === 'POST') return ok(ITEMS);
  if (s.includes('/assignments/batch') && o && o.method === 'POST') { batch = JSON.parse(o.body); return ok({ created_ids: [1,2,3] }); }
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__e = () => switchTab("entry");');

(async () => {
  const out = { tabs: [] };
  out.tabs = [...doc.querySelectorAll('#tabs button')].map(b => b.textContent.trim());
  w.__e();
  await new Promise(r => setTimeout(r, 200));
  doc.getElementById('entry-date').value = '2026-09-28';
  doc.getElementById('raw-text').value = '测试原文';
  doc.getElementById('btn-parse').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 400));

  out.hasTable = !!doc.querySelector('.preview-table');
  out.rowCount = doc.querySelectorAll('.preview-table tbody tr').length;

  const dots = [...doc.querySelectorAll('.col-conf .conf-dot')];
  out.dots = dots.map(d => ({
    level: d.classList.contains('high') ? 'high' : d.classList.contains('medium') ? 'medium'
         : d.classList.contains('low') ? 'low' : '?',
    text: d.textContent.trim(), title: d.getAttribute('title'), aria: d.getAttribute('aria-label'),
  }));
  const srcCell = doc.querySelector('.col-src small');
  out.srcTitle = srcCell ? srcCell.getAttribute('title') : null;
  const del = doc.querySelector('.row-del');
  out.del = del ? { cls: del.getAttribute('class'), aria: del.getAttribute('aria-label') } : null;

  // 提交: 验证 collectRows 读回正确
  doc.getElementById('btn-confirm').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 300));
  out.batch = batch;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_pv_harness.js"
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
def test_live_tab_is_登记(live):
    assert live["tabs"] == ["看板", "登记", "日历", "复盘", "设置"]


@needs_jsdom
def test_live_table_renders(live):
    assert live["hasTable"] is True
    assert live["rowCount"] == 3


@needs_jsdom
def test_live_dots_use_single_char(live):
    for d in live["dots"]:
        assert len(d["text"]) == 1, d
        assert d["title"], "图标必须保留 title 提示"
        assert d["aria"], "图标必须有 aria-label"


@needs_jsdom
def test_live_dot_levels_correct(live):
    assert [d["level"] for d in live["dots"]] == ["low", "high", "medium"]


@needs_jsdom
def test_live_source_has_title(live):
    assert live["srcTitle"] and "P18-19" in live["srcTitle"]


@needs_jsdom
def test_live_del_button_plain(live):
    assert live["del"]["cls"] == "ghost row-del", live["del"]
    assert "danger" not in live["del"]["cls"]
    assert live["del"]["aria"] == "删除这一行"


@needs_jsdom
def test_live_confidence_survives_roundtrip(live):
    """关键回归: 换了标记 class 后, 置信度必须仍能正确提交。

    低置信度行默认不勾选(设计 §9 坑 3), 所以只提交 2 条 ——
    这本身也验证了低置信度没被静默入库。
    """
    items = live["batch"]["items"]
    assert len(items) == 2, "低置信度行应默认不勾选"
    levels = [i["confidence"] for i in items]
    assert "high" in levels, f"高置信度应被读出: {levels}"
    assert "medium" in levels, f"中置信度应被读出: {levels}"
    assert levels.count("medium") == len(levels) - levels.count("high"), \
        "置信度不应全部退化成同一档"


@needs_jsdom
def test_live_source_span_correct_per_row(live):
    """原文必须取自各自的 .col-src, 不能串行。"""
    items = live["batch"]["items"]
    assert all(i["source_span"] for i in items), "原文不能为空"
    assert len({i["source_span"] for i in items}) == len(items), "各行原文应互不相同"
    high = next(i for i in items if i["confidence"] == "high")
    assert "预习卡" in high["source_span"], "原文与所属行应对应"
