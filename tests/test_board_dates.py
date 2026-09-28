"""看板多日期支持测试 (2026-09-28).

覆盖:
  - /board/dates 只返回"有作业"的日期 (长假期间不返回空白日期)
  - 标签按与今天的关系显示 (今天/昨天/N天前/M月D日)
  - /board?date= 能取指定日期, 且结构与 /board/today 一致
  - 历史日期不返回 upcoming_deadlines (对已过去的日期提醒无意义)
"""
from __future__ import annotations

from datetime import date, timedelta

from app.services import assignment_service


def _add(client, day: str, name: str = "作业", **over):
    item = {
        "subject": "语文",
        "task_name": name,
        "due_date": day,
        "est_minutes": 15,
        "source_span": "(测试)",
        "confidence": "high",
    }
    item.update(over)
    r = client.post("/api/assignments/batch", json={"batch_id": None, "items": [item]})
    assert r.status_code == 200, r.text
    return r


def test_dates_returns_only_days_with_data(app_client):
    """空白日期不返回 —— 长假期间不该占掉 3 个位置."""
    today = date.today()
    _add(app_client, today.isoformat(), "今天作业")
    # 制造一个 6 天空洞, 模拟长假
    old = (today - timedelta(days=6)).isoformat()
    _add(app_client, old, "长假前作业")

    r = app_client.get("/api/board/dates", params={"limit": 3})
    assert r.status_code == 200
    days = [d["date"] for d in r.json()["dates"]]
    assert days[0] == today.isoformat()
    assert old in days
    # 中间 5 天没有作业, 一个都不该出现
    for i in range(1, 6):
        assert (today - timedelta(days=i)).isoformat() not in days


def test_dates_limit_and_order(app_client):
    today = date.today()
    for i in range(5):
        _add(app_client, (today - timedelta(days=i)).isoformat(), f"作业{i}")
    r = app_client.get("/api/board/dates", params={"limit": 3})
    days = [d["date"] for d in r.json()["dates"]]
    assert len(days) == 3
    assert days == sorted(days, reverse=True), "必须最新在前"


def test_dates_carries_counts_for_tab_badges(app_client):
    today = date.today()
    _add(app_client, today.isoformat(), "甲")
    _add(app_client, today.isoformat(), "乙")
    aid = _add(app_client, today.isoformat(), "丙").json()["created_ids"][0]
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})

    r = app_client.get("/api/board/dates", params={"limit": 1})
    d = r.json()["dates"][0]
    assert d["total"] == 3
    assert d["done"] == 1
    assert d["is_today"] is True


def test_dates_empty_when_no_data(app_client):
    r = app_client.get("/api/board/dates", params={"limit": 3})
    assert r.status_code == 200
    assert r.json()["dates"] == []


def test_board_by_date_matches_today_shape(app_client):
    """指定今天时, /board 与 /board/today 应完全一致."""
    _add(app_client, date.today().isoformat(), "甲")
    a = app_client.get("/api/board/today").json()
    b = app_client.get("/api/board", params={"date": date.today().isoformat()}).json()
    for k in ("date", "progress", "has_data", "label", "is_today"):
        assert a[k] == b[k], f"{k} 不一致"


def test_board_by_date_returns_history(app_client):
    """历史日期可取, 且带 label 便于进度条区分."""
    old = (date.today() - timedelta(days=3)).isoformat()
    _add(app_client, old, "历史作业")
    r = app_client.get("/api/board", params={"date": old})
    assert r.status_code == 200
    b = r.json()
    assert b["date"] == old
    assert b["has_data"] is True
    assert b["is_today"] is False
    assert b["label"] != "今天"
    assert len(b["by_subject"]["语文"]) == 1


def test_history_board_has_no_upcoming_deadlines(app_client):
    """看历史日期时不提醒"未来 7 天到期" —— 对已过去的日子无意义."""
    old = (date.today() - timedelta(days=2)).isoformat()
    _add(app_client, old, "历史作业")
    _add(app_client, date.today().isoformat(), "今天作业")
    b = app_client.get("/api/board", params={"date": old}).json()
    assert b["upcoming_deadlines"] == []


def test_label_relative_to_today():
    today = date(2026, 9, 28)
    assert assignment_service._date_label(today, today) == "今天"
    assert assignment_service._date_label(today - timedelta(days=1), today) == "昨天"
    assert assignment_service._date_label(today - timedelta(days=2), today) == "前天"
    assert assignment_service._date_label(today - timedelta(days=4), today) == "4 天前"
    # 超过一周用具体日期
    assert assignment_service._date_label(date(2026, 9, 1), today) == "9月1日"


def test_list_recent_dates_respects_limit():
    assert assignment_service.list_recent_dates(0) == []
    assert assignment_service.list_recent_dates(-1) == []
