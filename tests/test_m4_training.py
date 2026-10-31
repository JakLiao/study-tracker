"""M4 训练打卡: 时长 / 组数 / 次数 / 曲目段落.

要锁住的四条口径:

1. **打卡即完成.** 打卡记录与看板状态必须同源 —— 打卡后任务就是 done,
   actual_minutes 取打卡时长; 否则会出现"记录说练了 30 分, 看板还说没做"。
2. **一任务一记录 (upsert).** 同一天重复打卡按最后一次覆盖, 不能长出两行;
   撤销完成 (done -> todo) 时记录一起删, 不留"待完成却已打卡"的矛盾状态。
3. **只有培训任务能打卡.** 学科作业走状态接口, 习惯打卡是另一条范围。
4. **记录归到任务归属日.** 次日补记昨晚的练习, 仍算昨晚 —— 周报按
   log_date 聚合, 归错日子会把一次练习算到错误的一天。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "app" / "static" / "app.js"
STYLES = ROOT / "app" / "static" / "styles.css"


def _today() -> date:
    return datetime.now().astimezone().date()


def _yesterday() -> date:
    return _today() - timedelta(days=1)


def _project(app_client, name: str = "钢琴", project_type: str = "piano") -> int:
    r = app_client.post("/api/projects", json={"name": name, "project_type": project_type})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _training(app_client, project_id: int, due: str, task_name: str = "曲目练习",
              est: int = 30) -> int:
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [{
            "subject": None,
            "task_kind": "training",
            "project_id": project_id,
            "task_name": task_name,
            "due_date": due,
            "est_minutes": est,
            "task_type": "练习",
            "source_span": "(测试)",
            "confidence": "high",
        }],
    })
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _subject(app_client, due: str, task_name: str = "抄写生字") -> int:
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [{
            "subject": "语文",
            "task_name": task_name,
            "due_date": due,
            "est_minutes": 15,
            "source_span": "(测试)",
            "confidence": "high",
        }],
    })
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _checkin(app_client, assignment_id: int, **kwargs) -> dict:
    body = {"assignment_id": assignment_id, "duration_minutes": 30}
    body.update(kwargs)
    r = app_client.post("/api/training-logs", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _logs(app_client, d: str) -> list[dict]:
    r = app_client.get(f"/api/training-logs?date={d}")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _board(app_client, d: str) -> dict:
    r = app_client.get(f"/api/board?date={d}")
    assert r.status_code == 200, r.text
    return r.json()


# ====== 1. 打卡即完成 ======

def test_checkin_marks_done_and_fills_actual(app_client):
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    res = _checkin(app_client, aid, duration_minutes=35, sets=3, reps=20, segment="第 2 段")

    assert res["status"] == "done"
    assert res["actual_minutes"] == 35
    log = res["log"]
    assert log["assignment_id"] == aid
    assert log["project_id"] == pid
    assert log["project_name"] == "钢琴"
    assert log["task_name"] == "曲目练习"
    assert log["duration_minutes"] == 35
    assert log["sets"] == 3
    assert log["reps"] == 20
    assert log["segment"] == "第 2 段"

    board = _board(app_client, _today().isoformat())
    assert board["progress"]["done"] == 1
    assert board["progress"]["settled"] == 1
    assert board["progress"]["actual_total"] == 35
    item = board["by_kind"]["training"][0]
    assert item["status"] == "done"
    assert item["actual_minutes"] == 35
    # 打卡记录随看板一起下发: 行内不必再发一次请求
    assert board["training_logs"][str(aid)]["segment"] == "第 2 段"


def test_empty_day_board_has_empty_training_logs(app_client):
    board = _board(app_client, _today().isoformat())
    assert board["training_logs"] == {}


# ====== 2. 一任务一记录 / 撤销 ======

def test_repeat_checkin_overwrites_single_row(app_client):
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    _checkin(app_client, aid, duration_minutes=20, sets=2)
    _checkin(app_client, aid, duration_minutes=40, reps=50, segment="第 3 段", note="状态不错")

    logs = _logs(app_client, _today().isoformat())
    assert len(logs) == 1
    assert logs[0]["duration_minutes"] == 40
    # 第二次没传组数 -> 覆盖为 null, 不能残留在 2 组
    assert logs[0]["sets"] is None
    assert logs[0]["reps"] == 50
    assert logs[0]["segment"] == "第 3 段"

    # 实耗跟着打卡时长一起更新
    board = _board(app_client, _today().isoformat())
    assert board["by_kind"]["training"][0]["actual_minutes"] == 40


def test_undo_done_removes_training_log(app_client):
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    _checkin(app_client, aid, duration_minutes=30)

    r = app_client.patch(f"/api/assignments/{aid}/status", json={"status": "todo"})
    assert r.status_code == 200, r.text

    assert _logs(app_client, _today().isoformat()) == []
    board = _board(app_client, _today().isoformat())
    assert board["training_logs"] == {}
    assert board["by_kind"]["training"][0]["status"] == "todo"
    assert board["by_kind"]["training"][0]["actual_minutes"] is None


# ====== 3. 范围与校验 ======

def test_subject_assignment_cannot_checkin(app_client):
    aid = _subject(app_client, _today().isoformat())
    r = app_client.post("/api/training-logs", json={
        "assignment_id": aid, "duration_minutes": 30,
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4015"


def test_habit_assignment_cannot_checkin(app_client):
    pid = _project(app_client, name="每日阅读", project_type="reading")
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [{
            "subject": None, "task_kind": "habit", "project_id": pid,
            "task_name": "课外阅读", "due_date": _today().isoformat(),
            "est_minutes": 20, "source_span": "(测试)", "confidence": "high",
        }],
    })
    aid = r.json()["created_ids"][0]
    resp = app_client.post("/api/training-logs", json={
        "assignment_id": aid, "duration_minutes": 20,
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "4015"


def test_unknown_assignment_returns_4008(app_client):
    r = app_client.post("/api/training-logs", json={
        "assignment_id": 999999, "duration_minutes": 30,
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4008"


def test_other_family_assignment_rejected(app_client):
    """打卡同样按家庭收口: 别人家的任务 ID 不能被本家庭打卡."""
    import psycopg
    from psycopg.rows import dict_row

    from app import config

    # 先建出"本家庭" (default_family_id 取最早那条): 否则下面插入的
    # "别人家"会变成第一个家庭, 这条测试就失去意义了。
    own_aid = _subject(app_client, _today().isoformat(), "本家庭的作业")
    assert own_aid > 0

    now = datetime.now()
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        family_id = conn.execute(
            "INSERT INTO families (name, created_at, updated_at) VALUES ('别人家', %s, %s) RETURNING id",
            (now, now),
        ).fetchone()["id"]
        child_id = conn.execute(
            "INSERT INTO children (family_id, name, grade, created_at, updated_at) "
            "VALUES (%s, '别人家孩子', '3', %s, %s) RETURNING id",
            (family_id, now, now),
        ).fetchone()["id"]
        other_pid = conn.execute(
            "INSERT INTO projects (family_id, child_id, name, project_type, status, created_at, updated_at) "
            "VALUES (%s, %s, '别人的钢琴', 'piano', 'active', %s, %s) RETURNING id",
            (family_id, child_id, now, now),
        ).fetchone()["id"]
        aid = conn.execute(
            """
            INSERT INTO assignments
                (subject, task_name, due_date, est_minutes, status, source_span,
                 confidence, created_at, updated_at, family_id, child_id, project_id, task_kind)
            VALUES (NULL, '别人的曲目练习', %s, 30, 'todo', '', 'high', %s, %s, %s, %s, %s, 'training')
            RETURNING id
            """,
            (_today().isoformat(), now, now, family_id, child_id, other_pid),
        ).fetchone()["id"]
        conn.execute("COMMIT")

    r = app_client.post("/api/training-logs", json={
        "assignment_id": aid, "duration_minutes": 30,
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4008"


def test_duration_out_of_range_rejected(app_client):
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    r = app_client.post("/api/training-logs", json={
        "assignment_id": aid, "duration_minutes": 241,
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_sets_out_of_range_rejected(app_client):
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    r = app_client.post("/api/training-logs", json={
        "assignment_id": aid, "duration_minutes": 30, "sets": 101,
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_blank_segment_stored_as_null(app_client):
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    res = _checkin(app_client, aid, segment="   ", note="")
    assert res["log"]["segment"] is None
    assert res["log"]["note"] is None


def test_zero_duration_allowed(app_client):
    """0 分钟是"今天上课, 回家没练但记一笔", 与"没打卡"不同."""
    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    res = _checkin(app_client, aid, duration_minutes=0)
    assert res["log"]["duration_minutes"] == 0
    assert res["status"] == "done"
    assert res["actual_minutes"] == 0


# ====== 4. 归属日 ======

def test_log_attributed_to_assignment_due_date(app_client):
    """今天补记昨天的练习, 记录算昨天 (周报按 log_date 聚合)."""
    pid = _project(app_client)
    aid = _training(app_client, pid, _yesterday().isoformat())
    _checkin(app_client, aid, duration_minutes=25)

    y = _logs(app_client, _yesterday().isoformat())
    assert len(y) == 1 and y[0]["log_date"] == _yesterday().isoformat()
    assert _logs(app_client, _today().isoformat()) == []

    board = _board(app_client, _yesterday().isoformat())
    assert board["by_kind"]["training"][0]["status"] == "done"
    assert str(aid) in board["training_logs"]


# ====== 5. 导出 ======

def test_export_includes_training_logs(app_client):
    import json

    pid = _project(app_client)
    aid = _training(app_client, pid, _today().isoformat())
    _checkin(app_client, aid, duration_minutes=30, segment="第 1 段")

    r = app_client.get("/api/export")
    assert r.status_code == 200, r.text
    payload = json.loads(r.content)
    assert payload["counts"]["training_logs"] == 1
    assert payload["training_logs"][0]["assignment_id"] == aid
    assert payload["training_logs"][0]["segment"] == "第 1 段"


# ====== 6. 前端契约 ======

def test_frontend_has_training_checkin_ui():
    js = APP_JS.read_text(encoding="utf-8")
    assert "function openTrainingLogModal" in js
    assert "function trainingLogSummary" in js
    assert "'/training-logs'" in js
    assert "item.task_kind === 'training'" in js
    # 训练任务不再渲染通用「完成」: 打卡是唯一入口
    assert "status-btn training" in js
    # 打卡数据来自看板返回, 不逐行请求
    assert "b.training_logs" in js
    # 仍然不引入浏览器原生弹窗
    for bad in ("window.confirm(", "window.alert(", "window.prompt("):
        assert bad not in js


def test_styles_define_training_checkin():
    css = STYLES.read_text(encoding="utf-8")
    assert ".status-btn.training" in css
    assert ".training-tag" in css
