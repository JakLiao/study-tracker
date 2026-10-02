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


# ====== 1b. 通过编辑接口改选做 (2026-10-02) ======
#
# 此前 is_optional 只能走行内按钮的 /optional 专用接口, 编辑弹窗里没有
# 控件。补齐后这里锁住 PATCH 语义 —— 下面四条对应四种组合, 少一条都可能
# 让"取消选做"静默失效(而界面照样提示"已保存")。

def test_edit_can_mark_optional(app_client):
    aid = _add_one(app_client, is_optional=False)
    r = app_client.patch(f"/api/assignments/{aid}", json={"is_optional": True})
    assert r.status_code == 200
    assert _get(app_client, aid)["is_optional"] is True


def test_edit_can_unmark_optional(app_client):
    """取消选做必须真的生效 —— 这条最容易被写错。

    is_optional 的合法值之一就是 False, 而 False 又是 falsy: 如果
    service 里写成 `if edit.is_optional:` 或 `if "is_optional" in provided
    and edit.is_optional`, 取消动作就会被当成"没传"跳过, 勾选永远
    取消不掉, 且接口返回 200、界面提示"已保存"。
    """
    aid = _add_one(app_client, is_optional=True)
    r = app_client.patch(f"/api/assignments/{aid}", json={"is_optional": False})
    assert r.status_code == 200
    assert _get(app_client, aid)["is_optional"] is False


def test_omitted_is_optional_leaves_flag_untouched(app_client):
    """不传 is_optional 时不能动它。

    只改任务名的保存请求里不该夹带选做字段 —— 夹带了且实现写错,
    就会把选做状态清掉。这条锁住 PATCH 的"没传就不碰"。
    """
    aid = _add_one(app_client, is_optional=True)
    r = app_client.patch(f"/api/assignments/{aid}", json={"task_name": "改过的名字"})
    assert r.status_code == 200
    got = _get(app_client, aid)
    assert got["task_name"] == "改过的名字"
    assert got["is_optional"] is True, "没传 is_optional 却把它改了"


def test_is_optional_change_reaches_the_board_groups(app_client):
    """标记选做后, 这条作业要从未完成区移到选做分组。

    这是选做最要紧的业务含义 —— 进度条分母排除选做, 分组也据此分区。
    接口改了库但界面没重算, 家长会以为标记没生效。
    """
    aid = _add_one(app_client, is_optional=False)
    app_client.patch(f"/api/assignments/{aid}", json={"is_optional": True})
    board = app_client.get("/api/board?date=2026-09-21").json()
    in_required = [i["id"] for lst in board["by_subject"].values() for i in lst]
    assert aid not in in_required, "标为选做后仍留在必做分组里"
    assert aid in [i["id"] for i in board["optional_items"]]


