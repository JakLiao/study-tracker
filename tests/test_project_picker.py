"""项目选择器的"默认选中"与 [hidden] 失效 (2026-11-02 用户反馈).

用户反馈原文: 「我编辑作业项目的时候, 为什么现在有个『项目』, 而且出现跟这个
项目信息无关的『钢琴』, 这里肯定出错了, 如果原作业项目没这个数据, 应该默认
不选中才对。」

这条反馈背后是两个独立的缺陷, 叠在一起才让界面长成截图那样:

1. **[hidden] 在 .form-grid 里不生效 (CSS)**
   `.form-grid > label` 用了 `display: contents` (让 label 的子元素直接参与
   两列网格)。作者样式优先级高于浏览器对 `[hidden]` 的 `display: none`,
   于是 `<label id="ed-project-wrap" hidden>` 照样显示 —— 学科作业本不该有
   「项目」这一行, 弹窗里却凭空多出来。手动添加弹窗的「项目」行同样中招。

   项目里已有同款先例: `.quick-row[hidden]` / `.cal-legend[hidden]` 都是
   作者样式盖掉 [hidden] 后专门补的 `display: none`。`.form-grid` 漏了。

2. **选择器自己编了一个项目 (JS)**
   `projectPickerHtml` 在"原记录没有项目"时会默认落在候选列表的第一个项目
   上, `bindProjectPicker` 的 sync() 还会再强制选一次。于是库里
   project_id 为 null 的作业, 一打开编辑弹窗就变成了「钢琴」(候选里
   第一个)。

   这不只是显示错 —— **保存时会把 project_id 悄悄写进去**:
   培训/习惯任务原��没挂项目, 家长只是改了个估时点保存, 请求里就带上
   了「钢琴」, 这条任务从此被挪到了别的项目下。

两条的修法方向一致: **默认值可以给"新建"的记录兜底, 但绝不能改写
"已存在"的记录。** 新建流程(快速添加/手动添加/新增课表时段/新增计划项)
保留"默认第一个项目"的省事行为; 编辑已有记录时一律回到原值, 没有就是
没有。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ====== 1. CSS: [hidden] 必须在 .form-grid 里真的生效 ======

@pytest.fixture(scope="module")
def styles_css() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def test_form_grid_hidden_label_really_hides(styles_css):
    """[hidden] 必须能盖住 .form-grid > label 的 display: contents。

    这是 2026-11-02 反馈的第一半: 学科作业的编辑弹窗里凭空多出「项目」
    一行。代码里明明写了 hidden, 是 CSS 让它失效的 —— 少了这条规则,
    hidden 属性在这张表单里就是个摆设。
    """
    m = re.search(r"\.form-grid\s*>\s*label\[hidden\]\s*\{([^}]*)\}", styles_css)
    assert m, (
        ".form-grid > label[hidden] 必须显式 display:none —— "
        "display: contents 会盖掉浏览器对 [hidden] 的处理, "
        "学科作业的弹窗就会多出「项目」这一行"
    )
    assert "display:none" in m.group(1).replace(" ", ""), \
        f"[hidden] 规则里应有 display:none, 实际: {m.group(1)!r}"


def test_form_grid_label_uses_display_contents(styles_css):
    """锁住前提: .form-grid > label 确实是 display: contents。

    哪天改成 flex/grid, [hidden] 就自然生效了, 这条规则会变成冗余 ——
    冗余无害, 但把它删掉之前得先看这条测试。
    """
    m = re.search(r"\.form-grid\s*>\s*label\s*\{([^}]*)\}", styles_css)
    assert m, ".form-grid > label 规则不见了"
    assert "display:contents" in m.group(1).replace(" ", ""), \
        f"前提变了: {m.group(1)!r}"


# ====== 2. JS: 选择器不得替已有记录编项目 ======

def _code(js: str) -> str:
    return "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))


def test_edit_modal_does_not_default_to_first_project(app_js):
    """编辑弹窗不能默认选第一个项目。

    原记录没有项目时就该显示"未选择项目" —— 默认落在「钢琴」上, 家长
    看到的就跟这条作业毫无关系, 而且保存时还会真的写进库里。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    call = re.search(r"projectPickerHtml\('ed-project'[^)]*\)", block)
    assert call, "编辑弹窗未使用项目选择器"
    assert "defaultFirst" not in call.group(0), \
        f"编辑已有记录不得默认选第一个项目: {call.group(0)}"


