"""上课时段弹窗的次要键必须是「返回」而不是「取消」 (2026-10-31).

用户截图反馈: 「新增上课时段」弹窗底部并排摆着「取消」和「新增」,
圈出来的「取消」让他以为点它等于放弃这次新增。他要的是"返回上一层"。

为什么不是「取消」:
  这个弹窗是「固定课表」列表的下层 —— 从列表点「＋新增上课时段」进来的。
  上一层(课表列表)还在, 所以次要键的自然语义是"退回上一层", 而不是
  "关掉整个流程"。写成「取消」会让人以为整件事被撤销了, 下次进来又得
  从周计划页重新点一遍「管理课表」。

同一个层级关系也出现在「周计划项」弹窗上, 一起锁住。

这里锁三件事:
  1. 新增/改上课时段的次要键文案是「返回」, 不是「取消」
  2. 点它退回上一层 (课表列表), 弹窗不关闭、标题回到「固定课表」
  3. Esc / 点遮罩也退回上一层 —— 按键写着「返回」, 按 Esc 却把整个弹窗
     关掉, 一样会让人以为按错了
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


def _code(src: str) -> str:
    s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))


# ====== 静态检查 ======

def test_set_modal_actions_supports_cancel_text(app_js):
    """setModalActions 必须支持自定义次要键文案与行为, 否则没法表达"返回上一层"."""
    code = _code(app_js)
    assert "cancelText = '取消'" in code, "cancelText 应有默认值「取消」"
    assert "cancel.textContent = cancelText;" in code
    assert "onCancel = null" in code
    assert "_modalCancel = onCancel;" in code


def test_slot_modal_uses_return_semantics(app_js):
    """新增/改上课时段走同一个 openScheduleSlotModal, 次要键是「返回」."""
    code = _code(app_js)
    block = code.split("async function openScheduleSlotModal(")[1].split("\n}\n")[0]
    assert "cancelText: '返回'" in block, "上课时段弹窗的次要键应写「返回」"
    assert "onCancel: () => openScheduleManager()" in block, "点「返回」应退回课表列表层"


def test_plan_item_modal_uses_return_semantics(app_js):
    """周计划项弹窗是同一个层级关系, 次要键也要是「返回」."""
    code = _code(app_js)
    block = code.split("function openPlanItemModal(")[1].split("\n}\n")[0]
    assert "cancelText: '返回'" in block
    assert "onCancel: () => openPlanItemManager()" in block


def test_plan_item_save_returns_to_list_level(app_js):
    """保存周计划项后回到列表层, 与课表时段同构。

    原来这里调 closeModal() 直接关掉整个弹窗, 家长刚加的那条计划项看不见,
    只能重新点一次「周计划项」才找得到。课表时段那边已经是"存完回列表",
    两个兄弟弹窗不该一个回列表一个关窗口。
    """
    code = _code(app_js)
    block = code.split("function openPlanItemModal(")[1].split("\n}\n")[0]
    assert "openPlanItemManager();" in block, "保存后应回到周计划项列表层"
    assert "closeModal();" not in block, "保存后不该把整个弹窗关掉"


def test_modal_cancel_goes_through_modal_cancel(app_js):
    """次要键必须走 modalCancel(), 才能让 onCancel 生效 (关整个弹窗就没退一层了)."""
    code = _code(app_js)
    assert "document.getElementById('modal-cancel').addEventListener('click', () => {\n  modalCancel();\n});" in code
    assert "function modalCancel()" in code
    # Esc 与遮罩也走同一出口
    assert "modalCancel();" in code


# ====== 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [basePath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const PROJECT = {
  id: 1, name: '钢琴', project_type: 'piano', coach_name: null,
  schedule_note: null, status: 'active', created_at: '2026-09-28T00:00:00',
  updated_at: '2026-09-28T00:00:00',
};
const SLOT = {
  id: 11, project_id: 1, project_name: '钢琴', weekday: 2,
  start_time: '16:30', end_time: '18:00', note: null, minutes: 90,
};
const DAY = {
  date: '2026-09-28', weekday: 0, label: '周一', is_today: true, is_past: false,
  slots: [], slot_minutes: 0, plans: [], plan_pending_minutes: 0,
  assignments: { total: 0, done: 0, pending: 0, remaining_minutes: 0, actual_minutes: 0 },
  load_minutes: 0, limit_minutes: 120, overloaded: false, conflicts: [],
};
const WEEK = {
  week_start: '2026-09-28', week_end: '2026-10-04', today: '2026-09-28',
  daily_load_limit: 120, days: [DAY, DAY, DAY, DAY, DAY, DAY, DAY],
  totals: { slot_minutes: 0, remaining_minutes: 0, plan_pending_minutes: 0,
            load_minutes: 0, overloaded_days: 0, conflict_days: 0 },
  slots: [SLOT], plan_items: [],
};

const dom = new JSDOM(fs.readFileSync(basePath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/week-plan')) return ok(WEEK);
  if (s.includes('/schedule-slots')) return ok({ items: [SLOT] });
  if (s.includes('/projects')) return ok({ items: [PROJECT] });
  if (s.includes('/project-templates')) return ok({ items: [] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__w = () => switchTab("week");');

const modal = doc.getElementById('modal');
const okBtn = doc.getElementById('modal-ok');
const cancelBtn = doc.getElementById('modal-cancel');
const click = el => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const key = (el, k) => el.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
const wait = ms => new Promise(r => setTimeout(r, ms));
const state = () => ({
  title: doc.getElementById('modal-title').textContent,
  okText: okBtn.textContent.trim(),
  cancelText: cancelBtn.textContent.trim(),
  cancelHidden: cancelBtn.classList.contains('hidden'),
  open: !modal.classList.contains('hidden'),
});

(async () => {
  const out = { steps: [] };
  w.__w();
  await wait(400);

  // 进「固定课表」列表层
  click(doc.getElementById('week-manage-slots'));
  await wait(150);
  out.list = state();

  // 1. 「+ 新增上课时段」这一层
  click(doc.getElementById('slot-add'));
  await wait(150);
  out.add = state();

  // 2. 点次要键 -> 退回上一层 (课表列表), 弹窗不关
  click(cancelBtn);
  await wait(150);
  out.afterBackFromAdd = state();

  // 3. 「改上课时段」这一层
  const editBtn = doc.querySelector('#modal-content [data-op="edit"]');
  out.hasEditBtn = !!editBtn;
  if (editBtn) {
    click(editBtn);
    await wait(150);
    out.edit = state();
    // 4. 点次要键 -> 同样退回上一层
    click(cancelBtn);
    await wait(150);
    out.afterBackFromEdit = state();
  }

  // 5. Esc 也只退一层, 不是关掉整个弹窗
  click(doc.getElementById('slot-add'));
  await wait(150);
  key(doc, 'Escape');
  await wait(150);
  out.afterEsc = state();

  // 6. 点遮罩也只退一层
  click(doc.getElementById('slot-add'));
  await wait(150);
  click(modal);
  await wait(150);
  out.afterOverlay = state();

  // 7. 文案不被上一个弹窗污染: 再进一次仍是「返回」
  click(doc.getElementById('slot-add'));
  await wait(150);
  out.addAgain = state();

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_slot_modal_harness.js"
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
def test_list_level_has_single_return(live):
    """课表列表层是只读弹窗: 只留一颗「返回」, 退出到周计划页."""
    s = live["list"]
    assert s["title"] == "固定课表"
    assert s["cancelHidden"] is True
    assert s["okText"] == "返回"


@needs_jsdom
def test_add_slot_modal_cancel_says_return(live):
    """「新增上课时段」的次要键写「返回」, 不是「取消」."""
    s = live["add"]
    assert s["title"] == "新增上课时段"
    assert s["cancelText"] == "返回", "次要键应写「返回」(退回上一层), 不是「取消」"
    assert s["cancelHidden"] is False
    assert s["okText"] == "新增"


@needs_jsdom
def test_return_goes_back_to_list_level(live):
    """点「返回」退回课表列表, 弹窗不关闭 —— 上一层还在, 不用重进."""
    s = live["afterBackFromAdd"]
    assert s["open"] is True, "退回上一层而不是关掉整个弹窗"
    assert s["title"] == "固定课表"


@needs_jsdom
def test_edit_slot_modal_cancel_says_return(live):
    """「改上课时段」与新增是同一个弹窗, 次要键同样写「返回」."""
    assert live["hasEditBtn"] is True
    s = live["edit"]
    assert s["title"] == "改上课时段"
    assert s["cancelText"] == "返回"
    assert s["cancelHidden"] is False
    assert s["okText"] == "保存"


@needs_jsdom
def test_return_from_edit_goes_back_to_list_level(live):
    s = live["afterBackFromEdit"]
    assert s["open"] is True
    assert s["title"] == "固定课表"


@needs_jsdom
def test_escape_goes_back_one_level_only(live):
    """按 Esc 只退一层: 写着「返回」就该只回上一层, 而不是关掉整个弹窗."""
    s = live["afterEsc"]
    assert s["open"] is True
    assert s["title"] == "固定课表"


@needs_jsdom
def test_overlay_click_goes_back_one_level_only(live):
    s = live["afterOverlay"]
    assert s["open"] is True
    assert s["title"] == "固定课表"


@needs_jsdom
def test_cancel_text_not_polluted_by_previous_modal(live):
    """反复进出之后文案仍是「返回」, 不残留别的弹窗的状态."""
    s = live["addAgain"]
    assert s["cancelText"] == "返回"
    assert s["okText"] == "新增"
