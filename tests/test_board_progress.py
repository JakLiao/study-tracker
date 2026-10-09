"""看板进度条口径测试 (2026-09-28).

修掉的 bug: 进度条显示"已收尾 7 / 必做 7 · 100%", 但下面还挂着
"今天还有 1 项要做" —— 必做明明没做完, 却显示 100%。

根因是分子分母不同源:
  - 分子 settled = done + skipped, 而 done/skipped 在循环里统计了
    全部 rows(含选做)
  - 分母 required = len(rows) - len(optional_items), 把选做减掉了
于是选做一旦完成, 分子就凭空多出 1, 进度被抬高到 100%。截图那天的
数据正是: 选做 1 项已完成 + 必做跳过 6 项 + 必做还剩 1 项 => 7/7。

月历接口 calendar_summary 一直是按 is_optional 分流的(且有不变式测试
守着), 只有看板这条路径没被测过, 所以 bug 溜了过去。本文件把月历的
不变式搬到看板上, 堵住同一个漏洞。
"""
from __future__ import annotations

import pytest

from app.services.assignment_service import build_board_for


def _item(**overrides):
    item = {
        "subject": "语文",
        "task_name": "测试作业",
        "due_date": "2026-09-21",
        "deadline_time": None,
        "is_optional": False,
        "est_minutes": 20,
        "task_type": "other",
        "source_span": "(测试)",
        "confidence": "high",
    }
    item.update(overrides)
    return item


def _seed(client, items):
    r = client.post("/api/assignments/batch", json={"batch_id": None, "items": items})
    assert r.status_code == 200, r.text
    return r.json()


def _progress(client, date="2026-09-21"):
    from datetime import date as _date
    y, m, d = (int(x) for x in date.split("-"))
    return build_board_for(_date(y, m, d), _date(y, m, d))["progress"]


def _set_status(client, ids, status):
    for aid in ids:
        r = client.patch(f"/api/assignments/{aid}/status", json={"status": status})
        assert r.status_code == 200, r.text


# ====== 核心回归: 分子分母必须同源 ======

def test_completed_optional_does_not_inflate_progress(app_client):
    """本 bug 的最小复现: 选做完成不得抬高必做进度.

    1 项必做待完成 + 1 项选做已完成时, 应显示 0/1 而不是 1/1。
    修复前 done 把选做也数进去, settled=1 而 required=1 => 100%。
    """
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="必做还没做", subject="语文"),
        _item(due_date="2026-09-21", task_name="选做已完成", subject="数学", is_optional=True),
    ])["created_ids"]
    assert len(ids) == 2
    _set_status(app_client, [ids[1]], "done")

    p = _progress(app_client)
    assert p["required"] == 1
    assert p["done"] == 0, "选做的完成被算进了必做 done"
    assert p["settled"] == 0
    assert p["pct"] == 0, f"必做一项没做却显示 {p['pct']}%"


def test_skipped_optional_does_not_inflate_progress(app_client):
    """跳过同理 —— 选做被跳过也不该计入必做进度."""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="必做还没做", subject="语文"),
        _item(due_date="2026-09-21", task_name="选做跳过", subject="数学", is_optional=True),
    ])["created_ids"]
    _set_status(app_client, [ids[1]], "skipped")

    p = _progress(app_client)
    assert p["skipped"] == 0
    assert p["settled"] == 0
    assert p["pct"] == 0


def test_screenshot_scenario_reproduces(app_client):
    """按用户截图那天的数据复现: 必做 6 项跳过 + 1 项待做 + 选做 1 项完成.

    修复前: settled=7 (含选做) / required=7 => 100%
    修复后: settled=6 / required=7 => 86%
    """
    items = [
        _item(due_date="2026-09-21", task_name=f"必做{i}", subject="语文") for i in range(6)
    ] + [
        _item(due_date="2026-09-21", task_name="还没做的必做", subject="数学"),
        _item(due_date="2026-09-21", task_name="选做已完成", subject="英语", is_optional=True),
    ]
    ids = _seed(app_client, items)["created_ids"]
    # 8 条 = 必做 0-5 + 还没做的必做 + 选做已完成
    assert len(ids) == 8
    _set_status(app_client, ids[:6], "skipped")   # 前 6 条必做跳过
    _set_status(app_client, [ids[7]], "done")      # 最后一条是选做

    p = _progress(app_client)
    assert p["required"] == 7
    assert p["settled"] == 6
    assert p["pct"] == 86
    assert p["pct"] != 100