def test_plan_item_edit_does_not_default_to_first_project(app_js):
    """改周计划项同理 —— 那也是改一条已存在的记录。

    新增计划项时仍可兜默认值, 改已有计划项不行 —— 所以这个开关必须跟着
    "是不是新记录"走, 而不是写死。
    """
    block = app_js.split("async function openPlanItemModal(")[1].split("\n}")[0]
    call = re.search(r"projectPickerHtml\('pi-project'[^)]*\)", block)
    assert call, "周计划项弹窗未使用项目选择器"
    assert "defaultFirst" in call.group(0), \
        f"默认值开关应按'是否新记录'决定: {call.group(0)}"
    assert "!item" in call.group(0), \
        f"改已有计划项不得兜默认值: {call.group(0)}"


def test_slot_edit_does_not_default_to_first_project(app_js):
    """改课表时段同理 —— 原来那条时段属于哪个项目就是哪个项目。"""
    block = app_js.split("async function openScheduleSlotModal(")[1].split("\n}")[0]
    call = re.search(r"projectPickerHtml\('slot-project'[^)]*\)", block)
    assert call, "课表时段弹窗未使用项目选择器"
    assert "!slot" in call.group(0), \
        f"改已有时段不得兜默认值: {call.group(0)}"


def test_new_record_flows_keep_first_project_default(app_js):
    """新建流程仍可默认第一个项目 —— 那只是给新记录兜底, 不是改写旧数据。

    「家长只想加一条培训任务, 每次都要多点一次」是当初保留这个默认值的
    理由, 别顺手把它一起删了。
    """
    for modal, field in (("function openManualAddModal(", "m-project"),
                         ("function initQuickAdd(", "qa-project")):
        block = app_js.split(modal)[1].split("\n}")[0]
        call = re.search(rf"projectPickerHtml\('{field}'[^)]*\)", block)
        assert call, f"{modal} 未使用项目选择器"
        assert "defaultFirst" in call.group(0), \
            f"新建流程应保留'默认第一个项目': {call.group(0)}"


def test_picker_has_default_policy_switch(app_js):
    """选择器本身要认这个策略, 且默认不得是"自动选中"。

    默认值取最保守的一档(不自动选中), 想省事的新建流程显式开口子 ——
    这样以后再加新的弹窗, 忘配也不会静默改写别人的记录。
    """
    fn = _code(app_js).split("function projectPickerHtml(")[1].split("\n}")[0]
    assert "defaultFirst" in fn, "选择器未支持默认值策略"
    body = _code(app_js).split("function bindProjectPicker(")[1].split("\n}")[0]
    assert "defaultFirst" in body or "dataset.default" in body, \
        "绑定逻辑未按策略决定要不要自动选中"


# ====== 3. 运行时 (jsdom) ======

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

// 学科作业: 库里没有项目 (project_id = null) —— 用户反馈的那条。
const SUBJ_ITEM = {
  id: 375, subject: '数学', task_kind: 'subject', project_id: null, project_name: null,
  task_name: '订正限时计算', due_date: '2026-11-02',
  deadline_time: null, is_optional: false, status: 'todo',
  est_minutes: 5, actual_minutes: null, task_type: null,
  confidence: 'high', source_span: '订正限时计算',
};
// 培训任务: 同样没有项目 —— 这条用来验"保存时会不会被悄悄挂上钢琴"。
const TRAIN_ITEM = {
  id: 380, subject: null, task_kind: 'training', project_id: null, project_name: null,
  task_name: '练琴', due_date: '2026-11-02',
  deadline_time: null, is_optional: false, status: 'todo',
  est_minutes: 20, actual_minutes: null, task_type: null,
  confidence: 'high', source_span: '练琴',
};
// 培训任务, 但确实挂着项目 —— 修完不能把这种也清空。
const TRAIN_ITEM2 = {
  id: 381, subject: null, task_kind: 'training', project_id: 6, project_name: '国际象棋',
  task_name: '下棋', due_date: '2026-11-02',
  deadline_time: null, is_optional: false, status: 'todo',
  est_minutes: 20, actual_minutes: null, task_type: null,
  confidence: 'high', source_span: '下棋',
};

