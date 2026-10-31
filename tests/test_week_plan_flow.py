"""周计划页: 两条业务流 + 课表入口进负荷格 (2026-10-31)

周计划页原来把三颗按钮平铺在汇总卡下面: 「生成本周任务」「管理课表」
「管理周计划项」。两个问题:

1. 三颗平级按钮看不出先后。「生成本周任务」是第二步(先把周计划项这
   些"每周重复做什么"的模板定下来, 再展开成这周的作业), 家长多半先
   点生成, 才发现没有模板, 白跑一趟。
2. 「管理课表」夹在流程中间像是流程的一环, 其实课外课表是另一件事
   (只影响负荷统计, 不参与生成), 不该占一个流程按钮位。

改法:
- 「周计划项 → 生成本周任务」画成一条带序号和箭头的两步流程
- 课表入口挪进上面的「课表(分)」格子 —— 它本来就只影响那个数字
- 「待生成」只算今天及以后: 生成动作不会回填过去的日期, 把过去的
  待生成分钟数算进来会让提示永远显示有活可干, 却点了没反应

这个用例守: 结构(序号/箭头/入口位置)、旧容器不再存在、以及"待生成"
的口径。
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
        subprocess.run(["node", "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False


def _app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _code(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))


# ====== 1. 代码层面 ======

def test_old_flat_button_bar_removed():
    """旧的三平级按钮容器不能再出现 —— 留着一份就等于两条入口并存。"""
    code = _code(_app_js())
    assert "week-actions" not in code, "旧的平级按钮容器 (week-actions) 应已移除"


def test_two_step_flow_built_with_numbers_and_arrow():
    """两步流程要画出序号与箭头, 否则仍然读不出先后。"""
    code = _code(_app_js())
    block = re.search(r"function renderWeekPlan\(root, w\) \{(.*?)\n\}", code, re.S)
    assert block, "缺少 renderWeekPlan"
    body = block.group(1)
    assert 'class="week-flow"' in body, "缺少两步流程容器"
    assert 'class="week-flow-no"' in body, "缺少步骤序号"
    assert 'class="week-flow-arrow"' in body, "缺少步骤箭头"
    # 按模板里的 id 排, 不能用 querySelector 那两行: 那里 generate 在前
    # (只是绑定顺序), 模板里的按钮顺序才是家长看到的先后。
    assert body.index('id="week-manage-plans"') < body.index('id="week-generate"'), \
        "周计划项(第 1 步)必须排在生成本周任务(第 2 步)前面"


def test_schedule_entry_moved_into_load_cell():
    """课表入口挪进「课表(分)」格子 —— 它只影响那个数字。"""
    code = _code(_app_js())
    block = re.search(r"function renderWeekPlan\(root, w\) \{(.*?)\n\}", code, re.S)
    body = block.group(1)
    m = re.search(r'<button[^>]*id="week-manage-slots"[^>]*>', body)
    assert m, "课表入口应是按钮"
    assert "week-stat" in m.group(0), "课表入口应是「课表(分)」那一格"
    # 课表入口不该再占流程里的一个按钮位
    assert body.count('id="week-manage-slots"') == 1, "课表入口只能有一处"


def test_pending_only_counts_today_and_later():
    """「待生成」只算今天及以后 —— 生成不会回填过去的日期。"""
    code = _code(_app_js())
    block = re.search(r"function renderWeekPlan\(root, w\) \{(.*?)\n\}", code, re.S)
    body = block.group(1)
    m = re.search(r"const pending = w\.days\s*\.filter\((.*?)\)\s*\.reduce\(", body, re.S)
    assert m, "待生成分钟数必须先按日期过滤"
    assert "w.today" in m.group(1), "过滤条件必须以今天为界 (w.today)"
    assert "plan_pending_minutes" in body, "待生成应按 plan_pending_minutes 累计"


# ====== 2. 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const PROJECT = {
  id: 1, name: '钢琴', project_type: 'piano', coach_name: null,
  schedule_note: null, status: 'active', created_at: '2026-09-28T00:00:00',
  updated_at: '2026-09-28T00:00:00',
};
const SLOT = {
  id: 11, project_id: 1, project_name: '钢琴', weekday: 2,
  start_time: '16:30', end_time: '18:00', note: null, minutes: 90,
};
const PLAN = {
  id: 5, project_id: 1, project_name: '钢琴', subject: '语文',
  task_kind: 'subject', task_name: '练字', weekdays: [0, 2, 4],
  est_minutes: 20, active: true,
};

function day(date, pending) {
  return {
    date, weekday: 0, label: '周一', is_today: false, is_past: false,
    slots: [], slot_minutes: 0, plans: [], plan_pending_minutes: pending,
    assignments: { total: 0, done: 0, pending: 0, remaining_minutes: 0, actual_minutes: 0 },
    load_minutes: pending, limit_minutes: 120, overloaded: false, conflicts: [],
  };
}

// 今天=2026-10-31(周六), 本周周一=2026-10-26。
// 过去的周一(10-26)还有 30 分没生成 —— 那部分不该进「待生成」。
const WEEK = {
  week_start: '2026-10-26', week_end: '2026-11-01', today: '2026-10-31',
  daily_load_limit: 120,
  days: [
    day('2026-10-26', 30),   // 过去: 30 分
    day('2026-10-27', 30),   // 过去: 30 分
    day('2026-10-28', 30),   // 过去: 30 分
    day('2026-10-29', 30),   // 过去: 30 分
    day('2026-10-30', 30),   // 过去: 30 分
    day('2026-10-31', 20),   // 今天: 20 分
    day('2026-11-01', 40),   // 明天: 40 分
  ],
  totals: { slot_minutes: 90, remaining_minutes: 0, plan_pending_minutes: 210,
            load_minutes: 210, overloaded_days: 0, conflict_days: 0 },
  slots: [SLOT], plan_items: [PLAN],
};

const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/#/week',
  runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
w.fetch = async (u) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/week-plan?')) return ok(WEEK);
  if (s.includes('/week-plan')) return ok(WEEK);
  if (s.includes('/schedule-slots')) return ok({ items: [SLOT] });
  if (s.includes('/projects')) return ok({ items: [PROJECT] });
  if (s.includes('/project-templates')) return ok({ items: [] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8'));

(async () => {
  const out = {};
  await new Promise(r => setTimeout(r, 500));

  out.oldBarCount = doc.querySelectorAll('.week-actions').length;
  out.flowCount = doc.querySelectorAll('.week-flow').length;
  out.stepNumbers = [...doc.querySelectorAll('.week-flow-no')].map(e => e.textContent.trim());
  out.arrowCount = doc.querySelectorAll('.week-flow-arrow').length;
  out.flowHints = [...doc.querySelectorAll('.week-flow-hint')].map(e => e.textContent.trim());

  const slots = doc.getElementById('week-manage-slots');
  out.slotsTag = slots ? slots.tagName : null;
  out.slotsInLoadCell = !!(slots && slots.closest('.week-stats'));
  out.slotsInFlow = !!(slots && slots.closest('.week-flow'));
  out.hasPlansBtn = !!doc.getElementById('week-manage-plans');
  out.hasGenerateBtn = !!doc.getElementById('week-generate');
  out.planStepIndex = (() => {
    const flow = doc.querySelector('.week-flow');
    if (!flow) return -1;
    const kids = [...flow.querySelectorAll('.week-flow-step')];
    return kids.findIndex(k => k.querySelector('#week-manage-plans'));
  })();

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_week_flow_harness.js"
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
def test_old_bar_gone(live):
    assert live["oldBarCount"] == 0, "旧的三平级按钮容器还在"


@needs_jsdom
def test_flow_exists_with_two_numbered_steps(live):
    assert live["flowCount"] == 1, "缺少两步流程"
    assert live["stepNumbers"] == ["1", "2"], live["stepNumbers"]
    assert live["arrowCount"] == 1, "缺少步骤箭头"


@needs_jsdom
def test_schedule_entry_is_load_cell_button(live):
    assert live["slotsTag"] == "BUTTON", live["slotsTag"]
    assert live["slotsInLoadCell"] is True, "课表入口应在「课表(分)」格子里"
    assert live["slotsInFlow"] is False, "课表入口不该占流程里的按钮位"


@needs_jsdom
def test_flow_has_both_step_buttons_in_order(live):
    assert live["hasPlansBtn"] is True
    assert live["hasGenerateBtn"] is True
    assert live["planStepIndex"] == 0, "周计划项必须是第 1 步"


@needs_jsdom
def test_pending_hint_excludes_past_days(live):
    """「待生成」只算今天及以后: 过去 5 天共 150 分不该进来。"""
    hints = " ".join(live["flowHints"])
    assert "待生成 60 分" in hints, hints
    assert "210" not in hints, hints
