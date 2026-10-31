"""M2 执行台: 逾期任务可见性.

背景: 截止提醒原先只查"未来 7 天"(deadline_time::date > today)。
一条作业一旦错过截止, 就从提醒区里彻底消失 —— 恰恰是最容易漏交、
也最该被看见的那条。M2 补两处:

  1. 行内标记: 今日看板的条目若已过截止且未收尾, DTO 带 is_overdue,
     界面标「已逾期」。判定口径必须排除 done(已完成) 与 skipped
     (家长主动"今天不做"), 否则等于对已经处理过的条目重复指责。
  2. 往期提醒: 看板返回 overdue_items, 覆盖"今天之前"的逾期条目;
     今日的逾期条目已经在科目卡片里, 不重复列。窗口限定 30 天、最多
     20 条, 避免历史积压把提醒区变成无底洞。

这些断言同时锁住"计算字段不落库": is_overdue 依赖"现在", 应由 DTO
在读取时计算 —— 若哪天改成写库, 状态/时间一变就会过期失效。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _today():
    # 与后端 _local_today 同口径: 本地日期。测试机与 PG 共用同一时区。
    return datetime.now().astimezone().date()


def _add(app_client, due: str, deadline: str | None, subject: str = "语文",
         task_name: str = "逾期测试任务") -> int:
    payload = {
        "batch_id": None,
        "items": [{
            "subject": subject,
            "task_name": task_name,
            "due_date": due,
            "deadline_time": deadline,
            "is_optional": False,
            "est_minutes": 20,
            "task_type": "背诵",
            "source_span": "(测试)",
            "confidence": "high",
        }],
    }
    r = app_client.post("/api/assignments/batch", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _get_item(app_client, aid: int, date_: str) -> dict:
    items = app_client.get(f"/api/assignments?date={date_}").json()["items"]
    return next(i for i in items if i["id"] == aid)


# ====== 1. DTO 的 is_overdue 计算口径 ======

def test_past_deadline_todo_is_overdue(app_client):
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    aid = _add(app_client, due, dl)
    assert _get_item(app_client, aid, due)["is_overdue"] is True


def test_no_deadline_is_not_overdue(app_client):
    """没有截止时间的任务永远不算逾期 —— 它本来就没有"到点"。"""
    d = _today().isoformat()
    aid = _add(app_client, d, None)
    assert _get_item(app_client, aid, d)["is_overdue"] is False


def test_future_deadline_is_not_overdue(app_client):
    d = _today()
    due = (d + timedelta(days=1)).isoformat()
    dl = (d + timedelta(days=2)).isoformat() + "T08:00:00"
    aid = _add(app_client, due, dl)
    assert _get_item(app_client, aid, due)["is_overdue"] is False


def test_done_item_is_not_overdue(app_client):
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    aid = _add(app_client, due, dl)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    assert _get_item(app_client, aid, due)["is_overdue"] is False


def test_skipped_item_is_not_overdue(app_client):
    """跳过 = 家长主动决定今天不做, 不该再报红。"""
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    aid = _add(app_client, due, dl)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "skipped"})
    assert _get_item(app_client, aid, due)["is_overdue"] is False


# ====== 2. 看板 overdue_items ======

def test_board_lists_past_overdue_items(app_client):
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    aid = _add(app_client, due, dl)
    board = app_client.get(f"/api/board?date={d.isoformat()}").json()
    ids = [i["id"] for i in board["overdue_items"]]
    assert aid in ids
    assert board["overdue_items"][0]["is_overdue"] is True


def test_board_excludes_done_and_skipped_from_overdue(app_client):
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    done_id = _add(app_client, due, dl, task_name="已完成的")
    skip_id = _add(app_client, due, dl, task_name="已跳过的")
    app_client.patch(f"/api/assignments/{done_id}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{skip_id}/status", json={"status": "skipped"})
    board = app_client.get(f"/api/board?date={d.isoformat()}").json()
    ids = {i["id"] for i in board["overdue_items"]}
    assert done_id not in ids
    assert skip_id not in ids


def test_board_does_not_duplicate_todays_expired_item(app_client):
    """今天到点未做的条目由行内 is_overdue 标记, 不进往期提醒区。

    两处都列的话, 家长会在同一天看到同一条作业两遍 —— 提醒就会贬值。
    """
    d = _today()
    now = datetime.now()
    dl_dt = now - timedelta(minutes=5)
    if dl_dt.date() != d:
        pytest.skip("测试时刻恰在午夜前 5 分钟内, 跳过当日逾期的边界场景")
    aid = _add(app_client, d.isoformat(), dl_dt.strftime("%Y-%m-%dT%H:%M:00"))
    board = app_client.get(f"/api/board?date={d.isoformat()}").json()
    ids = {i["id"] for i in board["overdue_items"]}
    assert aid not in ids, "当日逾期不应重复出现在往期提醒区"
    inline = [i for lst in board["by_subject"].values() for i in lst]
    target = next(i for i in inline if i["id"] == aid)
    assert target["is_overdue"] is True, "当日逾期必须在行内标记为逾期"


def test_board_overdue_window_is_30_days(app_client):
    """超过 30 天的逾期不再提醒, 否则历史积压会淹掉真正要看的几条。"""
    d = _today()
    old_day = d - timedelta(days=40)
    aid = _add(app_client, old_day.isoformat(),
               old_day.isoformat() + "T08:00:00", task_name="很久以前")
    board = app_client.get(f"/api/board?date={d.isoformat()}").json()
    assert aid not in {i["id"] for i in board["overdue_items"]}


def test_history_board_has_no_overdue_block(app_client):
    """看历史日期时不弹"已逾期"—— 那时它只是那天的一条普通作业。"""
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    _add(app_client, due, dl)
    board = app_client.get(f"/api/board?date={due}").json()
    assert board["overdue_items"] == []


def test_board_overdue_items_capped(app_client):
    """最多 20 条: 接口是公开返回, 不能让一个家庭积压的任务无限返回。"""
    d = _today()
    due = (d - timedelta(days=2)).isoformat()
    dl = (d - timedelta(days=1)).isoformat() + "T08:00:00"
    for i in range(22):
        _add(app_client, due, dl, task_name=f"积压任务{i}")
    board = app_client.get(f"/api/board?date={d.isoformat()}").json()
    assert len(board["overdue_items"]) == 20


# ====== 3. 前端契约 ======

@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


def test_frontend_renders_inline_overdue_tag(app_js):
    assert "item.is_overdue" in app_js
    assert 'class="overdue-tag"' in app_js
    assert "已逾期" in app_js


def test_frontend_renders_overdue_block(app_js):
    assert "b.overdue_items" in app_js
    assert "overdue-warn" in app_js


def test_overdue_reminder_is_actionable(app_js):
    """逾期提醒必须能点回那一天处理, 否则只报不救。

    提醒区只说"有一条逾期了"却不给入口, 家长还得去日历上翻是哪天;
    任务名带 data-date 并点击切到该日看板, 处理闭环才成立。
    """
    assert "overdue-link" in app_js
    assert "data-date=" in app_js
    assert "_boardDate = link.dataset.date" in app_js


def test_styles_define_overdue_tag(styles):
    assert re.search(r"\.overdue-tag\s*\{", styles)
    assert re.search(r"\.overdue-warn\s*\{", styles)
    assert re.search(r"\.overdue-link\s*\{", styles)
