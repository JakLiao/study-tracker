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
    """库里一条作业都没有时, 仍然返回今天的空卡。

    不再返回 []: "今天永远在第一位" 是接口契约的一部分, 调用方不该
    依赖"恰好有历史数据"才拿得到今天。前端有 dates.length < 2 的
    判断, 单卡时整行不渲染, 所以这里的 [] 与 [今天] 视觉上等价。
    """
    r = app_client.get("/api/board/dates", params={"limit": 3})
    assert r.status_code == 200
    days = r.json()["dates"]
    assert len(days) == 1
    assert days[0]["date"] == date.today().isoformat()
    assert days[0]["label"] == "今天"
    assert days[0]["total"] == 0
    assert days[0]["has_data"] is False


def test_dates_keeps_today_even_without_homework(app_client):
    """今天没作业时, 第一张卡也必须是"今天" (2026-09-29 用户反馈)。

    回归点: SQL 的 GROUP BY due_date 会把零作业的日子抹掉, 于是第一张
    卡变成"昨天", 前端又默认高亮第一张 —— 深色底 + "昨天" 会被读成
    "今天是空白的一天", 家长以为今天的作业被漏掉了。
    """
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    _add(app_client, yesterday, "昨天的作业")

    r = app_client.get("/api/board/dates", params={"limit": 3})
    days = r.json()["dates"]
    assert days[0]["date"] == today.isoformat(), "今天必须占住第一位"
    assert days[0]["label"] == "今天"
    assert days[0]["is_today"] is True
    assert days[0]["has_data"] is False
    assert days[0]["total"] == 0
    # 昨天的记录仍然保留, 家长还能切回去
    assert any(d["date"] == yesterday for d in days)


def test_dates_injected_today_respects_limit(app_client):
    """补出来的今天卡也占 limit 的名额, 不能渲染成 limit+1 张。"""
    today = date.today()
    for i in range(1, 6):          # 只造历史, 今天留空
        _add(app_client, (today - timedelta(days=i)).isoformat(), f"历史{i}")
    r = app_client.get("/api/board/dates", params={"limit": 3})
    days = r.json()["dates"]
    assert len(days) == 3
    assert days[0]["date"] == today.isoformat()
    # 被挤掉的是最旧的那天
    assert (today - timedelta(days=5)).isoformat() not in [d["date"] for d in days]


def test_dates_ignores_future_dates(app_client):
    """未来日期不能挤掉今天 —— 否则深色底又落到非今天的那张卡上。"""
    today = date.today()
    _add(app_client, today.isoformat(), "今天的作业")
    _add(app_client, (today + timedelta(days=2)).isoformat(), "两天后的作业")

    r = app_client.get("/api/board/dates", params={"limit": 3})
    days = r.json()["dates"]
    assert days[0]["date"] == today.isoformat()
    assert all(d["date"] <= today.isoformat() for d in days), "不应返回未来日期"


def test_dates_marks_days_with_data(app_client):
    """has_data 让前端能区分"空卡"和"真有作业但没做完"。"""
    today = date.today()
    _add(app_client, today.isoformat(), "甲")
    _add(app_client, (today - timedelta(days=1)).isoformat(), "乙")
    days = app_client.get("/api/board/dates", params={"limit": 3}).json()["dates"]
    by_date = {d["date"]: d for d in days}
    assert by_date[today.isoformat()]["has_data"] is True
    assert by_date[(today - timedelta(days=1)).isoformat()]["has_data"] is True


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