const BOARD = {
  date: '2026-11-02', label: '今天', is_today: true, has_data: true, is_late_night: false,
  by_subject: { 语文: [], 数学: [SUBJ_ITEM], 英语: [] },
  by_kind: { training: [TRAIN_ITEM, TRAIN_ITEM2], habit: [] },
  optional_items: [], upcoming_deadlines: [], overdue_items: [], training_logs: {},
  progress: { done: 0, skipped: 0, settled: 0, total: 2, optional: 0, optional_done: 0,
              optional_skipped: 0, required: 2, pct: 0, est_total: 25, actual_total: 0 },
};

// 候选项目: 「钢琴」排在第一个 —— 它就是被误选中的那个。
const PROJECTS = { items: [
  { id: 5, name: '钢琴', project_type: 'custom', status: 'active' },
  { id: 6, name: '国际象棋', project_type: 'custom', status: 'active' },
  { id: 7, name: '阅读', project_type: 'custom', status: 'active' },
]};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
const patched = [];
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/board/today') || s.includes('/board?date=')) return ok(BOARD);
  if (s.includes('/board/dates')) return ok({ dates: [{ date: '2026-11-02', total: 2 }] });
  if (s.includes('/projects') && !s.includes('/project-templates')) return ok(PROJECTS);
  if (s.includes('/project-templates')) return ok({ items: [] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (o && o.method === 'PATCH') {
    patched.push({ url: s, body: JSON.parse(o.body) });
    return ok({ id: 7 });
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
w.eval(fs.readFileSync(appJsPath, 'utf8') +
  '\n;globalThis.__b = () => switchTab("board");');

const modal = doc.getElementById('modal');
const okBtn = doc.getElementById('modal-ok');
const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
const wait = ms => new Promise(r => setTimeout(r, ms));
const val = id => { const e = doc.getElementById(id); return e ? e.value : null; };
const open = () => !modal.classList.contains('hidden');
const rowByName = t =>
  [...doc.querySelectorAll('.assignment-row')].find(r => r.textContent.includes(t));

// 打开某条作业的编辑弹窗, 返回弹窗里项目选择器的观察结果。
async function openEdit(taskName) {
  const row = rowByName(taskName);
  if (!row) throw new Error('看板里找不到作业行: ' + taskName);
  const btn = row.querySelector('.row-actions button.edit');
  if (!btn) throw new Error('作业行里没有编辑按钮: ' + taskName);
  click(btn);
  await wait(200);
  const wrap = doc.getElementById('ed-project-wrap');
  const picker = wrap && wrap.querySelector('.picker');
  const label = wrap && wrap.querySelector('.dd-value');
  return {
    opened: open(),
    kind: val('ed-kind'),
    // hidden 属性在 JS 层是可靠的; CSS 是否真的把它藏起来由 CSS 契约测试负责。
    wrapHidden: wrap ? wrap.hasAttribute('hidden') : null,
    pickerValue: val('ed-project'),
    pickerLabel: label ? label.textContent.trim() : null,
    pickerIsEmpty: label ? label.classList.contains('is-empty') : null,
    pickerPolicy: picker ? picker.dataset.default : null,
  };
}

(async () => {
  const out = {};
  w.__b();
  await wait(400);

  out.subject = await openEdit('订正限时计算');
  key(doc, 'Escape');
  await wait(200);

  out.training = await openEdit('练琴');
  // 改个名字, 逼出一条真正的 PATCH —— 只改名字不该夹带 project_id。
  doc.getElementById('ed-name').value = '练琴(改)';
  const before = patched.length;
  click(okBtn);
  await wait(300);
  out.trainPatches = patched.length - before;
  out.trainPatchBody = patched.length > before ? patched[patched.length - 1].body : null;
  out.closedAfterSave = !open();

  // 学科作业也要验一遍: 保存时同样不能夹带 project_id。
  key(doc, 'Escape');
  await wait(200);
  out.subject2 = await openEdit('订正限时计算');
  doc.getElementById('ed-name').value = '订正限时计算(改)';
  const beforeSubj = patched.length;
  click(okBtn);
  await wait(300);
  out.subjectPatches = patched.length - beforeSubj;
  out.subjectPatchBody = patched.length > beforeSubj ? patched[patched.length - 1].body : null;

  // 挂着项目的培训任务: 必须照样带出它自己的项目 (别被"清空"波及)。
  key(doc, 'Escape');
  await wait(200);
  out.withProject = await openEdit('下棋');

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_project_picker_harness.js"
    h.parent.mkdir(exist_ok=True)
    h.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(h), str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(ROOT / "app/static/vendor/flatpickr.min.js"),
             str(ROOT / "app/static/vendor/flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-2000:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if h.exists():
            h.unlink()


needs_jsdom = pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")


@needs_jsdom
def test_subject_assignment_has_no_project_row(live):
    """学科作业不该出现「项目」这一行。

    学科作业按科目归口, 没有项目的概念 —— 弹窗里多一行, 家长会以为
    这条作业归在某个项目下, 而它根本没有。
    """
    s = live["subject"]
    assert s["opened"] is True
    assert s["kind"] == "subject"
    assert s["wrapHidden"] is True, "学科作业的「项目」行应带 hidden"


@needs_jsdom
def test_subject_assignment_project_is_not_autofilled(live):
    """原记录没有项目时, 选择器必须空着, 不能自动变成「钢琴」。"""
    s = live["subject"]
    assert s["pickerValue"] == "", \
        f"原记录没有项目, 选择器却选中了 {s['pickerValue']!r} (钢琴)"
    assert s["pickerLabel"] != "钢琴", "项目名显示成了无关的「钢琴」"
    assert s["pickerIsEmpty"] is True, "空状态应有 is-empty 弱色样式"
    assert s["pickerLabel"], "空状态仍应有一句提示文案"


@needs_jsdom
def test_training_assignment_without_project_stays_empty(live):
    """培训任务本来就没挂项目时, 编辑弹窗里也得是空的。"""
    t = live["training"]
    assert t["opened"] is True
    assert t["kind"] == "training"
    assert t["pickerValue"] == "", \
        f"原记录没挂项目, 选择器却选中了 {t['pickerValue']!r} (钢琴)"


@needs_jsdom
def test_saving_does_not_silently_attach_a_project(live):
    """保存时不得顺手把「钢琴」写进 project_id。

    这是本条反馈最要命的一半: 培训/习惯任务原本没挂项目, 家长只是改个
    估时点保存, 请求里就带上了 project_id=5 —— 这条任务从此被挪到了
    「钢琴」下, 而界面上从头到尾没人提示过。
    """
    body = live["trainPatchBody"]
    assert live["trainPatches"] == 1, f"应发出一次 PATCH, 实际 {live['trainPatches']}"
    assert body is not None
    assert "project_id" not in body, \
        f"保存时夹带了 project_id={body.get('project_id')!r} —— 原记录没有项目, 不该被改写"
    assert body.get("task_name") == "练琴(改)", f"改名的请求体不对: {body}"


@needs_jsdom
def test_saving_subject_assignment_sends_no_project(live):
    """学科作业保存时也不能带 project_id。

    学科作业按科目归口, 后端也明确禁止挂项目 (BadTaskKindError) ——
    前端要是把它送出去, 家长会看到一句看不懂的"保存失败"。
    """
    body = live["subjectPatchBody"]
    assert live["subjectPatches"] == 1, f"应发出一次 PATCH, 实际 {live['subjectPatches']}"
    assert body is not None
    assert "project_id" not in body, \
        f"学科作业不该带 project_id: {body}"
    assert "subject" not in body, f"只改名字不该夹带 subject: {body}"


@needs_jsdom
def test_existing_project_is_still_shown(live):
    """本来挂着项目的任务, 必须照样带出它自己的项目。

    这是"不再兜默认值"的反面: 不是把所有项目都清掉, 而是**按原值带出**。
    """
    w = live["withProject"]
    assert w["opened"] is True
    assert w["pickerValue"] == "6", \
        f"应带出原项目 id=6, 实际 {w['pickerValue']!r}"
    assert w["pickerLabel"] == "国际象棋", \
        f"应显示原项目名, 实际 {w['pickerLabel']!r}"
    assert w["pickerIsEmpty"] is False
