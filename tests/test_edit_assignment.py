"""编辑已入库作业 (PATCH /api/assignments/{id}) 的测试.

需求: 家长想改某条作业的描述或预计时间 —— 后端接口早就有, 但界面上没有
任何入口, 于是只能删掉重录, 而已完成的作业还被禁止删除(4010)。本文件
锁住两件事:

1. PATCH 语义必须分清"没传"和"传了 null"
   ---------------------------------------------------------
   这是这个接口最容易写错、且错了最难发现的地方。早期实现一律用
   `if edit.X is not None` 判断要改哪些列, 于是"清空截止时间"永远传不进去:
   家长在弹窗里删掉截止时间, 前端发 deadline_time: null, 服务端当成
   "这个字段没传"直接跳过, 弹窗照常关闭、toast 照常提示"已保存",
   刷新后截止时间又原样回来了 —— 界面在骗人。
   判定依据必须是 pydantic 的 model_fields_set(字段是否出现在请求里),
   而不是值是否为 None。

2. 改字段不能破坏既有不变量
   ---------------------------------------------------------
   归属日与截止时间的先后关系在 insert 时校验过, edit 也必须校验:
   只改 due_date 而不动 deadline_time 时, 新的归属日可能已经晚于原截止
   时间, 这条作业从此变成"截止在归属日之前"的矛盾数据。

前端契约与 jsdom 运行时行为见文件末尾。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _add_one(app_client, **overrides):
    payload = {
        "batch_id": None,
        "items": [{
            "subject": "语文",
            "task_name": "背诵古诗两首",
            "due_date": "2026-09-21",
            "deadline_time": None,
            "is_optional": False,
            "est_minutes": 20,
            "task_type": "背诵",
            "source_span": "(测试)",
            "confidence": "high",
        }],
    }
    payload["items"][0].update(overrides)
    r = app_client.post("/api/assignments/batch", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _get(app_client, aid, date_="2026-09-21"):
    items = app_client.get(f"/api/assignments?date={date_}").json()["items"]
    return next(i for i in items if i["id"] == aid)


# ====== 1. 改描述 / 改预计时间 (用户最初的需求) ======

def test_edit_task_name(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={"task_name": "背诵古诗三首"})
    assert r.status_code == 200
    assert _get(app_client, aid)["task_name"] == "背诵古诗三首"


def test_edit_est_minutes(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={"est_minutes": 45})
    assert r.status_code == 200
    assert _get(app_client, aid)["est_minutes"] == 45


def test_edit_both_at_once(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={
        "task_name": "抄写生字 2 遍",
        "est_minutes": 30,
    })
    assert r.status_code == 200
    got = _get(app_client, aid)
    assert got["task_name"] == "抄写生字 2 遍"
    assert got["est_minutes"] == 30


def test_edit_trims_task_name_whitespace(app_client):
    """'  古诗  ' 应存成 '古诗'。

    不去空白的话, 归一化去重(dedup_fingerprint)会把 '  古诗  ' 与
    '古诗' 判成两条不同的作业, 重复录入就绕过了去重。
    """
    aid = _add_one(app_client)
    app_client.patch(f"/api/assignments/{aid}", json={"task_name": "  背诵古诗两首  "})
    assert _get(app_client, aid)["task_name"] == "背诵古诗两首"


def test_edit_blank_task_name_rejected(app_client):
    """只含空白的任务名必须拒掉。

    pydantic 的 min_length=1 拦得住 "", 但 "   " 有 3 个字符能过;
    若不在 validator 里 strip, 看板会显示一个空标签, 家长看不出
    这条作业叫什么。
    """
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={"task_name": "   "})
    assert r.status_code == 400
    assert _get(app_client, aid)["task_name"] == "背诵古诗两首", "原值不应被改坏"


def test_edit_est_minutes_out_of_range_rejected(app_client):
    aid = _add_one(app_client)
    for bad in (0, 181, -5):
        r = app_client.patch(f"/api/assignments/{aid}", json={"est_minutes": bad})
        assert r.status_code == 400, f"est_minutes={bad} 应被拒"
    assert _get(app_client, aid)["est_minutes"] == 20


def test_edit_unknown_assignment_404(app_client):
    r = app_client.patch("/api/assignments/999999", json={"task_name": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4008"


# ====== 2. PATCH 语义: 没传 vs 传 null ======

def test_omitted_field_is_untouched(app_client):
    """没传的字段必须保持原值 —— 只改描述不该把估时带成默认值。"""
    aid = _add_one(app_client, task_type="背诵", est_minutes=33)
    app_client.patch(f"/api/assignments/{aid}", json={"task_name": "只改名字"})
    got = _get(app_client, aid)
    assert got["task_name"] == "只改名字"
    assert got["est_minutes"] == 33
    assert got["task_type"] == "背诵"
    assert got["subject"] == "语文"
    assert got["due_date"] == "2026-09-21"


def test_empty_patch_is_noop(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={})
    assert r.status_code == 200
    assert r.json().get("noop") is True
    assert _get(app_client, aid)["task_name"] == "背诵古诗两首"


def test_can_clear_deadline_time(app_client):
    """核心回归: 传 null 必须真的清空, 而不是被当成"没传"跳过。

    这正是旧实现 (is not None 判定) 的漏洞 —— 清空操作静默失效,
    界面提示"已保存"但数据没变。
    """
    aid = _add_one(app_client, deadline_time="2026-09-21T08:20:00")
    assert _get(app_client, aid)["deadline_time"] is not None

    r = app_client.patch(f"/api/assignments/{aid}", json={"deadline_time": None})
    assert r.status_code == 200
    assert _get(app_client, aid)["deadline_time"] is None, "deadline_time 应被清空"


def test_can_clear_task_type(app_client):
    aid = _add_one(app_client, task_type="背诵")
    r = app_client.patch(f"/api/assignments/{aid}", json={"task_type": None})
    assert r.status_code == 200
    assert _get(app_client, aid)["task_type"] is None


def test_blank_task_type_becomes_null(app_client):
    """表单里清空输入框传出的是 '', 不该在库里留下"有类型但为空"的脏记录。

    周复盘按 task_type 分组统计估时偏差, 空串会自成一组, 让偏差表
    多出一行没有意义的样本。
    """
    aid = _add_one(app_client, task_type="背诵")
    app_client.patch(f"/api/assignments/{aid}", json={"task_type": "  "})
    assert _get(app_client, aid)["task_type"] is None


def test_set_deadline_time_from_null(app_client):
    """反向也要能: 原本没有截止时间, 补上一个。"""
    aid = _add_one(app_client, deadline_time=None)
    r = app_client.patch(f"/api/assignments/{aid}", json={"deadline_time": "2026-09-21T08:20:00"})
    assert r.status_code == 200
    assert _get(app_client, aid)["deadline_time"] == "2026-09-21T08:20:00"


def test_not_null_columns_reject_null(app_client):
    """NOT NULL 列传 null 必须被拒, 而不是等 SQLite 抛 IntegrityError。"""
    aid = _add_one(app_client)
    for field in ("task_name", "est_minutes", "due_date", "subject"):
        r = app_client.patch(f"/api/assignments/{aid}", json={field: None})
        assert r.status_code == 400, f"{field}=null 应被拒"
        assert r.json()["error"]["code"] == "4003"


# ====== 3. 不变量: 截止时间不得早于归属日 ======

def test_edit_rejects_deadline_before_due_date(app_client):
    aid = _add_one(app_client, deadline_time="2026-09-21T08:20:00")
    r = app_client.patch(f"/api/assignments/{aid}", json={"deadline_time": "2026-09-20T08:20:00"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4004"
    # 原值不能被这次失败的请求改坏
    assert _get(app_client, aid)["deadline_time"] == "2026-09-21T08:20:00"


def test_edit_due_date_cannot_pass_existing_deadline(app_client):
    """只改归属日也会破坏这个不变量 —— 这是最容易漏掉的一条。

    把归属日从 09-21 改到 09-25, 而 deadline_time 仍是 09-21 08:20,
    于是截止时间跑到了归属日之前。若只校验"本次请求里同时出现两个字段",
    这条改动会漏过去。
    """
    aid = _add_one(app_client, deadline_time="2026-09-21T08:20:00")
    r = app_client.patch(f"/api/assignments/{aid}", json={"due_date": "2026-09-25"})
    assert r.status_code == 400, "归属日晚于截止时间时应被拒"
    assert r.json()["error"]["code"] == "4004"
    assert _get(app_client, aid)["due_date"] == "2026-09-21", "归属日不应被改坏"


def test_edit_due_date_with_deadline_same_day_ok(app_client):
    """同一天的 deadline 不算越界 (口径与 insert 一致: 只比日期不比时分)。"""
    aid = _add_one(app_client, deadline_time="2026-09-21T08:20:00")
    r = app_client.patch(f"/api/assignments/{aid}", json={"due_date": "2026-09-21"})
    assert r.status_code == 200


def test_edit_due_date_backward_with_deadline_ok(app_client):
    """归属日往前挪到截止时间之前是合法的, 不该被这条校验误伤。"""
    aid = _add_one(app_client, deadline_time="2026-09-25T08:20:00")
    r = app_client.patch(f"/api/assignments/{aid}", json={"due_date": "2026-09-21"})
    assert r.status_code == 200
    assert _get(app_client, aid)["due_date"] == "2026-09-21"


# ====== 4. 改字段后的下游影响 ======

def test_edit_due_date_moves_item_between_days(app_client):
    """改归属日 = 搬家: 原日期列表里不该再有它, 新日期里要有。"""
    aid = _add_one(app_client)
    app_client.patch(f"/api/assignments/{aid}", json={"due_date": "2026-09-22"})

    old_day = app_client.get("/api/assignments?date=2026-09-21").json()["items"]
    new_day = app_client.get("/api/assignments?date=2026-09-22").json()["items"]
    assert aid not in [i["id"] for i in old_day]
    assert aid in [i["id"] for i in new_day]


def test_edit_subject_moves_item_between_groups(app_client):
    aid = _add_one(app_client, subject="语文")
    app_client.patch(f"/api/assignments/{aid}", json={"subject": "数学"})
    board = app_client.get("/api/board?date=2026-09-21").json()
    assert board["by_subject"]["数学"], "改科目后应出现在数学分组"
    assert not board["by_subject"]["语文"], "不应再留在语文分组"


def test_edit_est_minutes_updates_board_total(app_client):
    """看板顶部的估时合计要跟着变, 否则家长看到的预估与实际不符。"""
    aid = _add_one(app_client, est_minutes=20)
    board = app_client.get("/api/board?date=2026-09-21").json()
    assert board["progress"]["est_total"] == 20
    app_client.patch(f"/api/assignments/{aid}", json={"est_minutes": 50})
    board = app_client.get("/api/board?date=2026-09-21").json()
    assert board["progress"]["est_total"] == 50


def test_edit_allows_done_item(app_client):
    """已完成的作业也必须能改。

    已完成项禁止删除(4010), 若这里也不给编辑, 家长发现描述写错时
    就彻底动不了这条记录了。
    """
    aid = _add_one(app_client)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    r = app_client.patch(f"/api/assignments/{aid}", json={"task_name": "改过的名字"})
    assert r.status_code == 200
    got = _get(app_client, aid)
    assert got["task_name"] == "改过的名字"
    assert got["status"] == "done", "编辑内容不该顺手改掉状态"


def test_edit_does_not_touch_actual_minutes(app_client):
    """编辑描述不该清掉已记录的实耗 —— 那会让周复盘少一条样本。"""
    aid = _add_one(app_client)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 28})

    app_client.patch(f"/api/assignments/{aid}", json={"task_name": "改过的名字", "est_minutes": 40})
    got = _get(app_client, aid)
    assert got["actual_minutes"] == 28, "编辑内容不应影响实际用时"
    assert got["status"] == "done"


def test_edit_extra_fields_ignored(app_client):
    """多传未知字段应被忽略 (extra='ignore'), 不能 500。

    前端 collectRows 一类的调用可能带上多余键, 接口不该因此崩掉。
    """
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={
        "task_name": "改过的名字",
        "source_span": "试图篡改原文",
        "actual_minutes": 999,
    })
    assert r.status_code == 200
    got = _get(app_client, aid)
    assert got["source_span"] == "(测试)", "原文不可经编辑接口篡改"
    assert got["actual_minutes"] is None


# ====== 5. 前端契约 (静态检查) ======

@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


def _code(js: str) -> str:
    return "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))


def test_edit_button_exists(app_js):
    assert "function openEditAssignmentModal(" in app_js
    assert "status-btn edit" in _code(app_js)


def test_edit_button_on_every_status(app_js):
    """入口不能被状态分支包住 —— done/skipped 也得有。

    把编辑按钮放进 todo/doing 的 if 里会让已完成的作业永远改不了,
    而它恰恰是最需要改的(家长补记时才发现描述写错)。
    """
    code = _code(app_js)
    btn = code.split("const editBtn")[1].split("actions.appendChild(editBtn)")[0]
    assert "item.status" not in btn, "编辑入口不应受状态限制"


def test_edit_uses_patch_endpoint(app_js):
    code = _code(app_js)
    block = code.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert "api('PATCH', `/assignments/${item.id}`" in block


def test_edit_modal_has_all_fields(app_js):
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    for fid in ("ed-subject", "ed-name", "ed-est", "ed-due", "ed-dl", "ed-type"):
        assert f'id="{fid}"' in block, f"{fid} 缺失"


def test_edit_modal_prefills_current_values(app_js):
    """弹窗必须带出当前值。

    家长是来"改"某个字段的, 不是重填整条 —— 留空的话每次改个估时
    都要重新敲一遍任务名, 反而更容易出错。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert "item.task_name" in block
    assert "item.due_date" in block
    assert "item.est_minutes" in block
    assert "item.deadline_time" in block


