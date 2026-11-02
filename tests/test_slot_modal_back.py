"""上课时段弹窗的次要键必须是「返回」而不是「取消」 (2026-10-31 首版,
2026-11-02 改版).

用户截图反馈: 「新增上课时段」弹窗底部并排摆着「取消」和「新增」,
圈出来的「取消」让他以为点它等于放弃这次新增。他要的是"返回上一层"。

为什么不是「取消」:
  这个弹窗是「固定课表」的下层 —— 从课表管理处点「＋新增上课时段」进来的。
  上一层(课表管理的地方)还在, 所以次要键的自然语义是"退回上一层", 而不是
  "关掉整个流程"。写成「取消」会让人以为整件事被撤销了, 下次进来又得
  重新找一遍课表。

同一个层级关系也出现在「周计划项」弹窗上, 一起锁住。

2026-11-02 的变化 (周计划页改版):
  课表列表从**弹窗**搬到了**页内分区** (#week-sched 的时间轴)。
  于是「上一层」不再是一个弹窗层, 而是页面里的那个分区。
  次要键的语义没变 —— 仍是「返回上一层」, 只是上一层现在在页面上:
  点它 = 关窗 + 高亮页内分区, 让家长知道自己退回到了哪里。

这里锁五件事:
  1. 新增/改上课时段的次要键文案是「返回」, 不是「取消」
  2. 点它 = 关闭弹窗并高亮页内课表分区 (不是"关掉整个流程")
  3. Esc / 点遮罩 走同一条路
  4. 课表分区在页面里, 点负荷卡的「课表(分) · 管理」不该再开弹窗
  5. 删除功能随这条时段进到编辑弹窗里 (列表里的「删」按钮没有了)
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
    assert "flashScheduleSection" in block, "点「返回」应高亮页内课表分区"


def test_slot_modal_has_delete_button(app_js):
    """删除功能随这条时段进到编辑弹窗 —— 列表里原来的「删」按钮没有了."""
    code = _code(app_js)
    block = code.split("async function openScheduleSlotModal(")[1].split("\n}\n")[0]
    assert "slot-del" in block, "编辑弹窗里要有删除按钮"
    assert "删除这个时段" in block
    assert "/schedule-slots/${slot.id}" in block, "删除要走 DELETE /api/schedule-slots/{id}"


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


def test_schedule_manager_no_longer_opens_a_modal(app_js):
    """课表列表搬进页面后, 「管理课表」不该再开弹窗。

    列表已经在页面上当分区了, 再开弹窗等于把同一份内容弹两遍。
    """
    code = _code(app_js)
    block = code.split("function openScheduleManager(")[1].split("\n}\n")[0]
    assert "modal-content" not in block, "openScheduleManager 不该再往弹窗里塞课表列表"
    assert "week-sched" in block, "应该定位到页内课表分区"
    assert "function flashScheduleSection()" in code


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
// charts.js 必须先于 app.js 加载 (与 index.html 的顺序一致): 复盘页的图
// 走 charts.js 的封装。jsdom 没有 canvas 包, 图表会走"退化成表格"那条
// 降级路径 —— 这正是降级逻辑要守的行为, 所以这里**不**加载 echarts.min.js。
w.eval(fs.readFileSync(require('path').join(require('path').dirname(appJsPath), 'charts.js'), 'utf8'));
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
const flashed = () => !!doc.querySelector('#week-sched.is-flash');

(async () => {
  const out = { steps: [] };
  w.__w();
  await wait(400);

  // 0. 课表分区在页内, 不在弹窗里
  out.hasSection = !!doc.getElementById('week-sched');
  out.blockCount = doc.querySelectorAll('.week-tl-blk').length;
  out.modalOpenAtStart = !modal.classList.contains('hidden');

  // 1. 点负荷卡的「课表(分) · 管理」-> 定位到分区, 不开弹窗
  click(doc.getElementById('week-manage-slots'));
  await wait(150);
  out.afterManageClick = state();
  out.flashedAfterManage = flashed();

  // 2. 分区里的「＋ 新增时段」
  click(doc.querySelector('#week-sched #slot-add'));
  await wait(150);
  out.add = state();

  // 3. 次要键 -> 关窗 + 高亮页内分区
  click(cancelBtn);
  await wait(150);
  out.afterBackFromAdd = state();
  out.flashedAfterBack = flashed();

  // 4. 点课块 -> 改上课时段 (同一个弹窗)
  const blk = doc.querySelector('.week-tl-blk');
  out.hasBlock = !!blk;
  if (blk) {
    click(blk);
    await wait(150);
    out.edit = state();
    out.hasDelete = !!doc.getElementById('slot-del');
    click(cancelBtn);
    await wait(150);
    out.afterBackFromEdit = state();
  }

  // 5. Esc 也关窗
  click(doc.querySelector('#week-sched #slot-add'));
  await wait(150);
  key(doc, 'Escape');
  await wait(150);
  out.afterEsc = state();

  // 6. 点遮罩也关窗
  click(doc.querySelector('#week-sched #slot-add'));
  await wait(150);
  click(modal);
  await wait(150);
  out.afterOverlay = state();

  // 7. 文案不被上一个弹窗污染: 再进一次仍是「返回」
  click(doc.querySelector('#week-sched #slot-add'));
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


# ====== 课表分区在页内 ======

@needs_jsdom
def test_schedule_section_is_in_the_page(live):
    """课表是页内分区, 不是弹窗。"""
    assert live["hasSection"] is True, "缺少页内课表分区 #week-sched"
    assert live["blockCount"] == 1, "课时段应画成时间轴上的色块"
    assert live["modalOpenAtStart"] is False, "进周计划页不该弹任何窗"


@needs_jsdom
def test_manage_button_does_not_open_a_modal(live):
    """点「课表(分) · 管理」定位到分区, 不开弹窗。"""
    s = live["afterManageClick"]
    assert s["open"] is False, "不该再开课表列表弹窗"
    assert live["flashedAfterManage"] is True, "应该高亮页内课表分区"


# ====== 新增 / 编辑弹窗 ======

@needs_jsdom
def test_add_slot_modal_cancel_says_return(live):
    """「新增上课时段」的次要键写「返回」, 不是「取消」."""
    s = live["add"]
    assert s["title"] == "新增上课时段"
    assert s["cancelText"] == "返回", "次要键应写「返回」(退回上一层), 不是「取消」"
    assert s["cancelHidden"] is False
    assert s["okText"] == "新增"


@needs_jsdom
def test_return_closes_modal_and_flashes_section(live):
    """点「返回」= 关闭弹窗 + 高亮页内课表分区。

    上一层(课表管理的地方)从弹窗变成了页内分区, 所以「返回」现在要
    关掉弹窗 —— 但必须让家长看见自己退回到了哪里, 否则像是整件事被撤销。
    """
    s = live["afterBackFromAdd"]
    assert s["open"] is False, "「返回」应关闭弹窗 (上一层已是页面内的分区)"
    assert live["flashedAfterBack"] is True, "「返回」应高亮页内课表分区"


@needs_jsdom
def test_edit_slot_modal_cancel_says_return(live):
    """「改上课时段」与新增是同一个弹窗, 次要键同样写「返回」."""
    assert live["hasBlock"] is True, "时间轴上应有可点的课块"
    s = live["edit"]
    assert s["title"] == "改上课时段"
    assert s["cancelText"] == "返回"
    assert s["cancelHidden"] is False
    assert s["okText"] == "保存"


@needs_jsdom
def test_edit_modal_has_delete_button(live):
    """列表里的「删」按钮没有了, 删除跟着时段进到编辑弹窗。"""
    assert live["hasDelete"] is True, "编辑弹窗里应有删除按钮"


@needs_jsdom
def test_return_from_edit_closes_modal(live):
    s = live["afterBackFromEdit"]
    assert s["open"] is False


@needs_jsdom
def test_escape_closes_modal(live):
    """按 Esc 走同一条路: 关窗 + 回到页内分区。"""
    s = live["afterEsc"]
    assert s["open"] is False


@needs_jsdom
def test_overlay_click_closes_modal(live):
    s = live["afterOverlay"]
    assert s["open"] is False


@needs_jsdom
def test_cancel_text_not_polluted_by_previous_modal(live):
    """反复进出之后文案仍是「返回」, 不残留别的弹窗的状态."""
    s = live["addAgain"]
    assert s["cancelText"] == "返回"
    assert s["okText"] == "新增"