# ====== 不变式 (对齐月历 test_day_metadata_sums_consistently) ======

def test_board_sums_consistently(app_client):
    """不变式: settled <= required, 且 required + optional == total."""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="a", subject="语文"),
        _item(due_date="2026-09-21", task_name="b", subject="数学"),
        _item(due_date="2026-09-21", task_name="c", subject="英语", is_optional=True),
    ])["created_ids"]
    _set_status(app_client, [ids[0]], "done")
    _set_status(app_client, [ids[2]], "done")

    p = _progress(app_client)
    assert p["settled"] == p["done"] + p["skipped"]
    assert p["required"] + p["optional"] == p["total"]
    # 这条就是原 bug 破坏的不变式: 分子含选做时 settled 会超过 required
    assert p["settled"] <= p["required"]


def test_board_and_calendar_use_same_denominator(app_client):
    """看板与月历对同一天必须给出一致的进度.

    两套口径分叉过一次(看板分子含选做、月历不含), 家长在两个页面会
    看到互相矛盾的进度。这里钉住"同源"这个前提。
    """
    from app.services.assignment_service import calendar_summary

    _seed(app_client, [
        _item(due_date="2026-09-21", task_name="必做A", subject="语文"),
        _item(due_date="2026-09-21", task_name="必做B", subject="数学"),
        _item(due_date="2026-09-21", task_name="选做C", subject="英语", is_optional=True),
    ])
    cal = calendar_summary(2026, 9)["days"]["2026-09-21"]
    p = _progress(app_client)

    assert p["required"] == cal["required"]
    assert p["settled"] == cal["settled"]
    assert p["optional"] == cal["optional"]


# ====== 选做计数(副行显示用) ======

def test_optional_counts_exposed(app_client):
    """选做的完成/跳过数要单独返回, 前端副行才能显示"选做 x/y"."""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="选做A", subject="语文", is_optional=True),
        _item(due_date="2026-09-21", task_name="选做B", subject="数学", is_optional=True),
        _item(due_date="2026-09-21", task_name="选做C", subject="英语", is_optional=True),
    ])["created_ids"]
    _set_status(app_client, [ids[0]], "done")
    _set_status(app_client, [ids[1]], "skipped")

    p = _progress(app_client)
    assert p["optional"] == 3
    assert p["optional_done"] == 1
    assert p["optional_skipped"] == 1
    # 选做无论如何都不影响必做进度
    assert p["required"] == 0
    assert p["pct"] == 100


# ====== 总用时 (2026-10-09 用户要求"显出孩子的努力成果") ======
#
# 口径 (用户确认):
#   - 只算真正做完的项 (status=done), 跳过的项没投入时间, 不计
#   - 选做做了就是做了, 计入 (与周复盘偏差统计刻意排除选做不同:
#     那边排除是为了让"估时准不准"的评估样本干净, 这里是记录投入)
#   - actual 为空时按预估计, 与周复盘 COALESCE 口径一致
#
# 这里最容易被改坏的是"跳过不计" —— 若照 est_total 那套全量累加,
# 跳过项会被算进总用时, 等于凭空替孩子加分, 与本功能的本意相反。


def test_actual_total_counts_done_with_filled_actual(app_client):
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="a", subject="语文", est_minutes=15),
        _item(due_date="2026-09-21", task_name="b", subject="数学", est_minutes=25),
    ])["created_ids"]
    _set_status(app_client, ids, "done")
    app_client.patch(f"/api/assignments/{ids[0]}/completion", json={"actual_minutes": 45})
    app_client.patch(f"/api/assignments/{ids[1]}/completion", json={"actual_minutes": 40})

    # 45 + 40, 不是 15 + 25 —— 有实测值就必须用实测值
    assert _progress(app_client)["actual_total"] == 85


def test_actual_total_falls_back_to_est_when_unfilled(app_client):
    """未填实际用时按预估计入, 而不是不计入 (用户口径: 缺省即与预估一致)。"""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="a", subject="语文", est_minutes=15),
        _item(due_date="2026-09-21", task_name="b", subject="数学", est_minutes=25),
    ])["created_ids"]
    _set_status(app_client, ids, "done")

    assert _progress(app_client)["actual_total"] == 40