def test_edit_optional_writes_to_completion_logs(app_client):
    """选做变更要落进 completion_logs, 口径与 /optional 接口一致。

    这张表是"这条怎么变成选做的"的唯一去处, 但没有读取接口, 所以直接
    查库断言。import 写在函数内与其他测试一致: conftest 每个用例都会
    reload app.config, 模块级 import 会拿到旧模块的 DB_PATH。
    """
    import sqlite3
    from app import config
    aid = _add_one(app_client, is_optional=False)
    app_client.patch(f"/api/assignments/{aid}", json={"is_optional": True})
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT action, from_value, to_value FROM completion_logs "
            "WHERE assignment_id = ? ORDER BY at DESC, id DESC LIMIT 1",
            (aid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "选做变更没有留痕"
    assert row["action"] == "edited"
    assert "is_optional" in (row["from_value"] or "") + (row["to_value"] or "")




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
    for fid in ("ed-subject", "ed-name", "ed-est", "ed-due", "ed-dl", "ed-opt"):
        assert f'id="{fid}"' in block, f"{fid} 缺失"
    # 作业类型改成自绘下拉后, input 由 taskTypeComboHtml 渲染, 不在这段字面量里
    assert "taskTypeComboHtml('ed-type'" in block, "ed-type 下拉缺失"


def test_edit_form_hints_are_inline_not_stray_paragraph(app_js):
    """编辑弹窗的说明必须就地贴在字段旁, 不能留在弹窗最底部。

    原先底部一句「改完点保存。选做请用行内的选做按钮」把三件事混在
    一起: 一句废话、一个指路说明、一个已不成立的限制(选做现在就在
    弹窗里改)。拆成字段旁的说明后, 底部不再有任何游离文字。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    grid = re.search(r'<div class="form-grid">(.*?)</div>', block, re.S)
    assert grid, "未找到 .form-grid"
    # 三个需要解释的字段各自带 .form-hint
    for label in ("截止时间", "作业类型", "是否选做"):
        seg = re.search(
            rf'<span class="form-label">{label}</span>(.*?)</label>', grid.group(1), re.S)
        assert seg, f"{label} 字段不存在"
        assert 'class="form-field"' in seg.group(1), f"{label} 未与说明裹进 form-field"
        assert 'class="form-hint"' in seg.group(1), f"{label} 旁缺就地说明"
    # 弹窗底部不许再有 .muted 段落
    tail = block.split("</div>")[-1]
    assert 'class="muted"' not in tail, "弹窗底部仍有游离说明, 应拆到字段旁"


def test_edit_optional_field_label_is_explicit(app_js):
    """选做标签直说「是否选做」, 作业类型另起一个字段。

    两个字段不能共用一个标签 —— 否则家长分不清改的是哪个。作业类型
    标签定为「作业类型」而不是「类型」: 设置页里对应的配置项就叫
    「各作业类型预估用时」, 叫「类型」会与"作业类型/是否选做"这类
    其他类型概念混淆。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert '<span class="form-label">是否选做</span>' in block
    assert '<span class="form-label">作业类型</span>' in block, "作业类型字段应有独立标签"
    assert '<span class="form-label">类型</span>' not in block, \
        "「类型」与「是否选做」易混, 应写成「作业类型」"
    # 两个字段必须各自独立, 不能是同一个 label
    assert "taskTypeComboHtml('ed-type'" in block and 'id="ed-opt"' in block


# ====== 5c. 字段顺序 (2026-10-02 用户指定) ======
#
# 顺序本身不影响功能, 但它是家长每天都要扫一遍的操作路径 —— 两次打开
# 弹窗位置不一致会让人多花半秒找字段。下面按用户给的顺序逐个断言标签
# 出现的位置, 而不只是"存在": 存在但乱序, 断言照样通过, 等于没锁。

def test_edit_field_order_matches_spec(app_js):
    """编辑弹窗字段顺序: 归属日→科目→作业项→作业类型→预计用时→是否选做→截止时间。"""
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    grid = re.search(r'<div class="form-grid">(.*?)</div>', block, re.S).group(1)
    got = re.findall(r'<span class="form-label">([^<]+)</span>', grid)
    assert got == ["归属日", "科目", "作业项", "作业类型", "预计用时", "是否选做", "截止时间"], \
        f"字段顺序与设计不符: {got}"


def test_task_type_candidates_come_from_settings(app_js):
    """作业类型候选必须取自设置, 不能写死一份。

    写死的话, 家长在设置页新增自定义类型(如「听写」)后, 弹窗里选不到
    那个类型, 于是它的预估值也永远命中不了 —— 2026-09-29 报障的
    「配置孤岛」会以另一种形式复发。
    """
    code = _code(app_js)
    fn = code.split("function taskTypeOptions(")[1].split("\n}")[0]
    assert "_baselineCache" in fn, "候选未读取设置缓存"
    assert "task_type" in fn, "候选未取自 task_type 基线"
    # 两个弹窗都必须用这份共享候选, 不允许各自写死
    for modal in ("openEditAssignmentModal", "openManualAddModal"):
        block = code.split(f"function {modal}(")[1].split("\n}")[0]
        assert "taskTypeComboHtml(" in block, f"{modal} 未使用共享候选下拉"


# ====== 5d. 作业类型下拉自绘, 不用 datalist (2026-10-02 用户反馈) ======
#
# 用户反馈: 「这个样式还是有点丑呀, 应该要跟我们系统配色搭配, 而不是
# 用系统默认的。」截图里是 <datalist> 的候选面板 —— 近黑底浅色字, 完全
# 不属于本项目的奶油糖果配色。
#
# 根因不是配色写错了, 而是 <datalist> 的面板由操作系统绘制, CSS 改不动。
# 所以只能自绘。项目里已有同样先例: 原生 prompt() 与 <input type="date">
# 都被换成了自绘控件(见 promptNumber / flatpickr 段落)。

def test_no_datalist_anywhere(app_js):
    """不得再用 <datalist> —— 它的面板无法用 CSS 定制。

    这条是本轮反馈的核心。若哪天有人为了"省事"把下拉改回 datalist,
    界面会立刻退回系统默认样式, 而功能测试全绿 —— 只能靠断言拦住。

    只查标记本身, 不查注释: 解释"为什么不用 datalist"的注释里必然会出现
    这个词, 连注释一起断言会让这条测试永远红, 反而没人敢碰这段代码。
    """
    markup = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", "", app_js)
    assert "<datalist" not in markup, "datalist 面板由系统绘制, 无法适配主题"
    assert "taskTypeDatalist" not in app_js, "旧 datalist 渲染函数应已移除"
    assert not re.search(r"<input[^>]*\slist=", markup), "不应再有 input[list] 关联 datalist"


def test_combo_is_built_with_a11y_roles(app_js):
    """自绘下拉要带 combobox/listbox 语义, 否则读屏软件无从下手。

    丢掉的不是可有可无的细节: 原本 <input list> 由浏览器负责播报
    "有候选可用", 自绘后得自己补上 aria-expanded / role=listbox。
    """
    code = _code(app_js)
    fn = code.split("function taskTypeComboHtml(")[1].split("\n}")[0]
    assert 'role="combobox"' in fn
    assert 'aria-expanded="false"' in fn
    assert 'role="listbox"' in fn
    assert 'aria-haspopup="listbox"' in fn
    # 输入框仍可自由输入: 候选是建议, 不是枚举限制
    assert 'class="combo-input"' in fn
    assert "disabled" not in fn, "作业类型必须允许自由输入, 不能只让选"


def test_combo_supports_keyboard_navigation(app_js):
    """上下键移动高亮、回车选中、Esc 关闭 —— 键盘用户不能被排除。"""
    code = _code(app_js)
    fn = code.split("function bindCombo(")[1].split("\nfunction ")[0]
    for key in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
        assert f"'{key}'" in fn, f"下拉未处理 {key}"
    assert "aria-selected" in fn, "高亮项应同步 aria-selected"
    assert "preventDefault" in fn, "方向键/回车需 preventDefault, 否则会同时移动光标或提交表单"


def test_combo_closes_on_outside_click(app_js):
    """点浮层外部要收起, 否则弹窗里会留一块"关不掉"的候选面板。"""
    code = _code(app_js)
    fn = code.split("function bindCombo(")[1].split("\nfunction ")[0]
    assert "pointerdown" in fn
    assert "!root.contains(e.target)" in fn, "外部点击未做 contains 判断"
    # 用 capture 阶段: 选项自己的 click 在冒泡, 冒泡里判断会误关
    assert "true)" in fn.split("document.addEventListener")[1][:120], \
        "外部点击监听应在 capture 阶段注册"


def test_combo_selection_does_not_reopen(app_js):
    """选中候选后浮层必须保持收起。

    这条对应一个真实踩过的坑: pick() 里派发 input 通知预填、又要调
    input.focus(), 两者都会触发 open() 把浮层弹回来, 表现是"点了选项
    却像没点上"。所以 suppressOpen 闸门必须覆盖这两步。
    """
    code = _code(app_js)
    fn = code.split("const pick = (value)")[1].split("};")[0]
    assert "suppressOpen = true" in fn
    assert "suppressOpen = false" in fn
    # 闸门必须先于 onPick / focus 就位
    assert fn.index("suppressOpen = true") < fn.index("onPick")
    assert fn.index("suppressOpen = false") > fn.index("input.focus()")


def test_combo_styles_use_theme_tokens(styles_css):
    """下拉样式必须引用主题令牌, 不能写死系统灰。

    这条直接对应"要跟系统配色搭配"那句反馈: 面板底色/文字/高亮都要走
    --surface / --text-1 / --accent-soft 这一套, 换成写死的 #333 之类
    就又变回系统默认观感了。
    """
    block = re.search(r"\.combo \.combo-menu \{(.*?)\}", styles_css, re.S)
    assert block, "候选浮层缺少样式"
    css = block.group(1)
    assert "var(--surface)" in css, "浮层底色未用主题表面色"
    assert "var(--border)" in css, "浮层描边未用主题边框色"
    # 绝不能出现系统默认深色(如 #333 / black / rgb(0,0,0))
    assert not re.search(r"#(?:000|333|222)\b|black\b|rgba?\(\s*0\s*,\s*0\s*,\s*0", css), \
        "浮层里出现写死的系统深色, 会退回系统默认观感"
    # 选项高亮
    opt = re.search(r"\.combo \.combo-option:hover,\s*\.combo \.combo-option\.is-active \{(.*?)\}", styles_css, re.S)
    assert opt and "var(--surface-2)" in opt.group(1), "高亮底色未用主题色"


def test_accent_soft_token_defined(styles_css):
    """--accent-soft 必须在令牌区定义, 且确实是主色的浅化版。

    下拉的焦点环与选中底色都依赖它。这个令牌原先并不存在(只有 --accent
    和 --warn-soft), 漏定义会让这两处样式静默失效 —— 变量未定义时
    background 落回 transparent, 不会有任何报错, 只是"看不见高亮"。
    """
    m = re.search(r"--accent-soft:\s*(#[0-9A-Fa-f]{3,8})", styles_css)
    assert m, "--accent-soft 未定义, 下拉高亮与焦点环会静默失效"
    token = m.group(1).lower()
    accent = re.search(r"--accent:\s*(#[0-9A-Fa-f]{3,8})", styles_css).group(1).lower()

    def rgb(h):
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    t, a = rgb(token), rgb(accent)
    # 必须是主色的浅化版: 三个通道都比主色更亮, 且仍保持同一色相方向
    # (红最多、蓝最少), 否则就不是"粉色系"的浅底了。
    assert t != a, "--accent-soft 不能与 --accent 同值, 否则作底太艳"
    assert t[0] >= a[0] and t[1] >= a[1] and t[2] >= a[2], \
        f"--accent-soft 应比 --accent({accent}) 更浅, 当前 {token}"
    assert t[0] > t[2], "浅底应保持暖粉方向(红 > 蓝), 否则会偏灰或偏紫"


def test_global_focus_ring_softened_for_combo(styles_css):
    """展开时不该沿用那个 2px 深棕实环。

    全局 input:focus 用的是 box-shadow: 0 0 0 2px var(--text-1)(深棕),
   平时没问题; 但下拉展开时它紧贴着一块浅色面板, 看起来像"校验失败"
   的红色警告框, 视觉过于紧张。展开态要换成主色的柔光环。
    """
    m = re.search(r"\.combo\.is-open \.combo-input \{(.*?)\}", styles_css, re.S)
    assert m, "展开态缺少输入框样式"
    css = m.group(1)
    assert "var(--accent-soft)" in css, "展开态焦点环未用主色柔光环"
    assert "var(--text-1)" not in css, "展开态仍在用深棕实环, 视觉像报错"


def test_edit_optional_checkbox_reflects_current_value(app_js):
    """复选框的 checked 必须由 item.is_optional 驱动。

    写死 checked 或干脆不带, 都会让"取消选做"在界面上无从下手 ——
    家长看到的永远是未勾选, 与实际状态脱节。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert re.search(r'id="ed-opt" type="checkbox" \$\{item\.is_optional \? .checked. : ..\}', block), \
        "ed-opt 的 checked 未绑定 item.is_optional"


def test_edit_optional_change_is_sent_to_backend(app_js):
    """保存时必须把选做状态发出去。

    这是加后端支持的另一半: 光有控件不发请求等于没做(界面提示
    "已保存"但库里没变)。走主 PATCH 接口而不是 /optional 专用接口,
    避免一次保存发两个请求留下半保存状态。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    code = _code(block)
    assert "setIfChanged('is_optional', document.getElementById('ed-opt').checked);" in code, \
        "保存时没发 is_optional"
    # 不能偷偷走 /optional 专用接口: 一次保存发两个请求, 中途失败会留下
    # "描述已改但选做没改"的半保存状态, 而界面对此毫无提示。
    assert "/optional" not in code, "不应在保存流程里另发 /optional 请求"
    # 走主接口, 与其他字段同一个请求体
    assert "api('PATCH', `/assignments/${item.id}`" in code


def test_edit_no_longer_refuses_optional_via_row_button(app_js):
    """底部那句「选做请用行内的按钮」必须删掉。

    选做已经能在这个弹窗里改了, 那句指路会让家长以为弹窗里改不了,
    白白关掉弹窗再去看板找按钮。
    """
    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    assert "行内" not in block, "底部仍在让家长去用行内按钮切选做"
    assert "改完点" not in block, "「改完点保存」是废话, 保存键自己就写着保存"


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
    assert "if (!name) { toast('作业项不能为空'); return; }" in block
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
const posted = [];
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/board/today') || s.includes('/board?date=')) return ok(BOARD);
  if (s.includes('/board/dates')) return ok({ dates: [{ date: '2026-09-28', total: 1 }] });
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (o && o.method === 'POST') {
    posted.push({ url: s, body: JSON.parse(o.body) });
    return ok({ created_ids: [8], created_count: 1, skipped: [] });
  }
  if (o && o.method === 'PATCH') {
    const body = JSON.parse(o.body);
    patched.push({ url: s, body });
    // 真后端会把改动落库, 下次 GET 就能看到新值。这里必须照做, 否则
    // "勾上选做 -> 重开弹窗 -> 取消" 这条路径无法验证: 复选框会一直
    // 带出初始的 false, 看起来像 prefill 绑定写错了。
    if ('is_optional' in body) ITEM.is_optional = body.is_optional;
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
w.eval(fs.readFileSync(appJsPath, 'utf8') +
  '\n;globalThis.__b = () => switchTab("board");' +
  '\n;globalThis.__setBaseline = setBaselineCache;' +
  '\n;globalThis.__openManual = openManualAddModal;');

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

  // 1b. 是否选做 (2026-10-02 新增): 必须带出当前状态 (ITEM.is_optional=false)
  out.optPrefill = doc.getElementById('ed-opt') ? doc.getElementById('ed-opt').checked : null;
  out.optHasField = !!doc.getElementById('ed-opt');

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

  // 11. (2026-10-02) 在编辑弹窗里勾选「选做」-> 必须真的发 is_optional: true。
  //     此前 is_optional 只能走行内按钮的 /optional 专用接口; 若只在前端
  //     加复选框而不改后端, 主 PATCH 会因 extra="ignore" 静默丢弃它, 界面
  //     却提示"已保存" —— 勾选永远不生效。这条锁住前端确实发了。
  key(doc, 'Escape');
  await wait(150);
  click(editBtn());
  await wait(150);
  const optBox = doc.getElementById('ed-opt');
  out.optStartsUnchecked = !optBox.checked;
  const beforeOpt = patched.length;
  optBox.checked = true;
  click(okBtn);
  await wait(250);
  out.optPatchSent = patched.length - beforeOpt;
  out.optPatchBody = patched[patched.length - 1] ? patched[patched.length - 1].body : null;
  out.optClosedAfterSave = !open();

  // 12. (2026-10-02) 取消选做必须发 false 而不是"不发"。
  //     勾上再打开取消掉, 若实现里用 truthy 判断, 这一步会退化成
  //     "没有改动"而直接关窗 —— 选做状态永远取消不掉。
  click(editBtn());
  await wait(150);
  const optBox2 = doc.getElementById('ed-opt');
  out.optPrefilledChecked = optBox2.checked;
  const beforeUnmark = patched.length;
  optBox2.checked = false;
  click(okBtn);
  await wait(250);
  out.unmarkPatchBody = patched.length > beforeUnmark ? patched[patched.length - 1].body : null;

  // 13. (2026-10-02) 手动添加弹窗: 预计用时按作业类型自动预填。
  //     用户需求原文「预计用时在新增操作时, 如果为空, 根据设置
  //     「各作业类型预估用时」的作业类型可以自动匹配预填」。
  key(doc, 'Escape');
  await wait(150);
  w.__setBaseline({ task_type: { 背诵: 15, 订正: 25, 听写: 8 }, subject: { 语文: 20, 数学: 15 } });
  w.__openManual();
  const est = doc.getElementById('m-est');
  const type = doc.getElementById('m-type');
  const subj = doc.getElementById('m-subject');
  out.manualStartsEmpty = est.value === '';
  out.estOnOpen = est.value;
  out.manualFieldOrder = [...doc.querySelectorAll('#modal .form-grid > label > .form-label')]
    .map(l => l.textContent);

  type.value = '背诵'; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  out.estAfterType15 = est.value;
  type.value = '订正'; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  out.estAfterType25 = est.value;
  // 自定义类型: 候选与预填都必须跟着设置走 (听写=8 是配置里的, 非写死值)
  type.value = '听写'; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  out.estAfterCustomType = est.value;
  // 自绘下拉的候选: 展开一次让它渲染, 再读选项。
  // 先清空输入框: 下拉会按当前输入过滤, 此时框里是"听写", 展开就只能
  // 看到它自己 —— 看不出候选列表是否跟着设置走。
  // 找 .combo-toggle 要用 [data-combo] 容器, 不能写成 #m-type 的后代 ——
  // 箭头按钮是 input 的兄弟节点, 不是它的子节点。
  type.value = ''; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  est.value = ''; est.dispatchEvent(new w.Event('input', { bubbles: true }));
  doc.querySelector('#m-type').closest('[data-combo]').querySelector('.combo-toggle')
    .dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  // 候选与菜单都挂在 [data-combo] 容器下, 与 #m-type(input) 是兄弟,
  // 不能写成 #m-type .combo-option —— 那样一个都选不到。
  const comboBox = doc.querySelector('#m-type').closest('[data-combo]');
  out.comboOptions = [...comboBox.querySelectorAll('.combo-option')]
    .map(o => o.getAttribute('data-value'));
  out.comboMenuClosedAfterPick = (() => {
    // 选一个候选后浮层应保持收起 (suppressOpen 闸门的作用)
    const opt = comboBox.querySelector('.combo-option');
    if (!opt) return null;
    opt.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
    return {
      value: type.value,
      hidden: comboBox.querySelector('.combo-menu').hidden,
    };
  })();
  // 家长手输过 -> 改类型不得覆盖。
  // 必须派发 input 事件: 真实家长敲键盘时浏览器就是这么通知的,
  // 直接赋值 value 不会触发, 那样测的是"程序改的值"而非"家长敲的值"。
  const typeEst = (el, v) => { el.value = v; el.dispatchEvent(new w.Event('input', { bubbles: true })); };
  typeEst(est, '40');
  type.value = '背诵'; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  out.estKeepsUserValue = est.value;
  // 家长自己清空 -> 视为交回控制权, 重新选类型应再次预填
  typeEst(est, '');
  type.value = '订正'; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  out.estRefillsAfterClear = est.value;
  // 类型留空时退回科目基线
  typeEst(est, ''); type.value = ''; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  subj.value = '数学'; subj.dispatchEvent(new w.Event('change', { bubbles: true }));
  out.estBySubject = est.value;
  // 留空提交也要能入库 (后端 est_minutes 必填, 前端必须兜底)。
  // 注意: 家长清空后系统会立即按当前类型重填(这是刻意的), 所以这里
  // 断言的是"最终发出去的 est_minutes 合法且来自基线", 而不是"框是空的"。
  type.value = '订正'; type.dispatchEvent(new w.Event('input', { bubbles: true }));
  typeEst(est, '');
  out.estAfterFinalClear = est.value;
  doc.getElementById('m-name').value = '预填验收';
  const batchBefore = posted.filter(p => p.url.includes('/assignments/batch')).length;
  click(okBtn);
  // 手动添加的 onOk 里有 await api(...) 再 switchTab('board'), 链路比
  // 编辑保存长(编辑那边 await 之后还有 onSaved 重载), 250ms 不够稳定。
  await wait(600);
  // 只认入库那个 POST: 前面的场景(如"跳过/恢复/删除确认")也会发 POST,
  // 直接取 posted[last] 会抓到不相干的请求。
  const batchPosts = posted.filter(p => p.url.includes('/assignments/batch'));
  out.manualPosted = batchPosts.length - batchBefore;
  out.manualPostUrls = posted.map(p => p.url);
  out.manualBody = batchPosts.length ? batchPosts[batchPosts.length - 1] : null;

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


# ====== 5b. 是否选做字段 (2026-10-02) ======

@needs_jsdom
def test_edit_modal_has_optional_field(live):
    """编辑弹窗必须有「是否选做」控件, 且带出当前状态。

    ITEM.is_optional 是 false, 所以打开时必须是不勾选 —— 反过来
    (勾上了) 说明 `checked` 绑定写错, 家长一打开就看到假状态。
    """
    assert live["optHasField"] is True, "编辑弹窗缺少「是否选做」控件"
    assert live["optPrefill"] is False, "选做状态没带出来"
    assert live["optStartsUnchecked"] is True, "is_optional=false 却显示成勾选"


@needs_jsdom
def test_marking_optional_sends_true(live):
    """勾选「选做」必须真的发出去, 而不是被后端静默丢弃。"""
    assert live["optPatchSent"] == 1, "勾选选做后没有发出 PATCH"
    body = live["optPatchBody"]
    assert body.get("is_optional") is True, f"没发 is_optional=true: {body}"
    assert live["optClosedAfterSave"] is True


@needs_jsdom
def test_unmarking_optional_sends_false(live):
    """取消选做必须发 false —— 这条最容易被 truthy 判断写坏。

    若实现写成 `if (checked) patch.is_optional = true`, 取消时不发该
    字段, 界面却照样提示"已保存", 而选做状态纹丝不动。
    """
    assert live["optPrefilledChecked"] is True, "重新打开应带出已勾选状态"
    body = live["unmarkPatchBody"]
    assert body is not None, "取消选做没有发出 PATCH —— 被当成'没有改动'了"
    assert body.get("is_optional") is False, \
        f"取消选做必须显式发 false, 实际: {body}"


@needs_jsdom
def test_optional_untouched_sends_nothing(live):
    """没动选做时不能夹带该字段。

    由 test_edit_sends_only_changed_fields 的精确字段集断言间接覆盖,
    这里补一条正面表述: setIfChanged 的存在意义就是防止这种夹带。
    """
    assert live["patchesAfterNoop"] == 0
    assert live["patchesAfterEsc"] == 0


# ====== 6b. 预计用时按作业类型自动预填 (2026-10-02 用户需求) ======
#
# 需求原文: 「预计用时在新增操作时, 如果为空, 根据设置
# 「各作业类型预估用时」的作业类型可以自动匹配预填」
#
# 基线配置取自 settings 的 task_type_baseline_minutes, harness 里注入
# { 背诵:15, 订正:25, 听写:8 }。其中「听写」是配置里的自定义类型, 不在
# 任何硬编码列表里 —— 用它来验证候选与预填确实跟着配置走。

@needs_jsdom
def test_manual_form_field_order(live):
    """手动添加的字段顺序与设计一致, 且与编辑弹窗相同。"""
    spec = ["归属日", "科目", "作业项", "作业类型", "预计用时", "是否选做", "截止时间"]
    assert live["manualFieldOrder"] == spec, f"字段顺序不符: {live['manualFieldOrder']}"


@needs_jsdom
def test_estimate_not_hardcoded_to_15(live):
    """预计用时不得写死初值。

    原来写死 15(或 estDefault({task_type:'other'}) —— 那个键在配置里
    根本不存在, 恒定落到 15), 于是"空才填"的规则永远不成立, 家长在
    设置页配的类型基线永远不生效 —— 那正是 2026-09-29 报障的形态。

    打开时应当按当前科目给出基线(此处语文=20), 而不是硬编码值。
    """
    assert live["manualStartsEmpty"] is False, "打开时应按科目基线预填, 而不是留空"
    assert live["estOnOpen"] == "20", \
        f"打开时应按科目基线(语文=20)预填, 实际 {live['estOnOpen']}"


@needs_jsdom
def test_estimate_prefilled_by_task_type(live):
    """选不同类型要给出对应的基线值, 且能连续改。

    连续修改这条尤其关键: 曾经用 `value !== ''` 判断"家长是否手输过",
    首次预填后框里就有数字, 于是从背诵(15)改成订正(25)会被拒绝更新,
    数字永远停在第一次的结果。
    """
    assert live["estAfterType15"] == "15", "背诵 未预填成 15"
    assert live["estAfterType25"] == "25", \
        f"改选订正后未更新为 25, 实际 {live['estAfterType25']}"


@needs_jsdom
def test_prefill_follows_custom_type_from_settings(live):
    """设置里加的自定义类型也要能预填。

    「听写=8」只存在于注入的配置里。若实现写死了类型列表, 这条会拿到
    15 或空 —— 也就是"设置改了没反应", 属于 2026-09-29 同类问题。
    """
    assert live["estAfterCustomType"] == "8", \
        f"自定义类型未按配置预填: {live['estAfterCustomType']}"
    assert "听写" in live["comboOptions"], \
        f"候选里没有设置中的自定义类型: {live['comboOptions']}"


@needs_jsdom
def test_combo_pick_closes_menu_and_fills_input(live):
    """点候选后: 输入框拿到值、浮层保持收起。

    浮层重新弹回是真实踩过的坑 —— pick() 里派发 input 通知预填、又调
    input.focus(), 两者都会触发 open(), 表现是"点了选项像没点上"。
    运行时断言比只查代码更能守住这个顺序。
    """
    r = live["comboMenuClosedAfterPick"]
    assert r is not None, "未取到候选"
    assert r["value"], "点候选后输入框应被填上"
    assert r["hidden"] is True, "选中后浮层应保持收起, 否则会弹回来"


@needs_jsdom
def test_prefill_never_overwrites_typed_value(live):
    """家长手填过的数字不能被改类型覆盖。

    这是"智能预填"最容易被投诉的点: 刚填 40, 改下类型变成 15, 家长会
    认为是系统改了他的输入。
    """
    assert live["estKeepsUserValue"] == "40", \
        f"家长手填值被覆盖成 {live['estKeepsUserValue']}"


@needs_jsdom
def test_prefill_refills_after_manual_clear(live):
    """清空后重新选类型应再次预填。"""
    assert live["estRefillsAfterClear"] == "25"


@needs_jsdom
def test_prefill_falls_back_to_subject(live):
    """类型留空时退回科目基线, 不该显示空白。"""
    assert live["estBySubject"] == "15", \
        f"类型留空时应回退到科目基线(数学=15), 实际 {live['estBySubject']}"


@needs_jsdom
def test_manual_submit_falls_back_when_estimate_empty(live):
    """提交时 est_minutes 必须合法, 且带上作业类型。

    后端 est_minutes 必填 (ge=1, le=180), 前端若把 NaN 发过去, 家长
    只会看到"入库失败", 不知道是哪个框的问题。
    """
    assert live["manualPosted"] == 1, (
        f"应能提交成功; POST urls={live.get('manualPostUrls')}")
    rec = live["manualBody"]
    assert rec is not None, f"未捕获入库请求; 所有 POST={live.get('manualPostUrls')}"
    # posted 数组存的是 {url, body} 记录, 请求体在 rec['body'] 里。
    body = rec["body"]
    item = body["items"][0]
    assert isinstance(item["est_minutes"], int), \
        f"est_minutes 必须是整数, 实际 {item['est_minutes']!r}"
    assert 1 <= item["est_minutes"] <= 180, f"est_minutes 越界: {item['est_minutes']}"
    assert item["est_minutes"] == 25, f"应按订正基线取 25, 实际 {item['est_minutes']}"
    assert item["task_type"] == "订正", f"作业类型未入库: {item}"


@needs_jsdom
def test_estimate_refills_immediately_after_clear(live):
    """家长清空后应立即按当前类型重填, 而不是从此不再自动填。

    清空后若预填永久失灵, 家长就只能手动敲每一个数字 —— 与需求
    「如果为空就预填」相悖。
    """
    assert live["estAfterFinalClear"] == "25", \
        f"清空后应立刻按订正重填 25, 实际 {live['estAfterFinalClear']!r}"


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