def test_edit_sends_only_changed_fields(app_js):
    """只发改动的字段 —— 否则清空操作表达不出来。

    全部回传的话, 后端 model_fields_set 每次都命中所有字段,
    "清空截止时间"和"没改截止时间"就变成了同一个请求。
    """
    code = _code(app_js)
    assert "const setIfChanged = (k, v) => { if (v !== item[k]) patch[k] = v; };" in code


def test_edit_can_send_null_for_deadline(app_js):
    """清空截止时间必须真的发 null, 不是发空串。"""
    code = _code(app_js)
    assert "setIfChanged('deadline_time', dl ? `${dl}:00` : null);" in code


def test_edit_keeps_dialog_open_on_invalid(app_js):
    """校验不过要留在弹窗里, 不能静默关掉。

    悄悄关掉会让家长以为改完了, 实际什么也没改, 还得再点一次「编辑」
    才发现 —— 与 promptNumber 里同一条原则。
    """
    code = _code(app_js)
    block = code.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert "if (!name) { toast('任务名不能为空'); return; }" in block
    assert "if (!(est >= 1 && est <= 180))" in block
    assert "if (!due)" in block
    assert "dl.slice(0, 10) < due" in block


def test_edit_guards_against_overwriting_pending_dialog(app_js):
    assert "if (_modalCloser) return;" in _code(app_js).split("function openEditAssignmentModal(")[1]


