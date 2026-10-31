"""弹窗底部按钮语义测试 (2026-09-28).

用户截图反馈: 点「原文」弹出的弹窗里, 「取消」和「确定」两个按键功能一样,
都是关闭弹窗, 建议合并为一个「返回」。

这个改动有个容易踩的坑: 弹窗是共用的。
  - 原文弹窗    —— 只读, 两键同功能 → 应只留「返回」
  - 手动添加弹窗 —— 有输入, 「取消」(放弃)与「确定」(提交)语义不同 → 必须都留

如果只在原文弹窗里隐藏取消按钮而不在手动添加里恢复, 家长先看原文再手动添加,
就会看到一个没有取消按钮、写着「返回」的提交弹窗。

这些用例锁住两件事:
  1. 只读弹窗只显示一个「返回」
  2. 状态不互相污染, 且「确定」仍能真正提交
  3. .hidden 这个类真的能藏住东西 (之前只有 .modal.hidden / .toast.hidden,
     通用类没定义, 于是只读弹窗里「取消」照样显示, 两颗键并排出现)
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


# ====== 静态检查 ======

def test_set_modal_actions_exists(app_js):
    assert "function setModalActions(" in app_js


def test_both_modes_supported(app_js):
    code = _code(app_js)
    assert "mode === 'close'" in code
    assert "mode: 'confirm'" in code or "mode: 'close'" in code


def test_source_modal_uses_close_mode(app_js):
    """原文弹窗必须走只读模式。"""
    code = _code(app_js)
    show = code.split("function showSource")[1] if "function showSource" in code else code
    assert "setModalActions({ mode: 'close' })" in show, \
        "原文弹窗应调用 close 模式, 而不是直接给 onclick 赋关闭函数"


def test_manual_modal_uses_confirm_mode(app_js):
    """手动添加必须走 confirm 模式并显式重置状态。"""
    code = _code(app_js)
    assert "mode: 'confirm'" in code
    assert "onOk: async () =>" in code


def test_manual_modal_does_not_assign_onclick_directly(app_js):
    """旧写法直接赋 modal-ok.onclick, 不会重置按钮文案与 class。"""
    code = _code(app_js)
    assert not re.search(r"getElementById\('modal-ok'\)\.onclick = async", code), \
        "手动添加应改用 setModalActions, 否则只读模式的状态会残留"


def test_close_mode_hides_cancel(app_js):
    code = _code(app_js)
    block = code.split("function setModalActions(")[1].split("\n}")[0]
    assert "cancel.classList.add('hidden')" in block


def test_close_mode_renames_to_return(app_js):
    code = _code(app_js)
    block = code.split("function setModalActions(")[1].split("\n}")[0]
    assert "ok.textContent = '返回'" in block


def test_close_mode_button_looks_like_a_button(app_js):
    """「返回」要用 secondary 而不是 ghost。

    ghost 是透明底 + 透明边框, 渲染出来是一行浅灰文字, 家长看不出那里
    有一颗可按的键 (2026-10-30 用户截图反馈)。
    """
    code = _code(app_js)
    block = code.split("function setModalActions(")[1].split("\n}")[0]
    close_branch = block.split("mode === 'close'")[1].split("} else {")[0]
    assert "secondary" in close_branch, "「返回」应带 secondary, 才有边框和底色"
    assert "'ghost'" not in close_branch, "「返回」不该再用 ghost (透明底透明边)"


def test_confirm_mode_restores_everything(app_js):
    """confirm 分支必须把 close 分支改过的东西全部还原。"""
    code = _code(app_js)
    block = code.split("function setModalActions(")[1].split("\n}")[0]
    for frag in ("cancel.classList.remove('hidden')",
                 "ok.textContent = '确定'",
                 "ok.classList.add('primary')",
                 "ok.classList.remove('ghost', 'modal-solo-btn')"):
        assert frag in block, f"confirm 分支缺少还原: {frag}"


def test_no_duplicate_ok_handler(app_js):
    """onclick 只在 setModalActions 里赋值一次, 避免两处互相覆盖。"""
    code = _code(app_js)
    assert len(re.findall(r"getElementById\('modal-ok'\)\.onclick", code)) == 0, \
        "modal-ok.onclick 应只在 setModalActions 内部赋值"


def test_solo_button_width(styles):
    """单按钮时给 min-width, 否则会被 flex 拉成一整条。"""
    assert re.search(r"\.modal-solo-btn \{ min-width: \d+px", styles)


# ====== 运行时 (jsdom) ======

HARNESS = r"""
const http = require('http');
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const ITEM = {
  id: 1, subject: '数学', task_name: '第一单元综合复习', due_date: '2026-09-28',
  deadline_time: null, is_optional: false, status: 'todo', est_minutes: 30,
  actual_minutes: null, task_type: 'other', confidence: 'low', source_span: '第一单元综合复习',
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
const posted = [];
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/board/today') || s.includes('/board?date=')) return ok(BOARD);
  if (s.includes('/board/dates')) return ok({ dates: [{ date: '2026-09-28', total: 1 }] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (s.includes('/assignments/batch') && o && o.method === 'POST') {
    posted.push(JSON.parse(o.body)); return ok({ created_ids: [99] });
  }
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__b = () => switchTab("board");');

const okBtn = doc.getElementById('modal-ok');
const cancelBtn = doc.getElementById('modal-cancel');
const modal = doc.getElementById('modal');
const state = () => ({
  okText: okBtn.textContent.trim(),
  cancelHidden: cancelBtn.classList.contains('hidden'),
  okGhost: okBtn.classList.contains('ghost'),
  okSecondary: okBtn.classList.contains('secondary'),
  okPrimary: okBtn.classList.contains('primary'),
  okSolo: okBtn.classList.contains('modal-solo-btn'),
  visible: [okBtn, cancelBtn].filter(b => !b.classList.contains('hidden')).length,
  modalOpen: !modal.classList.contains('hidden'),
});
const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

(async () => {
  const out = { steps: [] };
  w.__b();
  await new Promise(r => setTimeout(r, 300));

  const srcLink = [...doc.querySelectorAll('.source-link')][0];
  out.hasSourceLink = !!srcLink;

  // 1. 打开原文
  click(srcLink);
  await new Promise(r => setTimeout(r, 120));
  out.source = state();
  out.sourceTitle = doc.getElementById('modal-title').textContent;
  out.sourceBody = doc.getElementById('modal-content').textContent;
  // 点返回关闭
  click(okBtn);
  await new Promise(r => setTimeout(r, 80));
  out.afterReturn = state();

  // 2. 再开一次(幂等)
  click(srcLink);
  await new Promise(r => setTimeout(r, 120));
  out.source2 = state();
  click(okBtn);
  await new Promise(r => setTimeout(r, 80));

  // 3. 看过原文后打开手动添加 —— 关键: 状态必须还原
  const manualBtn = doc.getElementById('btn-add-manual');
  out.hasManualBtn = !!manualBtn;
  click(manualBtn);
  await new Promise(r => setTimeout(r, 150));
  out.manual = state();
  out.hasNameInput = !!doc.getElementById('m-name');
  out.dueHasPicker = !!(doc.getElementById('m-due') && doc.getElementById('m-due')._fpInstance);

  // 4. 确定能真正提交
  doc.getElementById('m-subject').value = '语文';
  doc.getElementById('m-name').value = '测试任务';
  doc.getElementById('m-due').value = '2026-09-28';
  doc.getElementById('m-est').value = '15';
  click(okBtn);
  await new Promise(r => setTimeout(r, 200));
  out.postCount = posted.length;
  out.postedTask = posted[0] ? posted[0].items[0].task_name : null;
  out.afterSubmit = state();

  // 5. 取消可用且不误提交
  click(manualBtn);
  await new Promise(r => setTimeout(r, 150));
  click(cancelBtn);
  await new Promise(r => setTimeout(r, 80));
  out.afterCancel = state();
  out.postCountAfterCancel = posted.length;

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_modal_harness.js"
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
def test_source_link_exists(live):
    assert live["hasSourceLink"] is True
    assert live["hasManualBtn"] is True


@needs_jsdom
def test_source_modal_shows_single_return(live):
    s = live["source"]
    assert s["visible"] == 1, "只读弹窗应只有一个按钮"
    assert s["cancelHidden"] is True
    assert s["okText"] == "返回"
    assert s["okPrimary"] is False, "关闭类操作不该用 primary 强调"
    assert s["okSecondary"] is True, "「返回」要像个按键: 有边框有底色"
    assert s["okGhost"] is False, "「返回」不该用 ghost (透明底透明边, 看着像一行字)"
    assert s["okSolo"] is True


@needs_jsdom
def test_source_modal_content_intact(live):
    """合并按钮不能影响弹窗内容。"""
    assert live["sourceTitle"].startswith("原文")
    assert "第一单元综合复习" in live["sourceBody"]


@needs_jsdom
def test_return_closes_modal(live):
    assert live["afterReturn"]["modalOpen"] is False


@needs_jsdom
def test_source_modal_is_idempotent(live):
    s = live["source2"]
    assert s["visible"] == 1
    assert s["okText"] == "返回"


@needs_jsdom
def test_manual_modal_restores_buttons(live):
    """核心回归: 只读模式的状态不能污染输入弹窗。"""
    m = live["manual"]
    assert m["visible"] == 2, "手动添加应有两个按钮"
    assert m["cancelHidden"] is False, "取消按钮必须回来"
    assert m["okText"] == "确定"
    assert m["okPrimary"] is True
    assert m["okGhost"] is False
    assert m["okSecondary"] is False, "「确定」是主操作, 不该再带 secondary 的边框底色"
    assert m["okSolo"] is False


@needs_jsdom
def test_manual_modal_fields_present(live):
    assert live["hasNameInput"] is True
    assert live["dueHasPicker"] is True


@needs_jsdom
def test_confirm_still_submits(live):
    assert live["postCount"] == 1
    assert live["postedTask"] == "测试任务"
    assert live["afterSubmit"]["modalOpen"] is False


@needs_jsdom
def test_cancel_closes_without_submit(live):
    assert live["afterCancel"]["modalOpen"] is False
    assert live["postCountAfterCancel"] == 1, "取消不应触发提交"
