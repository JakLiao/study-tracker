"""危险操作确认弹窗测试 (2026-09-29).

背景: 家长截图批注 —— 点看板上的「删除」弹出的���浏览器原生 confirm 框,
一块系统灰, 按钮顺序随平台变, 和页面完全是两套皮。

这个改动把三处原生弹窗换成了自绘的 #modal:
  - confirm() 删除作业  -> confirmDialog()
  - prompt()  修改用时  -> promptNumber()
  - alert()   设置保存失败 -> toast()

替换掉原生弹窗本身不难, 难的是"Promise 化之后关闭路径还通不通"。
confirmDialog/promptNumber 都返回 Promise, 一旦某条关闭路径没有回调,
调用方的 await 就会永久挂起 —— 页面上表现为"点取消没反应, 也不报错"。
所以下面锁死四件事:
  1. 弹窗长成页面自己的样子(内容/文案/红色确认键), 而不是原生框
  2. 四条关闭路径(取消 / 确定 / Esc / 遮罩)全都能结算 Promise
  3. 焦点默认落在「取消」, 回车不会直接删
  4. 关闭即隐藏, 且不会误发请求
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


def _code(src: str) -> str:
    """剥掉注释, 只留真正执行的代码。"""
    s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))


# ====== 静态检查: 原生弹窗必须全部消失 ======

def test_no_native_confirm(app_js):
    """看板删除不能再走 confirm()。"""
    assert not re.search(r"(?<![.\w])confirm\(", _code(app_js))


def test_no_native_prompt(app_js):
    assert not re.search(r"(?<![.\w])prompt\(", _code(app_js))


def test_no_native_alert(app_js):
    assert not re.search(r"(?<![.\w])alert\(", _code(app_js))


def test_helpers_exist(app_js):
    assert "function confirmDialog(" in app_js
    assert "function promptNumber(" in app_js
    assert "function closeModal(" in app_js


def test_delete_goes_through_confirm_dialog(app_js):
    """删除必须 await 确认结果, 而不是靠原生 confirm 的返回值。"""
    code = _code(app_js)
    del_block = code.split("const delBtn")[1].split("actions.appendChild(delBtn)")[0]
    assert "await confirmDialog(" in del_block
    assert "if (!ok) return;" in del_block
    assert "okText: '删除'" in del_block
    assert "danger: true" in del_block


def test_edit_minutes_goes_through_prompt_number(app_js):
    """改用时改用自绘输入弹窗, 取消时必须能区分 null 与 0。"""
    code = _code(app_js)
    edit_block = code.split("const editBtn = el('<button class=\"ghost\">改用时")[1]
    edit_block = edit_block.split("actions.appendChild(editBtn)")[0]
    assert "await promptNumber(" in edit_block
    # 0 是合法用时, 所以只能用 === null 判取消, 不能用 !n
    assert "if (n === null) return;" in edit_block


def test_close_modal_is_single_exit(app_js):
    """四条关闭路径必须都走同一个出口, 否则 Promise 结算不了。

    2026-10-30: 次要键不再直接调 closeModal, 而是走 modalCancel() ——
    嵌套弹窗(课表时段/周计划项)的次要键是"返回上一层", 只退一层而不是
    关掉整个弹窗。modalCancel() 在没有 onCancel 时仍退回 closeModal(),
    所以四条路径的出口依然是同一个。
    """
    code = _code(app_js)
    assert "document.getElementById('modal-cancel').addEventListener('click', () => {\n  modalCancel();" in code
    # 兜底仍要落到 closeModal, 否则没有 onCancel 的弹窗按 Esc 会关不掉
    assert "const fn = _modalCancel || (() => closeModal());" in code
    assert "e.key !== 'Escape'" in code
    assert "e.target === e.currentTarget" in code
    # 取消只能通过哨兵表达: 各弹窗要的"取消值"类型不同(boolean / null)
    assert "const CANCELLED = Symbol(" in code
    assert "closeModal(result = CANCELLED)" in code


def test_cancel_sentinel_is_translated_per_dialog(app_js):
    """回归: 取消曾被当成 false 传给数字弹窗, 于是「改用时」一取消就存 0。

    两个弹窗对"取消"的返回值要求不同 —— 确认弹窗要 boolean, 数字弹窗要
    null(0 是合法用时, 不能用 falsy 当取消)。closeModal 因此只发哨兵,
    由各自注册的 resolver 翻译。
    """
    code = _code(app_js)
    assert "_modalCloser = (r) => resolve(r === true);" in code
    assert "_modalCloser = (r) => resolve(r === CANCELLED ? null : r);" in code


# ====== 静态检查: 样式 ======

def test_danger_button_is_solid_red(styles):
    """危险确认键要实心红, 且选择器要同时压过 button.primary。"""
    assert re.search(r"button\.primary\.modal-danger \{", styles)
    block = styles.split("button.primary.modal-danger {")[1].split("}")[0]
    assert "background: var(--status-overdue)" in block
    # 描边红会和旁边的取消键糊在一起, 失去"最终决定"的视觉台阶
    assert "border-color" in block


def test_confirm_dialog_is_narrower(styles):
    assert re.search(r"#modal\.confirm-modal \.modal-body \{ max-width: \d+px", styles)


def test_confirm_target_is_styled(styles):
    """被删对象要有独立底色, 否则和说明文字糊在一起。"""
    block = styles.split(".confirm-target {")[1].split("}")[0]
    assert "background: var(--surface-2)" in block
    assert "word-break: break-word" in block, "长任务名不能把弹窗撑破"


def test_prompt_input_is_touch_friendly(styles):
    block = styles.split(".prompt-body input {")[1].split("}")[0]
    assert re.search(r"height: \d+px", block)
    assert "text-align: center" in block


# ====== 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

// status=todo 才渲染删除按钮; actual_minutes 有值才渲染"改用时",
// 两者不冲突, 一条作业就能同时覆盖两个入口。
const ITEM = {
  id: 1, subject: '数学', task_name: '第一单元综合复习', due_date: '2026-09-28',
  deadline_time: null, is_optional: false, status: 'todo', est_minutes: 30,
  actual_minutes: 25, task_type: 'other', confidence: 'low', source_span: '第一单元综合复习',
};
const BOARD = {
  date: '2026-09-28', label: '今天', is_today: true, has_data: true, is_late_night: false,
  by_subject: { 语文: [], 数学: [ITEM], 英语: [] },
  optional_items: [], upcoming_deadlines: [],
  progress: { done: 0, skipped: 0, settled: 0, total: 1, optional: 0, required: 1, pct: 0, est_total: 30 },
};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
const deleted = [];
const patched = [];
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/board/today') || s.includes('/board?date=')) return ok(BOARD);
  if (s.includes('/board/dates')) return ok({ dates: [{ date: '2026-09-28', total: 1 }] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (o && o.method === 'DELETE') { deleted.push(s); return ok({ deleted: 1 }); }
  if (o && o.method === 'PATCH') { patched.push({ url: s, body: JSON.parse(o.body) }); return ok({ ok: 1 }); }
  return ok({});
};
// 一旦还有代码路径没换掉, 这里会直接把 harness 掀翻 —— 比静默返回 undefined 更容易发现
w.confirm = () => { throw new Error('不应调用原生 confirm'); };
w.prompt  = () => { throw new Error('不应调用原生 prompt'); };
w.alert   = () => { throw new Error('不应调用原生 alert'); };
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__b = () => switchTab("board");');

const modal = doc.getElementById('modal');
const okBtn = doc.getElementById('modal-ok');
const cancelBtn = doc.getElementById('modal-cancel');
const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
const wait = ms => new Promise(r => setTimeout(r, ms));
const btnByText = t => [...doc.querySelectorAll('.row-actions button')].find(b => b.textContent.trim() === t);
const state = () => ({
  open: !modal.classList.contains('hidden'),
  narrow: modal.classList.contains('confirm-modal'),
  okText: okBtn.textContent.trim(),
  okDanger: okBtn.classList.contains('modal-danger'),
  okPrimary: okBtn.classList.contains('primary'),
  cancelHidden: cancelBtn.classList.contains('hidden'),
  focus: doc.activeElement && doc.activeElement.id,
});

(async () => {
  const out = {};
  w.__b();
  await wait(300);

  const delBtn = btnByText('删除');
  const editBtn = btnByText('改用时');
  out.hasDelBtn = !!delBtn;
  out.hasEditBtn = !!editBtn;

  // 1. 点删除 -> 自绘确认弹窗
  click(delBtn);
  await wait(80);
  out.confirm = state();
  out.confirmTitle = doc.getElementById('modal-title').textContent;
  out.confirmBody = doc.getElementById('modal-content').textContent;
  out.confirmIcon = !!doc.querySelector('.confirm-body.danger .confirm-icon');
  out.confirmSubject = !!doc.querySelector('.confirm-target .subject-tag');
  out.deleteCallsBeforeConfirm = deleted.length;

  // 2. Esc 关闭 -> 不发请求, Promise 正常结算
  key(doc, 'Escape');
  await wait(120);
  out.afterEsc = state();
  out.deleteCallsAfterEsc = deleted.length;

  // 3. 取消按钮 -> 不发请求
  click(delBtn);
  await wait(80);
  click(cancelBtn);
  await wait(120);
  out.deleteCallsAfterCancel = deleted.length;
  out.afterCancel = state();

  // 4. 点遮罩关闭 -> 不发请求 (点弹窗内容不能关)
  click(delBtn);
  await wait(80);
  click(doc.querySelector('.modal-body h3'));
  await wait(120);
  out.closeOnContentClick = state().open;
  click(modal);
  await wait(120);
  out.deleteCallsAfterOverlay = deleted.length;
  out.afterOverlay = state();

  // 5. 确定删除 -> 发出唯一一次 DELETE
  click(delBtn);
  await wait(80);
  click(okBtn);
  await wait(250);
  out.deleteCalls = deleted.length;
  out.deleteUrl = deleted[0] || null;

  // 6. 改用时 -> 数字输入弹窗, 取消不发 PATCH
  click(btnByText('改用时'));
  await wait(80);
  out.prompt = state();
  out.promptPrefill = doc.getElementById('prompt-num').value;
  out.promptMin = doc.getElementById('prompt-num').min;
  out.promptMax = doc.getElementById('prompt-num').max;
  out.promptFocused = doc.activeElement.id;
  out.patchesBeforeCancel = patched.length;
  click(cancelBtn);
  await wait(120);
  out.patchesAfterCancel = patched.length;

  // 7. 越界值: 弹窗必须留在原地, 而不是静默关掉
  click(btnByText('改用时'));
  await wait(80);
  const num = doc.getElementById('prompt-num');
  num.value = '999';
  click(okBtn);
  await wait(150);
  out.staysOpenOnInvalid = !modal.classList.contains('hidden');
  out.marksInputError = num.classList.contains('error');
  out.patchesAfterInvalid = patched.length;

  // 8. 合法值 + 回车 -> 提交 PATCH
  num.value = '40';
  key(num, 'Enter');
  await wait(250);
  out.patches = patched.length;
  out.patched = patched[0] || null;
  out.closedAfterSubmit = modal.classList.contains('hidden');

  // 9. 窄版样式不能残留到别的弹窗上
  click(btnByText('删除'));
  await wait(80);
  key(doc, 'Escape');
  await wait(100);
  const srcLink = [...doc.querySelectorAll('.source-link')][0];
  if (srcLink) {
    click(srcLink);
    await wait(120);
    out.sourceAfterConfirm = state();
  }

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_confirm_harness.js"
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
def test_both_entry_points_exist(live):
    assert live["hasDelBtn"] is True
    assert live["hasEditBtn"] is True


@needs_jsdom
def test_delete_opens_styled_dialog(live):
    s = live["confirm"]
    assert s["open"] is True
    assert s["narrow"] is True, "确认弹窗应使用窄版布局"
    assert s["okText"] == "删除", "确认键要写明动作, 不能是含糊的「确定」"
    assert s["okDanger"] is True, "不可逆操作的确认键必须是红色"
    assert s["okPrimary"] is True
    assert s["cancelHidden"] is False, "必须有取消才能反悔"


@needs_jsdom
def test_confirm_shows_subject_and_task(live):
    """作业名重复率高, 必须连科目一起显示。"""
    assert "第一单元综合复习" in live["confirmBody"]
    assert live["confirmSubject"] is True
    assert live["confirmIcon"] is True
    assert "删除" in live["confirmTitle"]


@needs_jsdom
def test_focus_defaults_to_cancel(live):
    """回车不能直接删 —— 焦点必须落在取消上。"""
    assert live["confirm"]["focus"] == "modal-cancel"


@needs_jsdom
def test_escape_cancels_without_deleting(live):
    assert live["afterEsc"]["open"] is False
    assert live["deleteCallsAfterEsc"] == 0


@needs_jsdom
def test_cancel_button_does_not_delete(live):
    assert live["deleteCallsAfterCancel"] == 0
    assert live["afterCancel"]["open"] is False


@needs_jsdom
def test_content_click_keeps_dialog_open(live):
    """点弹窗内容(而非遮罩)不能关掉它。"""
    assert live["closeOnContentClick"] is True


@needs_jsdom
def test_overlay_click_cancels_without_deleting(live):
    assert live["deleteCallsAfterOverlay"] == 0
    assert live["afterOverlay"]["open"] is False


@needs_jsdom
def test_confirm_deletes_exactly_once(live):
    """反复确认也不能发出多次 DELETE。"""
    assert live["deleteCalls"] == 1
    assert "/assignments/1" in (live["deleteUrl"] or "")


@needs_jsdom
def test_edit_minutes_opens_prefilled_number_input(live):
    s = live["prompt"]
    assert s["open"] is True
    assert s["narrow"] is True
    assert s["okText"] == "保存"
    assert live["promptPrefill"] == "25", "要带出当前值, 家长是「改」不是「重填」"
    assert live["promptMin"] == "0"
    assert live["promptMax"] == "240"
    assert live["promptFocused"] == "prompt-num", "焦点应直接落在输入框"


@needs_jsdom
def test_edit_minutes_cancel_sends_nothing(live):
    assert live["patchesAfterCancel"] == 0


@needs_jsdom
def test_invalid_minutes_keeps_dialog_open(live):
    """越界不能静默关掉, 否则家长会以为改完了。"""
    assert live["staysOpenOnInvalid"] is True
    assert live["marksInputError"] is True
    assert live["patchesAfterInvalid"] == 0


@needs_jsdom
def test_valid_minutes_submits_on_enter(live):
    assert live["patches"] == 1
    assert live["patched"]["body"]["actual_minutes"] == 40
    assert "/completion" in live["patched"]["url"]
    assert live["closedAfterSubmit"] is True


@needs_jsdom
def test_narrow_style_does_not_leak(live):
    """确认弹窗的窄版样式不能残留到只读弹窗上。"""
    s = live.get("sourceAfterConfirm")
    if s is None:
        pytest.skip("本页没有原文入口, 跳过")
    assert s["narrow"] is False
    assert s["okText"] == "返回"
