"""全项目下拉统一 (自绘 select 内核 + 科目取消自动默认) · 2026-11-03。

需求原文: 「全项目的 select 下拉选项 UI 统一美化」。

为什么最后落成"换掉原生 select"而不是"美化原生 select":
原生 <select> 展开后的候选面板由操作系统绘制, 底色/文字/圆角/高亮/滚动条
都不是我们的 —— CSS 一处也改不动, 落在奶油糖果配色里像贴错了别的应用。
项目里已经因为同样的理由把 datalist 换成 .combo、原生日期换成 flatpickr、
原生 prompt/confirm 换成自绘弹窗, 所以本次是同一条路的延续。

本文件锁住四件事:

1. **内核**: 三种形态 (.combo / .sel / .picker) 共用一套开合/键盘/高亮逻辑
   —— bindDropdown。三处各写一份必然分叉 (面板样式、Esc 行为、方向键顺序
   任何一处改了另外两处都要跟着改)。
2. **.sel 契约**: hidden input 与原 <select> 同 id, 于是 8 处 .value 读取
   一行都不用改; 选中后必须在 hidden input 上派发 change, 4 处监听依赖它。
3. **默认��策略**: 科目是分类断言, 猜不得 → 一律不预选; 项目是具名实体,
   新建流程预选第一个省一次点击, 编辑流程绝不替已有记录编一个; 任务来源与
   星期保留默认 (可见且几乎不会选错)。
4. **视觉词汇**: 浮层与选项行共用 .dd-panel / .dd-list / .dd-option,
   且只用主题令牌 —— 写死系统色会退回"系统面板"观感。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app/static"


def _code(src: str) -> str:
    """剥掉注释, 只留真正执行的代码。"""
    s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))


def _block(css: str, sel: str) -> str:
    """取某条规则的内容(排除注释里的同名选择器)。"""
    css_no_comment = re.sub(r"/\*[\s\S]*?\*/", "", css)
    m = re.search(r"^[ \t]*" + re.escape(sel) + r" \{", css_no_comment, re.M)
    if not m:
        return ""
    end = css_no_comment.find("\n}", m.start())
    return css_no_comment[m.start():end] if end != -1 else ""


@pytest.fixture(scope="module")
def app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (STATIC / "styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


# ====== 1. 静态: 原生 <select> 必须全部消失 ======

def test_index_html_has_no_native_select(index_html):
    """index.html 里不该再有原生 <select>。

    展开后的候选面板由操作系统绘制, CSS 改不动 —— 与 .combo / .picker
    自绘是同一个理由, 所以一并换掉。
    """
    body = re.sub(r"<!--.*?-->", "", index_html, flags=re.S)
    assert "<select" not in body, "index.html 里还有原生 <select>"


def test_app_js_has_no_native_select(app_js):
    """app.js 的模板字符串里也不该再有原生 <select>。"""
    code = _code(app_js)
    assert "<select" not in code, "app.js 里还有原生 <select> 模板"


def test_select_helper_exists(app_js):
    """定值列表下拉的构造函数必须在。"""
    assert "function selectHtml(" in app_js, "缺少 selectHtml (定值列表下拉)"


def test_select_helper_signature(app_js):
    """selectHtml(id, options, value, emptyText) —— 四个参数缺一不可。

    value 传 null 表示"还没选"(显示未选择科目), emptyText 是空态文案。
    """
    fn = app_js.split("function selectHtml(")[1].split("\nfunction ")[0]
    head = fn.split(")")[0]
    for p in ("id", "options", "value", "emptyText"):
        assert p in head, f"selectHtml 缺参数 {p}: {head}"


def test_select_helper_uses_shared_vocabulary(app_js):
    """.sel 必须长在共享词汇上, 否则"统一"落不到用户看到的那一层。"""
    fn = app_js.split("function selectHtml(")[1].split("\nfunction ")[0]
    for cls in ("dd-panel", "dd-list", "dd-option", "dd-trigger", "dd-value", "dd-caret"):
        assert cls in fn, f".sel 里没有 {cls}"


def test_select_helper_keeps_hidden_input(app_js):
    """选中值放在 hidden input 里, id 与原 <select> 相同。

    这是"8 处 .value 读取一行都不用改"的前提 (设计 §4.1)。
    """
    fn = app_js.split("function selectHtml(")[1].split("\nfunction ")[0]
    assert 'type="hidden"' in fn, ".sel 必须用 hidden input 承载选中值"
    assert "data-sel" in fn, ".sel 容器要带 data-sel 标记"


def test_kernel_exists(app_js):
    """三种形态共用的下拉内核。"""
    assert "function bindDropdown(" in app_js, "缺少 bindDropdown 下拉内核"


def test_kernel_handles_full_keyboard_set(app_js):
    """内核负责全套键盘: Esc / ↑↓ / Home / End / Enter / Tab。

    三处各写一份必然分叉 —— Esc 行为、方向键顺序任何一处改了另外两处都要
    跟着改, 所以键盘处理只允许存在一份。
    """
    fn = app_js.split("function bindDropdown(")[1].split("\nfunction ")[0]
    for key in ("Escape", "ArrowDown", "ArrowUp", "Home", "End", "Enter", "Tab"):
        assert f"'{key}'" in fn, f"下拉内核未处理 {key}"


def test_kernel_guards_scroll_into_view(app_js):
    """高亮项滚进视口时必须判空。

    jsdom 没实现 scrollIntoView, 调用会抛 TypeError 并中断整个 keydown ——
    键盘导航会随之失灵。.combo 现有代码已踩过这个坑, 注释保留。
    """
    fn = app_js.split("function bindDropdown(")[1].split("\nfunction ")[0]
    assert "scrollIntoView" in fn, "内核未处理高亮项滚动"
    assert "typeof" in fn, "scrollIntoView 必须先判 typeof, 否则 jsdom 里会抛"


def test_old_class_names_are_gone(app_js):
    """旧的浮层/选项类名必须全部退场, 只留共享词汇。

    留两套类名等于留两套样式 —— 后改的那套必然分叉。
    """
    code = _code(app_js)
    for cls in ("combo-menu", "combo-option", "combo-empty",
                "picker-panel", "picker-list", "picker-option",
                "picker-trigger", "picker-value", "picker-caret"):
        assert f'"{cls}"' not in code, f"旧类名 {cls} 还在用, 应改用共享词汇"


def test_combo_and_picker_use_the_kernel(app_js):
    """.combo 与 .picker 必须接同一个内核, 而不是各自留一份键盘逻辑。"""
    code = _code(app_js)
    assert code.count("bindDropdown(") >= 2, \
        "只有内核一处调用 —— .combo / .picker 没接进去"


def test_option_click_does_not_forward_to_label_control(app_js):
    """点选项必须 preventDefault —— 否则 label 会把点击转发给触发键。

    选项都住在 <label> 里(表单网格是"标签 + 控件"两列), 而 label 的激活
    行为会把点击转发给"被标记的那个控件" —— 表单里第一个可标记元素, 也就是
    .dd-trigger 按钮。转发过来的第二次 click 会把刚收起的浮层又打开, 表现
    为"点了选项却像没点上"。

    jsdom 不实现 label 的激活行为, 所以这条只能靠静态断言守住; 真实行为
    由浏览器实机验收兜着 (2026-11-03 实机验收时真的复现过一次)。
    """
    code = _code(app_js)
    fn = code.split("function bindDropdown(")[1].split("\nfunction ")[0]
    handler = re.search(r"listEl\.addEventListener\('click'.*?\n  \}\);", fn, re.S)
    assert handler, "内核里没有选项的 click 处理"
    assert "preventDefault" in handler.group(0), \
        "选项点击必须 preventDefault —— 否则 label 把点击转发给触发键, 浮层会重新弹回"


# ====== 2. CSS: 共享词汇 + 主题令牌 ======

def test_css_has_shared_dropdown_vocabulary(styles_css):
    """浮层/列表/选项/触发键/当前值/三角 六个类必须都在。"""
    for cls in (".dd-panel", ".dd-list", ".dd-option", ".dd-trigger", ".dd-value", ".dd-caret"):
        assert cls in styles_css, f"styles.css 里没有 {cls}"


def test_css_panel_uses_theme_tokens(styles_css):
    """浮层底色/边框/圆角/阴影一律走主题令牌。"""
    block = _block(styles_css, ".dd-panel")
    assert block, "缺少 .dd-panel 样式"
    for token in ("var(--surface)", "var(--border)", "var(--radius-md)", "var(--shadow-card)"):
        assert token in block, f".dd-panel 少了 {token}"


def test_css_option_uses_theme_tokens(styles_css):
    """选项行与高亮态必须走主题令牌。"""
    block = _block(styles_css, ".dd-option")
    assert block, "缺少 .dd-option 样式"
    assert "var(--text-1)" in block, ".dd-option 文字色未走令牌"
    active = _block(styles_css, ".dd-option.is-active")
    assert active, "缺少 .dd-option.is-active 样式"
    assert "var(--accent-soft)" in active, "高亮底色未用主色柔光"


def test_css_option_has_selected_marker(styles_css):
    """选中项要看得出来 —— 家长得知道当前选的是哪个。"""
    selected = _block(styles_css, ".dd-option.is-selected")
    assert selected, "缺少 .dd-option.is-selected 样式"
    assert "var(--accent-soft)" in selected, "选中项底色未用主色柔光"


def test_css_no_hardcoded_system_colors(styles_css):
    """共享浮层里不许出现写死的系统色。

    写死颜色就等于退回"系统面板"观感 —— 正是这次要消灭的东西。
    """
    block = _block(styles_css, ".dd-panel") + _block(styles_css, ".dd-option")
    for bad in ("#fff", "#ffffff", "white;", "rgb(", "rgba("):
        assert bad not in block.lower(), f"共享下拉样式里出现了写死的颜色 {bad!r}"


def test_css_panel_scrollbar_is_themed(styles_css):
    """浮层里的滚动条也要用主题色, 不然又是一块系统灰。"""
    assert "scrollbar-color" in styles_css, "未给下拉浮层设滚动条主题"
    assert "scrollbar-width" in styles_css, "未给下拉浮层设滚动条宽度"


def test_old_css_class_names_are_gone(styles_css):
    """旧类名的样式规则必须删掉。"""
    code = re.sub(r"/\*[\s\S]*?\*/", "", styles_css)
    for sel in (".combo .combo-menu", ".combo .combo-option", ".picker-panel",
                ".picker-list", ".picker-option", ".picker-trigger",
                ".picker-value", ".picker-caret"):
        assert sel + " {" not in code, f"旧样式 {sel} 还在"


# ====== 3. 预览表科目列: 分段按钮 ======

def test_preview_subject_column_uses_segment_buttons(app_js):
    """预览表科目列改成三段按钮, 值仍落在 data-field="subject" 的 hidden input。

    列宽没有余量 (78px 里只剩 54px 内容区), 下拉箭头吃不下 —— 分段按钮
    贴住单元格两侧才装得下, 而且 collectRows() 一行都不用改。
    """
    code = _code(app_js)
    assert 'data-field="subject"' in code, "科目列必须保留 data-field=subject 的 hidden input"
    assert "seg-btn" in code, "科目列没有分段按钮"
    assert "<select data-field=" not in code, "预览表科目列还是原生 select"


def test_preview_segment_buttons_share_one_hidden_input(app_js):
    """分段按钮组里藏一个 hidden input, collectRows() 读它。"""
    code = _code(app_js)
    assert re.search(r'data-field="subject"[^>]*/>', code), \
        "分段按钮组里必须有 hidden input 承载科目值"


# ====== 4. 运行时 (jsdom) ======

node = shutil.which("node")
jsdom_available = False
if node:
    try:
        subprocess.run([node, "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False


HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const SUBJ_ITEM = {
  id: 375, subject: '数学', task_kind: 'subject', project_id: null, project_name: null,
  task_name: '订正限时计算', due_date: '2026-11-02', deadline_time: null,
  is_optional: false, status: 'todo', est_minutes: 5, actual_minutes: null,
  task_type: null, confidence: 'high', source_span: '订正限时计算',
};
const TRAIN_ITEM = {
  id: 380, subject: null, task_kind: 'training', project_id: null, project_name: null,
  task_name: '练琴', due_date: '2026-11-02', deadline_time: null,
  is_optional: false, status: 'todo', est_minutes: 20, actual_minutes: null,
  task_type: null, confidence: 'high', source_span: '练琴',
};
const PROJECTS = { items: [
  { id: 5, name: '钢琴', project_type: 'custom', status: 'active' },
  { id: 6, name: '国际象棋', project_type: 'custom', status: 'active' },
]};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
const posts = [];
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/projects') && !s.includes('/project-templates')) return ok(PROJECTS);
  if (s.includes('/project-templates')) return ok({ items: [] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (o && o.method === 'POST') {
    posts.push({ url: s, body: JSON.parse(o.body) });
    return ok({ id: 1, skipped: false });
  }
  return ok({});
};
w.confirm = () => { throw new Error('不应调用原生 confirm'); };
w.prompt  = () => { throw new Error('不应调用原生 prompt'); };
w.alert   = () => { throw new Error('不应调用原生 alert'); };
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(require('path').join(require('path').dirname(appJsPath), 'charts.js'), 'utf8'));
// app.js 是严格模式, window.eval 里声明的函数不会泄漏到全局 —— 要用就在
// 同一段 eval 里挂出来 (与项目既有 harness 同款做法)。
w.eval(fs.readFileSync(appJsPath, 'utf8') +
  '\n;globalThis.__t = { selectHtml, initSels, openManualAddModal, openEditAssignmentModal,' +
  ' openPlanItemModal, openScheduleSlotModal, renderPreviewTable };');

const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
const wait = ms => new Promise(r => setTimeout(r, ms));
const val = id => { const e = doc.getElementById(id); return e ? e.value : null; };
const sel = id => doc.getElementById(id) ? doc.getElementById(id).closest('[data-sel]') : null;
const labelOf = id => { const r = sel(id); const v = r && r.querySelector('.dd-value'); return v ? v.textContent.trim() : null; };
const isEmptyLabel = id => { const r = sel(id); const v = r && r.querySelector('.dd-value'); return v ? v.classList.contains('is-empty') : null; };
const openPanel = id => { const r = sel(id); const p = r && r.querySelector('.dd-panel'); return p ? !p.hidden : null; };
const pickOption = (id, value) => {
  const r = sel(id);
  const opt = [...r.querySelectorAll('.dd-option')].find(o => o.dataset.value === value);
  if (!opt) throw new Error('没有这个选项: ' + value);
  click(opt);
};

(async () => {
  const out = {};

  // ---------- A. .sel 契约 ----------
  const host = doc.createElement('div');
  host.innerHTML = w.__t.selectHtml('t-subject', [['语文', '语文'], ['数学', '数学'], ['英语', '英语']], null, '未选择科目');
  doc.body.appendChild(host);
  w.__t.initSels(host);

  const root = host.querySelector('[data-sel]');
  const hidden = doc.getElementById('t-subject');
  const changes = [];
  hidden.addEventListener('change', () => changes.push(hidden.value));

  out.selHiddenId = hidden.id;
  out.selHiddenValue = hidden.value;
  out.selLabel = labelOf('t-subject');
  out.selIsEmpty = isEmptyLabel('t-subject');
  out.selOptions = [...root.querySelectorAll('.dd-option')].map(o => o.dataset.value);
  out.selSelectedCount = root.querySelectorAll('.dd-option.is-selected').length;
  out.selHasListboxRole = !!root.querySelector('.dd-list[role="listbox"]');

  const trigger = root.querySelector('.dd-trigger');
  click(trigger);
  out.selOpenAfterClick = openPanel('t-subject');
  out.selAriaExpanded = trigger.getAttribute('aria-expanded');

  // 外部点击收起
  doc.body.dispatchEvent(new w.PointerEvent('pointerdown', { bubbles: true }));
  out.selClosedByOutsideClick = !openPanel('t-subject');

  // 键盘: 展开 -> ↓ 高亮 -> ↑ 回退 -> Home/End -> Enter 选中
  click(trigger);
  key(trigger, 'ArrowDown');
  out.activeAfterDown = (root.querySelector('.dd-option.is-active') || {}).dataset
    ? root.querySelector('.dd-option.is-active').dataset.value : null;
  key(trigger, 'ArrowDown');
  out.activeAfterDown2 = root.querySelector('.dd-option.is-active').dataset.value;
  key(trigger, 'ArrowUp');
  out.activeAfterUp = root.querySelector('.dd-option.is-active').dataset.value;
  key(trigger, 'Home');
  out.activeAfterHome = root.querySelector('.dd-option.is-active').dataset.value;
  key(trigger, 'End');
  out.activeAfterEnd = root.querySelector('.dd-option.is-active').dataset.value;
  out.activedescendant = trigger.getAttribute('aria-activedescendant');
  key(trigger, 'Enter');
  out.selValueAfterEnter = hidden.value;
  out.selChanges = changes;
  out.selLabelAfterPick = labelOf('t-subject');
  out.selSelectedAfterPick = root.querySelectorAll('.dd-option.is-selected').length;
  out.selClosedAfterPick = !openPanel('t-subject');

  // Esc 收起 (只收浮层, 不连带关弹窗)
  click(trigger);
  key(trigger, 'Escape');
  out.selClosedByEscape = !openPanel('t-subject');

  // Tab 收起
  click(trigger);
  key(trigger, 'Tab');
  out.selClosedByTab = !openPanel('t-subject');

  // ---------- B. 手动添加弹窗: 科目不预选 ----------
  // 项目候选是异步拉来的, 等它到齐再开弹窗 —— 否则新建流程的"预选第一个
  // 项目"还来不及生效。
  await wait(500);
  w.__t.openManualAddModal();
  out.manualKind = val('m-kind');
  out.manualSubjectValue = val('m-subject');
  out.manualSubjectLabel = labelOf('m-subject');
  out.manualSubjectIsEmpty = isEmptyLabel('m-subject');
  out.manualProjectValue = val('m-project');
  out.manualProjectPolicy = (doc.getElementById('m-project') || {}).closest
    ? doc.getElementById('m-project').closest('[data-picker]').dataset.default : null;

  // 选科目 -> 切来源 -> 科目值不该被清空 (隐藏 != 清空)
  pickOption('m-subject', '数学');
  out.manualSubjectAfterPick = val('m-subject');
  pickOption('m-kind', 'training');
  out.manualKindAfterSwitch = val('m-kind');
  out.manualSubjectWrapHidden = doc.getElementById('m-subject-wrap').hidden;
  out.manualSubjectKeptAfterSwitch = val('m-subject');

  // ---------- C. 未选科目时提交被拦下 ----------
  doc.getElementById('m-kind').value = 'subject';
  doc.getElementById('m-kind').dispatchEvent(new w.Event('change', { bubbles: true }));
  doc.getElementById('m-subject').value = '';
  doc.getElementById('m-name').value = '语文练习';
  doc.getElementById('m-est').value = '15';
  const beforePosts = posts.length;
  click(doc.getElementById('modal-ok'));
  await wait(200);
  out.blockedToast = doc.getElementById('toast').textContent;
  out.blockedNoPost = posts.length === beforePosts;
  out.blockedStillOpen = !doc.getElementById('modal').classList.contains('hidden');

  // ---------- D. 编辑弹窗: 带出原值 / 没有就空着 ----------
  w.__t.openEditAssignmentModal(SUBJ_ITEM);
  out.editKind = val('ed-kind');
  out.editSubjectValue = val('ed-subject');
  out.editSubjectLabel = labelOf('ed-subject');
  out.editProjectValue = val('ed-project');
  out.editProjectPolicy = doc.getElementById('ed-project').closest('[data-picker]').dataset.default;

  w.__t.openEditAssignmentModal(TRAIN_ITEM);
  out.editNoSubjectValue = val('ed-subject');
  out.editNoSubjectLabel = labelOf('ed-subject');
  out.editNoSubjectIsEmpty = isEmptyLabel('ed-subject');

  // ---------- E. 周计划项 / 课表时段 ----------
  await w.__t.openPlanItemModal(null);
  out.planKind = val('pi-kind');
  out.planSubjectValue = val('pi-subject');
  out.planSubjectLabel = labelOf('pi-subject');

  await w.__t.openScheduleSlotModal(null);
  out.slotWeekday = val('slot-weekday');

  // ---------- F. 快速添加面板 ----------
  out.quickSubjectValue = val('qa-subject');
  out.quickSubjectLabel = labelOf('qa-subject');
  out.quickSubjectIsEmpty = isEmptyLabel('qa-subject');

  // ---------- G. 预览表科目列 ----------
  w.__t.renderPreviewTable([
    { subject: '数学', task_name: '完成知能P35', due_date: '2026-11-02', est_minutes: 10,
      is_optional: false, confidence: 'high', source_span: '完成知能' },
  ], 'batch-1');
  const row = doc.querySelector('#parse-result tbody tr');
  out.segHiddenValue = row.querySelector('[data-field="subject"]').value;
  out.segButtons = [...row.querySelectorAll('.seg-btn')].map(b => b.textContent.trim());
  out.segSelected = (row.querySelector('.seg-btn.is-on') || {}).dataset
    ? row.querySelector('.seg-btn.is-on').dataset.value : null;
  click(row.querySelectorAll('.seg-btn')[0]);
  out.segHiddenValueAfterClick = row.querySelector('[data-field="subject"]').value;
  out.segSelectedAfterClick = row.querySelector('.seg-btn.is-on').dataset.value;

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_dropdown_harness.js"
    h.parent.mkdir(exist_ok=True)
    h.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(h), str(STATIC / "index.html"),
             str(STATIC / "app.js"),
             str(STATIC / "vendor/flatpickr.min.js"),
             str(STATIC / "vendor/flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-3000:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if h.exists():
            h.unlink()


needs_jsdom = pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")


# ---------- A. .sel 契约 ----------

@needs_jsdom
def test_sel_keeps_hidden_input_id(live):
    """hidden input 的 id 与原 <select> 相同 —— 读取代码一行都不用改。"""
    assert live["selHiddenId"] == "t-subject"
    assert live["selHiddenValue"] == "", "没选时 hidden input 必须是空"


@needs_jsdom
def test_sel_empty_state_shows_placeholder(live):
    """空态显示「未选择科目」, 且没有任何一项带 is-selected。"""
    assert live["selLabel"] == "未选择科目"
    assert live["selIsEmpty"] is True, "空态应带 is-empty 弱色"
    assert live["selSelectedCount"] == 0, "空态不该有选中项"


@needs_jsdom
def test_sel_lists_all_options(live):
    assert live["selOptions"] == ["语文", "数学", "英语"]
    assert live["selHasListboxRole"] is True, "候选项容器要带 role=listbox"


@needs_jsdom
def test_sel_dispatches_change_on_hidden_input(live):
    """选中后必须在 hidden input 上派发 change。

    派发在 trigger 按钮上没用 —— 监听者读的是 getElementById('xx').value,
    事件目标必须与它一致 (设计 §4.3)。
    """
    assert live["selChanges"] == ["英语"], f"change 没派发到 hidden input: {live['selChanges']}"


@needs_jsdom
def test_sel_pick_updates_value_and_label(live):
    assert live["selValueAfterEnter"] == "英语"
    assert live["selLabelAfterPick"] == "英语"
    assert live["selSelectedAfterPick"] == 1, "选中项应有 is-selected 标记"


@needs_jsdom
def test_sel_opens_on_trigger_click(live):
    assert live["selOpenAfterClick"] is True
    assert live["selAriaExpanded"] == "true"


@needs_jsdom
def test_sel_closes_on_outside_click(live):
    """点浮层外面必须收起 (capture 阶段的 pointerdown)。"""
    assert live["selClosedByOutsideClick"] is True


@needs_jsdom
def test_sel_closes_on_escape(live):
    assert live["selClosedByEscape"] is True


@needs_jsdom
def test_sel_closes_on_tab(live):
    assert live["selClosedByTab"] is True


@needs_jsdom
def test_sel_keyboard_navigation(live):
    """↓/↑ 移动高亮, Home/End 跳首尾。"""
    assert live["activeAfterDown"] == "语文"
    assert live["activeAfterDown2"] == "数学"
    assert live["activeAfterUp"] == "语文"
    assert live["activeAfterEnd"] == "英语"
    assert live["activeAfterHome"] == "语文"


@needs_jsdom
def test_sel_exposes_activedescendant(live):
    """高亮项要通过 aria-activedescendant 暴露给读屏软件。"""
    assert live["activedescendant"], "高亮项未同步 aria-activedescendant"


# ---------- B. 默认值策略 ----------

@needs_jsdom
def test_manual_add_subject_is_not_preselected(live):
    """科目一律不预选 —— 分类断言猜不得。

    原生 select 会自动选第一项「语文」, 家长加一条数学作业, 科目就静默
    落在语文下, 看板上归错了类, 而界面上没有任何一处会提示。
    """
    assert live["manualSubjectValue"] == "", "手动添加时科目不该预选"
    assert live["manualSubjectLabel"] == "未选择科目"
    assert live["manualSubjectIsEmpty"] is True


@needs_jsdom
def test_task_kind_keeps_default(live):
    """任务来源保留默认「学科作业」—— 它可见且几乎不会选错。"""
    assert live["manualKind"] == "subject"


@needs_jsdom
def test_project_keeps_default_first_in_new_flow(live):
    """新建流程里项目仍预选第一个 (defaultFirst 机制未动)。"""
    assert live["manualProjectPolicy"] == "first"
    assert live["manualProjectValue"], "新建流程应预选第一个项目"


@needs_jsdom
def test_edit_modal_shows_original_subject(live):
    """编辑已有记录时科目带出原值。"""
    assert live["editSubjectValue"] == "数学"
    assert live["editSubjectLabel"] == "数学"


@needs_jsdom
def test_edit_modal_subject_empty_when_record_has_none(live):
    """原记录没有科目时就是空的 —— 绝不替它编一个。"""
    assert live["editNoSubjectValue"] == ""
    assert live["editNoSubjectLabel"] == "未选择科目"
    assert live["editNoSubjectIsEmpty"] is True


@needs_jsdom
def test_edit_modal_project_not_autofilled(live):
    """编辑流程的项目绝不预选 (2026-11-02「钢琴」事故的修复, 一分不能让)。"""
    assert live["editProjectPolicy"] == "none"
    assert live["editProjectValue"] == "", "编辑流程不该替已有记录编项目"


@needs_jsdom
def test_plan_item_subject_not_preselected(live):
    """新增周计划项时科目也不预选。"""
    assert live["planKind"] == "subject"
    assert live["planSubjectValue"] == ""
    assert live["planSubjectLabel"] == "未选择科目"


@needs_jsdom
def test_weekday_keeps_default(live):
    """星期保留默认「周二」—— 时段列表里直接写着, 不会选错。"""
    assert live["slotWeekday"] == "2"


@needs_jsdom
def test_quick_add_subject_not_preselected(live):
    """快速添加面板的科目同样不预选。"""
    assert live["quickSubjectValue"] == ""
    assert live["quickSubjectLabel"] == "未选择科目"
    assert live["quickSubjectIsEmpty"] is True


# ---------- C. 提交前拦截 ----------

@needs_jsdom
def test_manual_add_blocks_when_subject_missing(live):
    """学科作业没选科目时必须在前端拦下, 并说清是哪一栏。"""
    assert live["blockedNoPost"] is True, "没选科目却把请求发出去了"
    assert live["blockedToast"] == "学科作业必须选择科目", \
        f"提示文案与后端不一致: {live['blockedToast']!r}"
    assert live["blockedStillOpen"] is True, "被拦下后不该关窗"


# ---------- D. 切换来源不清空另一侧 ----------

@needs_jsdom
def test_switching_kind_keeps_other_side_value(live):
    """把来源从学科作业切到培训, 科目只是隐藏, 已选的值不重置。"""
    assert live["manualSubjectAfterPick"] == "数学"
    assert live["manualKindAfterSwitch"] == "training"
    assert live["manualSubjectWrapHidden"] is True, "切到培训后科目行应隐藏"
    assert live["manualSubjectKeptAfterSwitch"] == "数学", \
        "隐藏不等于清空 —— 切回来时原值还在"


# ---------- E. 预览表科目列 ----------

@needs_jsdom
def test_preview_subject_uses_segment_buttons(live):
    """科目列是三段按钮, 值落在 hidden input 里。"""
    assert live["segButtons"] == ["语文", "数学", "英语"]
    assert live["segHiddenValue"] == "数学", "AI 解析出的科目要落在 hidden input"
    assert live["segSelected"] == "数学", "对应分段应标记为选中"


@needs_jsdom
def test_preview_segment_click_updates_value(live):
    """点分段按钮即改值 —— collectRows() 读的就是它。"""
    assert live["segHiddenValueAfterClick"] == "语文"
    assert live["segSelectedAfterClick"] == "语文"
