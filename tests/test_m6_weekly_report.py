"""M6: 本周课后周报 + 本周作业清单 CSV.

锁住的口径:
1. 完成率与看板同源: 必做不含选做, 已收尾 = done + skipped。
2. 时间只算 done, 实耗为空按预估计; 总用时计入选做, 观察作业不计。
3. 估时偏差样本不足只给 note, 不抛 4011 —— 周报不能被一张表拖垮。
4. 训练线按项目聚合, 报告只跟孩子自己的上周比。
5. 1–2 年级用 lite 模式 (建议/表扬各少一条)。
6. CSV 用 UTF-8 BOM + CRLF + 标准转义, Excel 打开不乱码。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "app" / "static" / "app.js"
STYLES = ROOT / "app" / "static" / "styles.css"


def _today() -> date:
    return datetime.now().astimezone().date()


def _monday() -> date:
    t = _today()
    return t - timedelta(days=t.weekday())


def _report(app_client, d: date | None = None) -> dict:
    qs = f"?date={d.isoformat()}" if d else ""
    r = app_client.get(f"/api/weekly-report{qs}")
    assert r.status_code == 200, r.text
    return r.json()


def _project(app_client, name: str = "钢琴", project_type: str = "piano") -> int:
    r = app_client.post("/api/projects", json={"name": name, "project_type": project_type})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _assignment(app_client, due: date, *, subject="语文", task_kind="subject",
                project_id=None, task_name="抄写生字", est=15,
                optional=False, task_type=None) -> int:
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [{
            "subject": subject,
            "task_kind": task_kind,
            "project_id": project_id,
            "task_name": task_name,
            "due_date": due.isoformat(),
            "est_minutes": est,
            "is_optional": optional,
            "task_type": task_type,
            "source_span": "(测试)",
            "confidence": "high",
        }],
    })
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _done(app_client, aid: int, actual: int | None = None) -> None:
    r = app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    assert r.status_code == 200, r.text
    if actual is not None:
        r = app_client.patch(f"/api/assignments/{aid}/completion",
                             json={"actual_minutes": actual})
        assert r.status_code == 200, r.text


def _reflect(app_client, d: date, difficulty="hard", focus="bad", mood="bad") -> None:
    r = app_client.post("/api/daily-wrap", json={
        "date": d.isoformat(), "difficulty": difficulty,
        "focus": focus, "mood": mood, "note": None,
    })
    assert r.status_code == 200, r.text


# ====== 1. 结构 / 空周 ======

def test_report_shape_and_week_bounds(app_client):
    r = _report(app_client, _today())
    for key in ("week_start", "week_end", "today", "grade", "mode", "has_data",
                "completion", "time", "deviation", "blockers", "training",
                "load", "trend", "praise", "coach_note", "suggestions"):
        assert key in r, key
    assert r["week_start"] == _monday().isoformat()
    assert r["week_end"] == (_monday() + timedelta(days=6)).isoformat()
    assert r["mode"] in ("lite", "full")
    assert len(r["deviation"]["by_type"]) == 0
    assert r["deviation"]["sample_size"] == 0


def test_report_defaults_to_today_without_date(app_client):
    r = app_client.get("/api/weekly-report")
    assert r.status_code == 200
    assert r.json()["week_start"] == _monday().isoformat()


def test_empty_week_is_gentle(app_client):
    r = _report(app_client)
    assert r["has_data"] is False
    assert r["completion"]["total"] == 0
    assert r["completion"]["rate"] == 0
    assert r["training"]["logs"] == 0
    assert r["praise"] == []
    assert any("还没有作业记录" in s for s in r["suggestions"])


def test_bad_date_rejected(app_client):
    r = app_client.get("/api/weekly-report?date=not-a-date")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4006"


# ====== 2. 完成情况 ======

def test_completion_counts_required_only(app_client):
    monday = _monday()
    a = _assignment(app_client, monday, task_name="第一项")
    _done(app_client, a)
    _assignment(app_client, monday, task_name="第二项")
    opt = _assignment(app_client, monday, task_name="选做阅读", optional=True)
    _done(app_client, opt)

    c = _report(app_client)["completion"]
    assert c["total"] == 3
    assert c["optional"] == 1
    assert c["required"] == 2
    assert c["done"] == 1
    assert c["pending"] == 1
    assert c["rate"] == 50
    assert len(c["unfinished"]) == 1
    u = c["unfinished"][0]
    assert u["task_name"] == "第二项"
    assert u["status"] == "todo"
    assert u["weekday"] == "周一"


def test_skipped_counts_as_settled_not_done(app_client):
    monday = _monday()
    a = _assignment(app_client, monday, task_name="放弃项")
    r = app_client.patch(f"/api/assignments/{a}/status", json={"status": "skipped"})
    assert r.status_code == 200
    c = _report(app_client)["completion"]
    assert c["skipped"] == 1
    assert c["done"] == 0
    assert c["settled"] == 1
    assert c["rate"] == 100


# ====== 3. 时间结构 ======

def test_time_uses_actual_or_est_and_includes_optional(app_client):
    monday = _monday()
    a = _assignment(app_client, monday, est=15, task_name="有实耗")
    _done(app_client, a, actual=30)
    b = _assignment(app_client, monday, subject="数学", est=10,
                    task_name="没填实耗", optional=True)
    _done(app_client, b)

    t = _report(app_client)["time"]
    assert t["total_minutes"] == 40
    assert t["by_subject"] == {"语文": 30, "数学": 10}
    assert t["subject_share"]["语文"] == 75.0
    assert t["active_days"] == 1
    assert t["school_days"] >= 1
    assert t["avg_daily_minutes"] == round(40 / t["school_days"], 1)
    assert t["policy_reference_minutes"] == 60


def test_time_excludes_observation(app_client):
    monday = _monday()
    obs = _assignment(app_client, monday, est=20, task_name="观察月亮",
                      task_type="observation")
    _done(app_client, obs)
    a = _assignment(app_client, monday, est=15, task_name="抄写")
    _done(app_client, a, actual=15)
    t = _report(app_client)["time"]
    assert t["total_minutes"] == 15


# ====== 4. 估时偏差 ======

def test_deviation_small_sample_gives_note_not_error(app_client):
    monday = _monday()
    for i, name in enumerate(("甲", "乙")):
        a = _assignment(app_client, monday, task_name=name, est=15)
        _done(app_client, a, actual=25)
    d = _report(app_client)["deviation"]
    assert d["sample_size"] == 2
    assert d["note"] and "样本不足" in d["note"]
    assert d["by_type"] == []


def test_deviation_computes_underestimate(app_client):
    monday = _monday()
    for name in ("甲", "乙", "丙", "丁", "戊"):
        a = _assignment(app_client, monday, task_name=name, est=15)
        _done(app_client, a, actual=25)
    d = _report(app_client)["deviation"]
    assert d["sample_size"] == 5
    assert d["note"] is None
    assert d["top_underestimate"][0]["diff_minutes"] == 10.0
    assert d["top_underestimate"][0]["diff_pct"] == 66.7


# ====== 5. 卡点 ======

def test_blockers_from_reflections(app_client):
    _reflect(app_client, _monday())
    b = _report(app_client)["blockers"]
    assert b["hard_days"][0]["weekday"] == "周一"
    assert b["bad_focus_days"][0]["weekday"] == "周一"
    assert b["bad_mood_days"][0]["weekday"] == "周一"


def test_slow_tasks_only_beyond_ratio(app_client):
    monday = _monday()
    a = _assignment(app_client, monday, task_name="超时项", est=10)
    _done(app_client, a, actual=30)
    b = _assignment(app_client, monday, task_name="正常项", est=20)
    _done(app_client, b, actual=22)
    slow = _report(app_client)["blockers"]["slow_tasks"]
    assert [x["task_name"] for x in slow] == ["超时项"]
    assert slow[0]["ratio"] == 3.0


# ====== 6. 训练线 ======

def test_training_aggregates_by_project(app_client):
    pid = _project(app_client)
    monday = _monday()
    a = _assignment(app_client, monday, subject=None, task_kind="training",
                    project_id=pid, task_name="曲目练习", est=30, task_type="练习")
    b = _assignment(app_client, monday + timedelta(days=2), subject=None,
                    task_kind="training", project_id=pid,
                    task_name="曲目练习", est=30, task_type="练习")
    r = app_client.post("/api/training-logs", json={
        "assignment_id": a, "duration_minutes": 30, "segment": "第一段",
    })
    assert r.status_code == 200, r.text
    r = app_client.post("/api/training-logs", json={
        "assignment_id": b, "duration_minutes": 40, "segment": "第二段",
    })
    assert r.status_code == 200, r.text

    tr = _report(app_client)["training"]
    assert tr["logs"] == 2
    assert tr["total_minutes"] == 70
    assert tr["by_project"][0]["project_name"] == "钢琴"
    assert tr["by_project"][0]["logs"] == 2
    assert tr["by_project"][0]["last_segment"] == "第二段"
    coach = _report(app_client)["coach_note"]
    assert "70 分钟" in coach and "第二段" in coach


# ====== 7. 负荷 / 趋势 ======

def test_load_comes_from_week_plan(app_client):
    pid = _project(app_client)
    weekday = _today().weekday()
    r = app_client.post("/api/schedule-slots", json={
        "project_id": pid, "weekday": weekday,
        "start_time": "16:00", "end_time": "17:00",
    })
    assert r.status_code == 200, r.text
    r = app_client.post("/api/plan-items", json={
        "task_kind": "subject", "subject": "语文",
        "task_name": "课外阅读", "est_minutes": 90, "weekdays": [weekday],
    })
    assert r.status_code == 200, r.text

    ld = _report(app_client)["load"]
    assert ld["slot_minutes"] == 60
    assert ld["limit_minutes"] == 120
    assert ld["overloaded_days"] >= 1
    assert ld["fullest_day"]["load_minutes"] >= 150


def test_trend_compares_last_week(app_client):
    monday = _monday()
    for name in ("上周甲", "上周乙"):
        a = _assignment(app_client, monday - timedelta(days=7), task_name=name)
        _done(app_client, a, actual=10)
    for name in ("本周甲", "本周乙", "本周丙"):
        a = _assignment(app_client, monday, task_name=name)
        _done(app_client, a, actual=10)

    tn = _report(app_client)["trend"]
    assert tn["prev_done"] == 2
    assert tn["done_delta"] == 1
    assert tn["prev_minutes"] == 20
    assert tn["minutes_delta"] == 10


# ====== 8. 低年级 / 隔离 ======

def test_lite_mode_shortens_suggestions_and_praise(app_client):
    app_client.patch("/api/settings/grade", json={"value": "1"})
    r = _report(app_client)
    assert r["mode"] == "lite"
    assert len(r["suggestions"]) <= 2
    assert len(r["praise"]) <= 2

    app_client.patch("/api/settings/grade", json={"value": "3"})
    assert _report(app_client)["mode"] == "full"


def test_other_family_assignments_not_counted(app_client):
    import psycopg
    from psycopg.rows import dict_row

    from app import config

    # 先建本家庭, 保证 default_family_id 取到的是自己
    _assignment(app_client, _monday(), task_name="我家的")

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
        conn.execute(
            "INSERT INTO assignments (family_id, child_id, subject, task_name, due_date, "
            "est_minutes, status, task_kind, source_span, confidence, created_at, updated_at) "
            "VALUES (%s, %s, '语文', '别人的作业', %s, 15, 'done', 'subject', '(测试)', 'high', %s, %s)",
            (fid, cid, _monday(), now, now),
        )
        conn.commit()

    r = _report(app_client)
    assert r["completion"]["total"] == 1
    assert r["completion"]["done"] == 0


# ====== 9. CSV 导出 ======

def test_csv_has_bom_crlf_and_labels(app_client):
    monday = _monday()
    a = _assignment(app_client, monday, task_name="抄写生字")
    _done(app_client, a, actual=20)
    r = app_client.get(f"/api/export/assignments.csv?date={monday.isoformat()}")
    assert r.status_code == 200
    assert r.content[:3] == b"\xef\xbb\xbf"
    body = r.content.decode("utf-8-sig")
    assert body.split("\r\n")[0] == "日期,星期,类型,科目/项目,任务,预计分钟,实际分钟,状态,选做,截止时间"
    line = body.split("\r\n")[1]
    cells = line.split(",")
    assert cells[0] == monday.isoformat()
    assert cells[1] == "周一"
    assert cells[2] == "学科"
    assert cells[3] == "语文"
    assert cells[4] == "抄写生字"
    assert cells[7] == "完成"
    assert cells[8] == "否"
    assert "text/csv" in r.headers["content-type"]
    assert "study-tracker-week-" in r.headers["content-disposition"]


def test_csv_escapes_comma_quote_newline(app_client):
    monday = _monday()
    _assignment(app_client, monday, task_name='检查, 并"朗读"\n第二行')
    r = app_client.get(f"/api/export/assignments.csv?date={monday.isoformat()}")
    body = r.content.decode("utf-8-sig")
    # 引号翻倍 + 整格加引号: "检查, 并""朗读""\n第二行"
    assert '"检查, 并""朗读""\n第二行"' in body


def test_csv_export_defaults_to_today(app_client):
    r = app_client.get("/api/export/assignments.csv")
    assert r.status_code == 200
    assert "study-tracker-week-" in r.headers["content-disposition"]


# ====== 10. 前端契约 ======

def test_frontend_wires_report_and_csv(app_client):
    js = APP_JS.read_text(encoding="utf-8")
    css = STYLES.read_text(encoding="utf-8")
    assert "/weekly-report" in js
    assert "export/assignments.csv" in js
    assert "renderWeeklyReport" in js
    # 复盘页顶部: 周报必须先于用时统计被 append
    assert js.index("loadWeeklyReport()") < js.index("loadReviewTime(period)")
    assert ".report-card" in css
    # 与 test_settings_ui 同一口径: 去掉注释再看, 注释里提到 :has() 不算违规。
    import re
    assert ":has(" not in re.sub(r"/\*[\s\S]*?\*/", "", css)
