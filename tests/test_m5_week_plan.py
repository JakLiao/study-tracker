"""M5 周课表 / 周负荷 / 周计划.

锁住的五条口径:

1. **课表冲突用半开区间.** 16:00-17:00 与 17:00-18:00 首尾相接不算撞车,
   只有真正交叉才报 —— 否则一节连着一节的排法永远存不进去。
2. **负荷 = 课表 + 待办剩余 + 未生成的计划项.** 已生成到任务池的计划项
   不能再叠加一次, 否则"点了生成"反而让当天显得更满。
3. **只生成今天及以后.** 给过去的日子补任务是篡改历史, 复盘会失真;
   重复生成靠 assign 去重, 不会越长越多。
4. **计划项的科目/项目归属与任务池同一口径.** 学科挂科目、培训/习惯挂
   项目, 两种错配都要在写入前拦住。
5. **前端只用自绘弹窗.** 冲突提示要能留在弹窗里让家长改, 浏览器原生
   confirm 会直接吞掉上下文。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "app" / "static" / "app.js"
INDEX_HTML = ROOT / "app" / "static" / "index.html"
STYLES = ROOT / "app" / "static" / "styles.css"


def _today() -> date:
    return datetime.now().astimezone().date()


def _project(app_client, name: str = "钢琴", project_type: str = "piano") -> int:
    r = app_client.post("/api/projects", json={"name": name, "project_type": project_type})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _slot(app_client, project_id: int, weekday: int, start: str, end: str,
          note: str | None = None):
    return app_client.post("/api/schedule-slots", json={
        "project_id": project_id, "weekday": weekday,
        "start_time": start, "end_time": end, "note": note,
    })


def _plan(app_client, **kwargs):
    body = {"task_name": "每日阅读", "est_minutes": 20, "weekdays": [0]}
    body.update(kwargs)
    return app_client.post("/api/plan-items", json=body)


def _week(app_client, d: str) -> dict:
    r = app_client.get(f"/api/week-plan?date={d}")
    assert r.status_code == 200, r.text
    return r.json()


# ====== 1. 课表 CRUD 与冲突 ======

def test_create_slot_returns_minutes(app_client):
    pid = _project(app_client)
    r = _slot(app_client, pid, 2, "16:30", "18:00", "琴房 3 号")
    assert r.status_code == 200, r.text
    slot = r.json()
    assert slot["project_id"] == pid
    assert slot["project_name"] == "钢琴"
    assert slot["weekday"] == 2
    assert slot["start_time"] == "16:30"
    assert slot["end_time"] == "18:00"
    assert slot["minutes"] == 90
    assert slot["note"] == "琴房 3 号"

    items = app_client.get("/api/schedule-slots").json()["items"]
    assert [s["id"] for s in items] == [slot["id"]]


def test_overlapping_slot_rejected(app_client):
    pid = _project(app_client)
    assert _slot(app_client, pid, 2, "16:30", "18:00").status_code == 200
    r = _slot(app_client, pid, 2, "17:30", "19:00")
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "4016"
    # 消息要说清和谁撞了, 家长才知道该改哪一条
    assert "钢琴" in err["message"] and "16:30-18:00" in err["message"]


def test_adjacent_slot_is_not_conflict(app_client):
    """首尾相接 (17:00 结束 / 17:00 开始) 是正常排法, 不该报冲突."""
    pid = _project(app_client)
    assert _slot(app_client, pid, 2, "16:00", "17:00").status_code == 200
    r = _slot(app_client, pid, 2, "17:00", "18:00")
    assert r.status_code == 200, r.text


def test_same_time_other_day_is_not_conflict(app_client):
    pid = _project(app_client)
    assert _slot(app_client, pid, 2, "16:30", "18:00").status_code == 200
    assert _slot(app_client, pid, 3, "16:30", "18:00").status_code == 200


def test_end_before_start_rejected(app_client):
    pid = _project(app_client)
    r = _slot(app_client, pid, 2, "18:00", "16:30")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_slot_with_unknown_project_rejected(app_client):
    r = _slot(app_client, 999999, 2, "16:30", "18:00")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4014"


def test_update_slot_and_clear_note(app_client):
    pid = _project(app_client)
    sid = _slot(app_client, pid, 2, "16:30", "18:00", "琴房").json()["id"]

    r = app_client.patch(f"/api/schedule-slots/{sid}", json={"note": None})
    assert r.status_code == 200, r.text
    assert r.json()["note"] is None

    # 只改结束时间, 开始/星期/项目都沿用库里的值
    r = app_client.patch(f"/api/schedule-slots/{sid}", json={"end_time": "17:30"})
    assert r.status_code == 200, r.text
    assert r.json()["start_time"] == "16:30"
    assert r.json()["end_time"] == "17:30"
    assert r.json()["minutes"] == 60


def test_update_slot_overlap_rejected_but_self_excluded(app_client):
    pid = _project(app_client)
    a = _slot(app_client, pid, 2, "16:00", "17:00").json()["id"]
    b = _slot(app_client, pid, 2, "17:00", "18:00").json()["id"]

    # 改自己 (start 从 16:00 挪到 16:30) 不该跟自己报冲突
    r = app_client.patch(f"/api/schedule-slots/{a}", json={"start_time": "16:30"})
    assert r.status_code == 200, r.text
    # 但会跟 b 撞上
    r = app_client.patch(f"/api/schedule-slots/{a}", json={"end_time": "17:30"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4016"


def test_delete_slot_and_missing_returns_4017(app_client):
    pid = _project(app_client)
    sid = _slot(app_client, pid, 2, "16:30", "18:00").json()["id"]
    r = app_client.delete(f"/api/schedule-slots/{sid}")
    assert r.status_code == 200, r.text
    assert app_client.get("/api/schedule-slots").json()["items"] == []

    r = app_client.delete(f"/api/schedule-slots/{sid}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4017"


# ====== 2. 计划项 CRUD ======

def test_create_plan_item_subject(app_client):
    r = _plan(app_client, task_kind="subject", subject="语文",
              task_name="每日阅读", est_minutes=20, weekdays=[0, 2, 4])
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["task_kind"] == "subject"
    assert item["subject"] == "语文"
    assert item["weekdays"] == [0, 2, 4]
    assert item["est_minutes"] == 20
    assert item["updated"] is False


def test_create_plan_item_is_idempotent(app_client):
    """同范围同名重复提交 → 更新星期与时长, 不新增第二条 (否则负荷算两遍)."""
    first = _plan(app_client, subject="语文", weekdays=[0, 2, 4], est_minutes=20).json()
    second = _plan(app_client, subject="语文", weekdays=[1, 3], est_minutes=30).json()
    assert second["id"] == first["id"]
    assert second["updated"] is True
    assert second["weekdays"] == [1, 3]
    assert second["est_minutes"] == 30
    assert len(app_client.get("/api/plan-items").json()["items"]) == 1


def test_plan_item_task_kind_validation(app_client):
    # 学科但没给科目
    r = _plan(app_client, task_kind="subject", subject=None)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"
    # 学科却挂了项目
    pid = _project(app_client)
    r = _plan(app_client, task_kind="subject", subject="语文", project_id=pid)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"
    # 培训却给了科目
    r = _plan(app_client, task_kind="training", subject="语文", project_id=pid)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_plan_item_unknown_project_rejected(app_client):
    r = _plan(app_client, task_kind="training", subject=None, project_id=999999)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4014"


def test_plan_item_weekdays_rejected_when_empty(app_client):
    r = _plan(app_client, subject="语文", weekdays=[])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_update_plan_item_kind_linkage(app_client):
    """只改 task_kind 时, 沿用的 subject 也要一起校验."""
    item = _plan(app_client, subject="语文", weekdays=[0]).json()
    r = app_client.patch(f"/api/plan-items/{item['id']}", json={"task_kind": "training"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4015"


def test_update_plan_item_weekdays_and_active(app_client):
    item = _plan(app_client, subject="语文", weekdays=[0, 1]).json()
    r = app_client.patch(f"/api/plan-items/{item['id']}",
                         json={"weekdays": [2, 4], "est_minutes": 30})
    assert r.status_code == 200, r.text
    assert r.json()["weekdays"] == [2, 4]
    assert r.json()["est_minutes"] == 30

    # 停用后默认列表看不到, 但 include_inactive 仍能查到
    r = app_client.patch(f"/api/plan-items/{item['id']}", json={"active": False})
    assert r.status_code == 200, r.text
    assert app_client.get("/api/plan-items").json()["items"] == []
    assert len(app_client.get("/api/plan-items?include_inactive=true").json()["items"]) == 1


def test_delete_plan_item_and_missing_returns_4018(app_client):
    item = _plan(app_client, subject="语文", weekdays=[0]).json()
    assert app_client.delete(f"/api/plan-items/{item['id']}").status_code == 200
    r = app_client.delete(f"/api/plan-items/{item['id']}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4018"


def test_other_family_plan_item_hidden(app_client):
    """别人家的计划项不能改也不能删 (按 family 收口)."""
    import psycopg
    from psycopg.rows import dict_row

    from app import config

    own = _plan(app_client, subject="语文", weekdays=[0]).json()
    assert own["id"] > 0

    now = datetime.now()
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        fid = conn.execute(
            "INSERT INTO families (name, created_at, updated_at) VALUES ('别人家', %s, %s) RETURNING id",
            (now, now),
        ).fetchone()["id"]
        cid = conn.execute(
            "INSERT INTO children (family_id, name, grade, created_at, updated_at) "
            "VALUES (%s, '别人家孩子', '3', %s, %s) RETURNING id",
            (fid, now, now),
        ).fetchone()["id"]
        other_id = conn.execute(
            "INSERT INTO plan_items (family_id, child_id, task_kind, subject, project_id, "
            "task_name, est_minutes, weekdays, active, created_at, updated_at) "
            "VALUES (%s, %s, 'subject', '语文', NULL, '别人的计划', 20, '0', TRUE, %s, %s) RETURNING id",
            (fid, cid, now, now),
        ).fetchone()["id"]
        conn.execute("COMMIT")

    r = app_client.patch(f"/api/plan-items/{other_id}", json={"est_minutes": 5})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4018"
    r = app_client.delete(f"/api/plan-items/{other_id}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4018"


# ====== 3. 一周视图 ======

def test_week_plan_has_seven_days_and_week_bounds(app_client):
    w = _week(app_client, _today().isoformat())
    assert len(w["days"]) == 7
    start = date.fromisoformat(w["week_start"])
    assert start.weekday() == 0, "一周从周一开始"
    assert date.fromisoformat(w["week_end"]) == start + timedelta(days=6)
    assert [d["weekday"] for d in w["days"]] == list(range(7))
    assert sum(1 for d in w["days"] if d["is_today"]) == 1
    assert w["daily_load_limit"] == 120


def test_week_plan_load_counts_slot_and_plan(app_client):
    """负荷 = 课表分钟 + 未生成计划项预估."""
    today = _today()
    wd = today.weekday()
    pid = _project(app_client, name="钢琴", project_type="piano")
    _slot(app_client, pid, wd, "16:30", "18:00")          # 90 分
    _plan(app_client, subject="语文", weekdays=[wd], est_minutes=60)

    w = _week(app_client, today.isoformat())
    day = next(d for d in w["days"] if d["is_today"])
    assert day["slot_minutes"] == 90
    assert day["plan_pending_minutes"] == 60
    assert day["load_minutes"] == 150
    assert day["overloaded"] is True
    assert w["totals"]["overloaded_days"] == 1
    assert w["totals"]["slot_minutes"] == 90


def test_week_plan_under_limit_not_overloaded(app_client):
    today = _today()
    pid = _project(app_client)
    _slot(app_client, pid, today.weekday(), "16:30", "17:00")   # 30 分
    w = _week(app_client, today.isoformat())
    day = next(d for d in w["days"] if d["is_today"])
    assert day["load_minutes"] == 30
    assert day["overloaded"] is False


def test_week_plan_counts_existing_assignments(app_client):
    """待办预估进负荷, 已完成的不再占今天的剩余时间."""
    today = _today()
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [
            {"subject": "语文", "task_name": "抄写生字", "due_date": today.isoformat(),
             "est_minutes": 20, "source_span": "(测试)", "confidence": "high"},
            {"subject": "数学", "task_name": "口算", "due_date": today.isoformat(),
             "est_minutes": 10, "source_span": "(测试)", "confidence": "high"},
        ],
    })
    assert r.status_code == 200, r.text
    done_id = r.json()["created_ids"][0]
    app_client.patch(f"/api/assignments/{done_id}/status", json={"status": "done"})

    w = _week(app_client, today.isoformat())
    day = next(d for d in w["days"] if d["is_today"])
    assert day["assignments"]["total"] == 2
    assert day["assignments"]["done"] == 1
    assert day["assignments"]["remaining_minutes"] == 10
    assert day["load_minutes"] == 10


def test_week_plan_marks_existing_conflict(app_client):
    """绕过写入校验直接造两条重叠时段, 周视图必须把它们标出来."""
    import psycopg
    from psycopg.rows import dict_row

    from app import config

    today = _today()
    pid = _project(app_client)
    _slot(app_client, pid, today.weekday(), "16:30", "18:00")

    now = datetime.now()
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT family_id, child_id FROM projects WHERE id = %s", (pid,)
        ).fetchone()
        conn.execute(
            "INSERT INTO schedule_slots (family_id, child_id, project_id, weekday, "
            "start_time, end_time, note, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, '17:30', '19:00', NULL, %s, %s)",
            (row["family_id"], row["child_id"], pid, today.weekday(), now, now),
        )
        conn.execute("COMMIT")

    w = _week(app_client, today.isoformat())
    day = next(d for d in w["days"] if d["is_today"])
    assert len(day["conflicts"]) == 1
    assert day["conflicts"][0]["message"].count("重叠") == 1
    assert w["totals"]["conflict_days"] == 1


def test_week_plan_past_week_has_no_today(app_client):
    w = _week(app_client, (_today() - timedelta(days=7)).isoformat())
    assert sum(1 for d in w["days"] if d["is_today"]) == 0
    assert all(d["is_past"] for d in w["days"])


# ====== 4. 一键生成 ======

def test_generate_creates_only_today_and_future(app_client):
    """给过去补任务是篡改历史: 只生成今天及以后."""
    today = _today()
    _plan(app_client, subject="语文", weekdays=list(range(7)), est_minutes=15)

    r = app_client.post(f"/api/week-plan/generate?date={today.isoformat()}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_count"] == 7 - today.weekday()
    assert body["skipped_count"] == 0

    w = _week(app_client, today.isoformat())
    for d in w["days"]:
        if d["is_past"]:
            assert d["assignments"]["total"] == 0, "过去的日子不该被补任务"
    today_day = next(d for d in w["days"] if d["is_today"])
    assert today_day["assignments"]["total"] == 1
    assert today_day["plans"][0]["generated"] is True
    # 已生成后不再把计划项预估叠进负荷
    assert today_day["plan_pending_minutes"] == 0


def test_generate_is_idempotent(app_client):
    today = _today()
    _plan(app_client, subject="语文", weekdays=[today.weekday()], est_minutes=15)
    first = app_client.post(f"/api/week-plan/generate?date={today.isoformat()}").json()
    assert first["created_count"] == 1

    second = app_client.post(f"/api/week-plan/generate?date={today.isoformat()}").json()
    assert second["created_count"] == 0
    assert second["skipped_count"] == 1
    assert second["skipped"][0]["reason"] == "已存在相同作业"

    board = app_client.get(f"/api/board?date={today.isoformat()}").json()
    assert board["progress"]["total"] == 1


def test_generate_without_plan_items_returns_zero(app_client):
    today = _today()
    body = app_client.post(f"/api/week-plan/generate?date={today.isoformat()}").json()
    assert body["created_count"] == 0
    assert body["skipped_count"] == 0
    assert body["created_ids"] == []


def test_generate_marks_week_plan_generated(app_client):
    """生成后计划项标 ✓, 再点一次不会重复建."""
    today = _today()
    _plan(app_client, subject="语文", weekdays=[today.weekday()], est_minutes=15)
    app_client.post(f"/api/week-plan/generate?date={today.isoformat()}")

    w = _week(app_client, today.isoformat())
    day = next(d for d in w["days"] if d["is_today"])
    assert day["plans"][0]["generated"] is True
    assert day["load_minutes"] == 15
    assert day["assignments"]["remaining_minutes"] == 15


def test_generate_with_other_week_does_not_touch_this_week(app_client):
    """生成下周不能顺带把本周的也生成出来."""
    today = _today()
    next_week = today + timedelta(days=7)
    _plan(app_client, subject="语文", weekdays=[next_week.weekday()], est_minutes=15)

    app_client.post(f"/api/week-plan/generate?date={next_week.isoformat()}")
    w = _week(app_client, today.isoformat())
    assert sum(d["assignments"]["total"] for d in w["days"]) == 0


def test_generate_uses_week_plan_source(app_client):
    today = _today()
    _plan(app_client, subject="语文", weekdays=[today.weekday()], est_minutes=15)
    app_client.post(f"/api/week-plan/generate?date={today.isoformat()}")
    board = app_client.get(f"/api/board?date={today.isoformat()}").json()
    item = board["by_subject"]["语文"][0]
    assert item["source_span"] == "周计划"
    assert item["confidence"] == "high"


# ====== 5. 导出 ======

def test_export_includes_slots_and_plan_items(app_client):
    import json

    today = _today()
    pid = _project(app_client)
    _slot(app_client, pid, today.weekday(), "16:30", "18:00", "琴房")
    _plan(app_client, subject="语文", weekdays=[today.weekday()], est_minutes=20)

    r = app_client.get("/api/export")
    assert r.status_code == 200, r.text
    payload = json.loads(r.content)
    assert payload["counts"]["schedule_slots"] == 1
    assert payload["counts"]["plan_items"] == 1
    # 导出是原表快照: 只带 project_id, 项目名靠同包的 projects 关联
    assert payload["schedule_slots"][0]["project_id"] == pid
    assert str(payload["schedule_slots"][0]["start_time"])[:5] == "16:30"
    assert payload["plan_items"][0]["weekdays"] == str(today.weekday())


# ====== 6. 前端契约 ======

def test_frontend_has_week_page():
    js = APP_JS.read_text(encoding="utf-8")
    assert "'week'" in js, "TABS 应含 week"
    for fn in ("loadWeekPlan", "renderWeekPlan", "weekDayCard", "generateWeekTasks",
               "openScheduleManager", "openScheduleSlotModal",
               "openPlanItemManager", "openPlanItemModal"):
        assert f"function {fn}" in js, f"缺 {fn}"
    assert "week-plan?date=" in js
    assert "schedule-slots" in js
    assert "plan-items" in js
    # 冲突提示必须留在弹窗里可改, 不能退化成浏览器原生弹窗
    for bad in ("window.confirm(", "window.alert(", "window.prompt("):
        assert bad not in js


def test_index_html_has_week_tab(app_client):
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'data-tab="week"' in html
    assert 'id="view-week"' in html
    assert 'id="week-content"' in html
    r = app_client.get("/week")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_styles_define_week_plan():
    css = STYLES.read_text(encoding="utf-8")
    for sel in (".week-bar", ".week-stats", ".week-days", ".week-day",
                ".week-chips", ".week-slot", ".week-plan", ".week-conflict",
                ".manage-list", ".manage-row", ".weekday-check"):
        assert sel in css, f"缺样式 {sel}"
    # 触屏可用性: 星期勾选与列表操作要够大
    assert "min-height: 44px" in css


@pytest.mark.parametrize("code", ["4016", "4017", "4018"])
def test_new_error_codes_documented(code):
    from app import errors

    assert code in {c.code for c in (
        errors.ScheduleConflictError,
        errors.ScheduleSlotNotFoundError,
        errors.PlanItemNotFoundError,
    )}