def test_edit_refreshes_after_save(app_js):
    """保存后必须重载 —— 改 due_date 会让作业从当前这天搬走。"""
    code = _code(app_js)
    block = code.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert "if (onSaved) await onSaved();" in block


def test_edit_does_not_use_native_prompt(app_js):
    """编辑弹窗必须自绘, 不许回退到原生 prompt/confirm/alert。"""
    code = _code(app_js)
    block = code.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    for native in ("prompt(", "confirm(", "alert("):
        assert native not in block, f"编辑弹窗不应调用原生 {native}"


def test_edit_button_is_styled_neutrally(styles_css):
    """编辑不能抢"完成"的视觉权重, 也不能像删除那样用红色。"""
    block = styles_css.split(".status-btn.edit {")[1].split("}")[0]
    assert "dashed" in block, "沿用虚线 = 附带入口, 与 redo/opt-toggle 同记号"
    assert "var(--text-2)" in block
    assert "var(--status-overdue)" not in block, "编辑是纠错, 不是危险操作"


def test_edit_button_meets_touch_target(styles_css):
    """窄屏下也必须 >= 44px, 否则触屏点不准。

    styles.css 里有多个 @media (max-width: 767px) 块, 触控目标那条在
    §26a 里, 所以要逐块找而不是只看第一个 —— 只看第一个会漏判。
    """
    blocks = re.findall(r"@media \(max-width: 767px\)[^{]*\{(.*?)\n\}", styles_css, re.S)
    assert blocks, "未找到窄屏媒体查询"
    hit = [b for b in blocks if "min-height: 44px" in b and ".status-btn.edit" in b]
    assert hit, "触控目标规则里漏了 .status-btn.edit"