def test_actual_total_excludes_skipped(app_client):
    """跳过的项没投入时间, 绝不能计入总用时。"""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="做了", subject="语文", est_minutes=20),
        _item(due_date="2026-09-21", task_name="跳过", subject="数学", est_minutes=30),
    ])["created_ids"]
    _set_status(app_client, [ids[0]], "done")
    _set_status(app_client, [ids[1]], "skipped")

    p = _progress(app_client)
    assert p["settled"] == 2        # 进度上跳过算已收尾
    assert p["actual_total"] == 20  # 但总用时不含它


def test_actual_total_excludes_todo(app_client):
    """还没做的项不计入 —— 总用时是"已投入", 不是"将要做"。"""
    _seed(app_client, [
        _item(due_date="2026-09-21", task_name="还没做", subject="语文", est_minutes=20),
    ])
    assert _progress(app_client)["actual_total"] == 0


def test_actual_total_includes_done_optional(app_client):
    """选做完成了就计入 —— 那是孩子真实投入的时间。"""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="选做", subject="英语",
              est_minutes=10, is_optional=True),
    ])["created_ids"]
    _set_status(app_client, ids, "done")

    p = _progress(app_client)
    assert p["optional_done"] == 1
    assert p["actual_total"] == 10


def test_actual_total_can_exceed_est_total(app_client):
    """实测高于预估时 actual_total > est_total 是正常的。

    不是断言"必须小于等于", 而是钉住"别为了好看去截断/取小":
    孩子真的花了 3 小时就是 180 分钟, 显示成预估值才是错的。
    """
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="a", subject="语文", est_minutes=10),
    ])["created_ids"]
    _set_status(app_client, ids, "done")
    app_client.patch(f"/api/assignments/{ids[0]}/completion", json={"actual_minutes": 180})

    p = _progress(app_client)
    assert p["actual_total"] == 180
    assert p["est_total"] == 10


def test_actual_total_is_zero_on_empty_day(app_client):
    _seed(app_client, [_item(due_date="2026-09-22", task_name="别的日期", subject="语文")])
    assert _progress(app_client)["actual_total"] == 0


def test_optional_fields_present_even_without_optional(app_client):
    """没有选做时字段也要在, 否则前端 p.optional_done 读到 undefined。

    前端已用 || 0 兜底, 但接口契约上这两个字段应始终存在。
    """
    _seed(app_client, [_item(due_date="2026-09-21", task_name="只有必做")])
    p = _progress(app_client)
    assert p["optional"] == 0
    assert p["optional_done"] == 0
    assert p["optional_skipped"] == 0


# ====== 边界 ======

def test_pct_never_exceeds_100(app_client):
    """pct 不得超过 100 —— 否则前端 width 撑破, 进度条反而变空."""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name=f"必做{i}", subject="语文") for i in range(4)
    ] + [
        _item(due_date="2026-09-21", task_name="选做", subject="数学", is_optional=True),
    ])["created_ids"]
    _set_status(app_client, ids, "done")

    p = _progress(app_client)
    assert p["required"] == 4
    assert p["settled"] == 4
    assert p["pct"] == 100


def test_no_required_yet_optional_only(app_client):
    """全是选做时不该出现除零错误, 也不该显示成"还没做完"."""
    _seed(app_client, [
        _item(due_date="2026-09-21", task_name=f"选做{i}", subject="语文", is_optional=True)
        for i in range(2)
    ])
    p = _progress(app_client)
    assert p["required"] == 0
    assert p["settled"] == 0
    assert p["pct"] == 100


def test_empty_day_is_zero(app_client):
    p = _progress(app_client)
    assert p["total"] == 0
    assert p["pct"] == 0, "没作业时不该显示 100%"


@pytest.mark.parametrize("status,is_optional,exp_pct", [
    ("todo", False, 0),
    ("done", False, 100),
    ("skipped", False, 100),
    ("done", True, 0),
    ("skipped", True, 0),
])
def test_status_matrix(app_client, status, is_optional, exp_pct):
    """1 必做 + 1 选做, 只动其中一个的状态 —— 必做进度只由必做决定."""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="必做", subject="语文"),
        _item(due_date="2026-09-21", task_name="选做", subject="数学", is_optional=True),
    ])["created_ids"]
    target = ids[1] if is_optional else ids[0]
    _set_status(app_client, [target], status)

    p = _progress(app_client)
    assert p["pct"] == exp_pct, f"{status}/optional={is_optional} => {p['pct']}%"
