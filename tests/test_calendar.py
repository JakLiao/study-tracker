"""作业日历按月汇总测试 (2026-09-28).

月历页面依赖这个接口, 覆盖:
  1. 按月汇总只返回当月有作业的日期 (不返回空占位, 前端自己补格子)
  2. 必做与选做的完成数必须分开统计
  3. 月历格子定位所需的日历元信息 (first_weekday / days_in_month)
  4. 跨年边界 (12 月 -> 次年 1 月)
  5. 非法月份返回 400

为什么必须拆开统计: 日历格子要判断"必做是否全完成"。若只返回合并的
done, 就会出现"选做全做完了、必做一项没做, 却显示全完成"的误判 ——
家长看到绿格子就不会再去检查必做。
"""
from __future__ import annotations

import pytest

from app.services.assignment_service import calendar_summary


def _item(**overrides):
    """单条作业。不同科目 + 不同名字, 否则同一天会被去重挡掉."""
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


def _cal(client, year=2026, month=9):
    r = client.get(f"/api/assignments/calendar?year={year}&month={month}")
    assert r.status_code == 200, r.text
    return r.json()


# ====== 汇总结构 ======

def test_empty_month_returns_no_days(app_client):
    r = _cal(app_client)
    assert r["days"] == {}


def test_only_days_with_assignments_are_returned(app_client):
    """不返回当月每一天的空占位 —— 前端按 first_weekday 自己补空格."""
    _seed(app_client, [_item(due_date="2026-09-10"), _item(due_date="2026-09-20")])
    r = _cal(app_client)
    assert set(r["days"].keys()) == {"2026-09-10", "2026-09-20"}


def test_calendar_metadata(app_client):
    r = _cal(app_client, 2026, 9)
    assert r["year"] == 2026
    assert r["month"] == 9
    assert r["days_in_month"] == 30
    # 2026-09-01 是周二 -> Monday=0 记作 1, 与前端 cal-weekdays 的"一二三四五六日"对齐
    assert r["first_weekday"] == 1


# ====== 必做 / 选做 拆分 ======

def test_required_and_optional_are_separated(app_client):
    _seed(app_client, [
        _item(due_date="2026-09-21", task_name="必做A"),
        _item(due_date="2026-09-21", task_name="选做A", is_optional=True),
    ])
    day = _cal(app_client)["days"]["2026-09-21"]
    assert day["total"] == 2
    assert day["required"] == 1
    assert day["optional"] == 1


def test_required_done_excludes_optional(app_client):
    """核心回归: 选做完成不能被算成必做完成.

    如果 required_done 混入了选做, 家长完成全部选做后日历就变绿,
    必做没做的作业再也不会被看见。
    """
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="选做1", is_optional=True),
        _item(due_date="2026-09-21", task_name="选做2", is_optional=True),
    ])["created_ids"]
    assert len(ids) == 2
    for aid in ids:
        app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})

    day = _cal(app_client)["days"]["2026-09-21"]
    assert day["required"] == 0
    assert day["required_done"] == 0
    assert day["optional_done"] == 2


def test_skipped_is_settled_but_not_done(app_client):
    """跳过算"已收尾"但不算完成 —— 与看板口径一致."""
    ids = _seed(app_client, [_item(due_date="2026-09-21", task_name="做不完的")])["created_ids"]
    app_client.patch(f"/api/assignments/{ids[0]}/status", json={"status": "skipped"})

    day = _cal(app_client)["days"]["2026-09-21"]
    assert day["required_done"] == 0
    assert day["required_skipped"] == 1
    assert day["settled"] == 1


def test_settled_counts_done_plus_skipped(app_client):
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="做完的", subject="语文"),
        _item(due_date="2026-09-21", task_name="跳过的", subject="数学"),
        _item(due_date="2026-09-21", task_name="还没动的", subject="英语"),
    ])["created_ids"]
    assert len(ids) == 3
    app_client.patch(f"/api/assignments/{ids[0]}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{ids[1]}/status", json={"status": "skipped"})

    day = _cal(app_client)["days"]["2026-09-21"]
    assert day["required"] == 3
    assert day["required_done"] == 1
    assert day["required_skipped"] == 1
    assert day["settled"] == 2


# ====== 边界 ======

def test_december_and_january_do_not_leak(app_client):
    """12 月的作业不该出现在 1 月, 反之亦然."""
    _seed(app_client, [_item(due_date="2026-12-31", task_name="跨年作业")])
    assert "2026-12-31" in _cal(app_client, 2026, 12)["days"]
    assert _cal(app_client, 2027, 1)["days"] == {}


def test_december_metadata_crosses_year(app_client):
    r = _cal(app_client, 2026, 12)
    assert r["days_in_month"] == 31
    assert r["year"] == 2026


@pytest.mark.parametrize("month", [0, 13, 99])
def test_invalid_month_rejected(app_client, month):
    r = app_client.get(f"/api/assignments/calendar?year=2026&month={month}")
    assert r.status_code == 400


def test_other_month_not_included(app_client):
    _seed(app_client, [_item(due_date="2026-08-15", task_name="上个月的")])
    assert _cal(app_client, 2026, 9)["days"] == {}


def test_day_metadata_sums_consistently(app_client):
    """不变式: settled == required_done + required_skipped, 且不超过 total."""
    ids = _seed(app_client, [
        _item(due_date="2026-09-21", task_name="a", subject="语文"),
        _item(due_date="2026-09-21", task_name="b", subject="数学"),
        _item(due_date="2026-09-21", task_name="c", subject="英语", is_optional=True),
    ])["created_ids"]
    assert len(ids) == 3
    app_client.patch(f"/api/assignments/{ids[0]}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{ids[2]}/status", json={"status": "done"})

    day = _cal(app_client)["days"]["2026-09-21"]
    assert day["settled"] == day["required_done"] + day["required_skipped"]
    assert day["required"] + day["optional"] == day["total"]
    assert day["settled"] <= day["required"]