# ====== 6. 运行时 (jsdom) ======

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

// 带截止时间和类型, 用来验证"带出当前值"与"清空"两条路径。
const ITEM = {
  id: 7, subject: '数学', task_name: '第一单元综合复习', due_date: '2026-09-28',
  deadline_time: '2026-09-28T08:20:00', is_optional: false, status: 'todo',
  est_minutes: 30, actual_minutes: null, task_type: '计算',
  confidence: 'medium', source_span: '第一单元综合复习',
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
const patched = [];
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/board/today') || s.includes('/board?date=')) return ok(BOARD);
  if (s.includes('/board/dates')) return ok({ dates: [{ date: '2026-09-28', total: 1 }] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (o && o.method === 'PATCH') { patched.push({ url: s, body: JSON.parse(o.body) }); return ok({ id: 7 }); }
  return ok({});
};
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
const editBtn = () => [...doc.querySelectorAll('.row-actions button')].find(b => b.textContent.trim() === '编辑');
const btnByText = t => [...doc.querySelectorAll('.row-actions button')].find(b => b.textContent.trim() === t);
const val = id => { const e = doc.getElementById(id); return e ? e.value : null; };
const open = () => !modal.classList.contains('hidden');

(async () => {
  const out = {};
  w.__b();
  await wait(300);

  out.hasEditBtn = !!editBtn();
  out.actions = [...doc.querySelectorAll('.row-actions button')].map(b => b.textContent.trim());

  // 1. 打开弹窗 -> 必须带出当前值
  click(editBtn());
  await wait(120);
  out.opened = open();
  out.title = doc.getElementById('modal-title').textContent;
  out.prefill = {
    name: val('ed-name'), est: val('ed-est'), due: val('ed-due'),
    dl: val('ed-dl'), type: val('ed-type'), subject: val('ed-subject'),
  };
  out.okText = okBtn.textContent.trim();
  out.cancelHidden = cancelBtn.classList.contains('hidden');
  out.datePickerBound = !!doc.getElementById('ed-due')._fpInstance;

  // 2. 原样保存 -> 不该发任何 PATCH
  click(okBtn);
  await wait(200);
  out.patchesAfterNoop = patched.length;
  out.closedAfterNoop = !open();

  // 3. Esc 取消 -> 不发请求
  click(editBtn());
  await wait(120);
  doc.getElementById('ed-name').value = '改了但我不保存';
  key(doc, 'Escape');
  await wait(150);
  out.patchesAfterEsc = patched.length;
  out.closedAfterEsc = !open();

  // 4. 改描述 + 估时 -> 只发这两个字段
  click(editBtn());
  await wait(120);
  doc.getElementById('ed-name').value = '第一单元综合复习(改)';
  doc.getElementById('ed-est').value = '45';
  click(okBtn);
  await wait(250);
  out.patches = patched.length;
  out.patched = patched[0] || null;
  out.closedAfterSave = !open();

  // 5. 清空截止时间 -> 必须发 null (不是空串)
  click(editBtn());
  await wait(120);
  doc.getElementById('ed-dl').value = '';
  doc.getElementById('ed-type').value = '';
  click(okBtn);
  await wait(250);
  out.clearPatch = patched[1] || null;

  // 6. 空任务名 -> 留在弹窗里, 不发请求
  click(editBtn());
  await wait(120);
  const before = patched.length;
  doc.getElementById('ed-name').value = '   ';
  click(okBtn);
  await wait(200);
  out.staysOpenOnBlankName = open();
  out.patchesAfterBlank = patched.length - before;

  // 7. 截止时间早于归属日 -> 拦住
  doc.getElementById('ed-name').value = '合法名字';
  doc.getElementById('ed-due').value = '2026-09-28';
  doc.getElementById('ed-dl').value = '2026-09-20T08:20';
  click(okBtn);
  await wait(200);
  out.staysOpenOnBadDeadline = open();
  out.patchesAfterBadDeadline = patched.length - before;

  // 8. 估时越界 -> 拦住
  doc.getElementById('ed-dl').value = '';
  doc.getElementById('ed-est').value = '999';
  click(okBtn);
  await wait(200);
  out.staysOpenOnBadEst = open();
  out.patchesAfterBadEst = patched.length - before;

  // 9. 回归 (2026-10-02 浏览器验收发现): 提交成功后确定键必须恢复可点。
  //    modal-ok 是常驻节点, 提交时把它 disabled 当请求锁; 若成功后不收回,
  //    第二次开弹窗的确定键就是灰的 —— 家长看到弹窗正常打开却点不动。
  //    注意: jsdom 里 dispatchEvent 不受 disabled 约束(真实浏览器会拦掉),
  //    所以这里必须直接读属性, 靠"再点一次能不能存"是测不出来的。
  key(doc, 'Escape');
  await wait(150);
  click(editBtn());
  await wait(150);
  out.okEnabledOnReopen = !okBtn.disabled;
  // 换个弹窗也一样不能是灰的
  key(doc, 'Escape');
  await wait(150);
  click(btnByText('删除'));
  await wait(150);
  out.okEnabledForOtherDialogs = !okBtn.disabled;

  // 10. 回归 (2026-10-02 浏览器验收发现): 弹窗关掉后, 还开着的
  //     flatpickr 日历浮层必须一起收起。
  //
  //     flatpickr 把日历挂在 body 下, 不在 #modal 里, 所以
  //     closeModal 给 #modal 加 .hidden 收不住它 —— 浮层会带着
  //     display:block 继续浮在页面上, 挡住"保存"按钮的点击命中
  //     (真实浏览器里点保存会 hit test 到日历里的数字, 按钮点不动)。
  key(doc, 'Escape');
  await wait(150);

  click(editBtn());
  await wait(150);
  // 模拟家长点了日期框: flatpickr 对外暴露的是 open()/close(),
  // 没有 _show 之类的私有方法(jsdom 里布局为空, 真实点击未必触发
  // 浮层, 所以直接调 open 更稳)。
  const dlInst = doc.getElementById('ed-dl')._fpInstance;
  out.dlHasInstance = !!dlInst && typeof dlInst.close === 'function';
  if (dlInst) dlInst.open();
  await wait(150);
  out.pickerOpenBeforeClose = doc.querySelectorAll('.flatpickr-calendar.open').length;
  out.dlInstanceOpen = dlInst ? !!dlInst.isOpen : null;

  key(doc, 'Escape');
  await wait(200);
  out.pickerOpenAfterClose = doc.querySelectorAll('.flatpickr-calendar.open').length;
  out.modalClosed = !open();
  out.dlInstanceClosed = dlInst ? !dlInst.isOpen : null;

  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    import json
    h = ROOT / "tmp" / "_edit_harness.js"
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
def test_edit_button_rendered(live):
    assert live["hasEditBtn"] is True
    # 与既有的状态按钮并存, 顺序在「删除」之前
    assert live["actions"] == ["完成", "今天不做", "选做", "编辑", "删除"]


@needs_jsdom
def test_edit_modal_opens_with_current_values(live):
    """家长是来"改"的, 不是重填 —— 必须带出当前值。"""
    assert live["opened"] is True
    assert "编辑" in live["title"]
    p = live["prefill"]
    assert p["name"] == "第一单元综合复习"
    assert p["est"] == "30"
    assert p["due"] == "2026-09-28"
    assert p["dl"] == "2026-09-28T08:20", "带秒的 ISO 要截到分钟, 否则控件显示空白"
    assert p["type"] == "计算"
    assert p["subject"] == "数学"
    assert live["okText"] == "保存"
    assert live["cancelHidden"] is False, "必须有取消才能反悔"
    assert live["datePickerBound"] is True, "日期框应绑上 flatpickr, 不露原生控件"


@needs_jsdom
def test_noop_save_sends_nothing(live):
    """什么都没改不该白跑一趟请求。"""
    assert live["patchesAfterNoop"] == 0
    assert live["closedAfterNoop"] is True


@needs_jsdom
def test_escape_cancels_without_saving(live):
    assert live["patchesAfterEsc"] == 0
    assert live["closedAfterEsc"] is True


@needs_jsdom
def test_edit_sends_only_changed_fields(live):
    """只发改动的字段, 否则清空语义会丢。"""
    assert live["patches"] == 1
    body = live["patched"]["body"]
    assert set(body) == {"task_name", "est_minutes"}, f"多发了字段: {sorted(body)}"
    assert body["task_name"] == "第一单元综合复习(改)"
    assert body["est_minutes"] == 45
    assert "/assignments/7" in live["patched"]["url"]
    assert live["closedAfterSave"] is True


@needs_jsdom
def test_clearing_deadline_sends_null(live):
    """清空必须发 null —— 发空串会被后端当成"设成空字符串"。"""
    body = live["clearPatch"]["body"]
    assert "deadline_time" in body
    assert body["deadline_time"] is None, f"应为 null, 实际 {body['deadline_time']!r}"
    assert "task_type" in body
    assert body["task_type"] is None, "空类型应归一成 null, 不留空串污染周复盘分组"


@needs_jsdom
def test_blank_name_keeps_dialog_open(live):
    assert live["staysOpenOnBlankName"] is True
    assert live["patchesAfterBlank"] == 0


@needs_jsdom
def test_deadline_before_due_is_blocked(live):
    assert live["staysOpenOnBadDeadline"] is True
    assert live["patchesAfterBadDeadline"] == 0


@needs_jsdom
def test_out_of_range_est_is_blocked(live):
    assert live["staysOpenOnBadEst"] is True
    assert live["patchesAfterBadEst"] == 0


@needs_jsdom
def test_ok_button_reenabled_after_submit(live):
    """回归 (2026-10-02 浏览器验收发现): 提交成功后确定键必须恢复可点。

    #modal-ok 是 index.html 里的常驻节点, 提交类弹窗把它 disabled 当请求锁。
    锁若不在每次开弹窗时收回, 一次成功提交之后确定键就永久变灰 ——
    家长第二次点任何弹窗都会看到"弹窗开了但确定键点不动"。

    这条在 jsdom 里必须直接读 okBtn.disabled: dispatchEvent 不受 disabled
    约束(真实浏览器会拦掉点击), 所以"再点一次看能不能存"这种间接验证
    在 jsdom 里恒为真, 抓不到这个 bug —— 它是拿真实浏览器验收时发现的。
    """
    assert live["okEnabledOnReopen"] is True, "重新打开编辑弹窗时确定键仍是灰的"
    assert live["okEnabledForOtherDialogs"] is True, \
        "上一次提交留下的锁污染了其它弹窗(删除/改用时/手动添加)"


@needs_jsdom
def test_date_picker_overlay_closes_with_modal(live):
    """回归 (2026-10-02 浏览器验收发现): 弹窗关闭时日历浮层要跟着收起。

    flatpickr 的日历挂在 body 下而不在 #modal 里, closeModal 隐藏
    #modal 收不住它。残留的浮层会一直浮在页面上, 并挡住"保存"按钮 ——
    浏览器里点保存会 hit test 到日历里的数字 span, 按钮完全点不动。

    这是真实浏览器才发现的: jsdom 没有布局, 浮层的遮挡/定位行为和
    真实浏览器不同, 只能断言"浮层是否还开着"这个可观测状态。
    """
    assert live["dlHasInstance"] is True, \
        "编辑弹窗的截止时间应绑上 flatpickr 实例(closeModal 靠它收起浮层)"
    assert live["dlInstanceOpen"] is True, \
        "前置条件没成立: open() 之后实例应处于打开状态"
    assert live["pickerOpenBeforeClose"] >= 1, \
        "前置条件没成立: 点日期框后应有浮层处于打开状态"
    assert live["modalClosed"] is True
    assert live["pickerOpenAfterClose"] == 0, \
        "弹窗关了但日历浮层还开着, 会挡住按钮点击"
    assert live["dlInstanceClosed"] is True, \
        "应走 inst.close() 让库自己收尾, 而不是只摘 open 类"
