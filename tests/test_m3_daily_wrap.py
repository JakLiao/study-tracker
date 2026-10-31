"""M3 日结: 晚间三问 + 每晚小结.

三条要锁住的口径:

1. **小结与看板同源.** summary 的 done/required/pct/actual_total 必须与
   /api/board 的 progress 完全一致 —— 两个界面各算一套, 家长只会两个都不信。
2. **一天一条三问.** 重复提交按覆盖处理(upsert), 不能长出两条记录,
   也不能丢掉首次 created_at 之外的东西。
3. **低年级两问版.** grade=1/2 时 mode=lite, 前端据此不问"专注";
   这是服务端判定, 换设备也按家庭配置走。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _today() -> date:
    return datetime.now().astimezone().date()


def _add(app_client, due: str, task_name: str = "晚辅任务", subject: str = "语文",
         est_minutes: int = 20, is_optional: bool = False,
         deadline: str | None = None) -> int:
    payload = {
        "batch_id": None,
        "items": [{
            "subject": subject,
            "task_name": task_name,
            "due_date": due,
            "deadline_time": deadline,
            "is_optional": is_optional,
            "est_minutes": est_minutes,
            "task_type": "背诵",
            "source_span": "(测试)",
            "confidence": "high",
        }],
    }
    r = app_client.post("/api/assignments/batch", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _wrap(app_client, d: str) -> dict:
    r = app_client.get(f"/api/daily-wrap?date={d}")
    assert r.status_code == 200, r.text
    return r.json()


def _save(app_client, d: str, **kwargs) -> dict:
    body = {"date": d, "difficulty": "ok", "mood": "ok"}
    body.update(kwargs)
    r = app_client.post("/api/daily-wrap", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ====== 1. 空状态 ======

def test_empty_day_still_returns_wrap(app_client):
    """没有作业也能记录状态: 小结不是"有作业才有"的附属页。"""
    w = _wrap(app_client, _today().isoformat())
    assert w["summary"]["has_data"] is False
    assert w["summary"]["required"] == 0
    assert w["reflection"] is None
    assert w["headline"] == "今天没有作业记录"
    assert 1 <= len(w["suggestions"]) <= 3
    assert "登记" in "".join(w["suggestions"])


# ====== 2. 三问读写与幂等 ======

def test_reflection_roundtrip(app_client):
    d = _today().isoformat()
    saved = _save(app_client, d, difficulty="hard", focus="bad", mood="bad",
                  note="  数学卡在应用题  ")
    assert saved["reflection"]["difficulty"] == "hard"
    assert saved["reflection"]["focus"] == "bad"
    assert saved["reflection"]["mood"] == "bad"
    assert saved["reflection"]["note"] == "数学卡在应用题"
    assert _wrap(app_client, d)["reflection"]["difficulty"] == "hard"


def test_reflection_upsert_keeps_single_row(app_client):
    """记错档位再点一次是覆盖, 不是追加 —— 一天只该有一条。"""
    d = _today().isoformat()
    _save(app_client, d, difficulty="hard", mood="bad", note="第一次")
    second = _save(app_client, d, difficulty="easy", mood="good", note="改过了")
    assert second["reflection"]["difficulty"] == "easy"
    assert second["reflection"]["mood"] == "good"
    assert second["reflection"]["note"] == "改过了"


def test_blank_note_stored_as_null(app_client):
    d = _today().isoformat()
    saved = _save(app_client, d, note="   ")
    assert saved["reflection"]["note"] is None


def test_invalid_enum_rejected(app_client):
    r = app_client.post("/api/daily-wrap", json={
        "date": _today().isoformat(), "difficulty": "very-hard", "mood": "ok",
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_missing_mood_rejected(app_client):
    r = app_client.post("/api/daily-wrap", json={
        "date": _today().isoformat(), "difficulty": "ok",
    })
    assert r.status_code == 400


def test_bad_date_rejected(app_client):
    r = app_client.get("/api/daily-wrap?date=2026-13-40")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4006"


# ====== 3. 低年级两问版 ======

def test_low_grade_uses_lite_mode(app_client):
    app_client.patch("/api/settings/grade", json={"value": "1"})
    assert _wrap(app_client, _today().isoformat())["mode"] == "lite"
    app_client.patch("/api/settings/grade", json={"value": "2"})
    assert _wrap(app_client, _today().isoformat())["mode"] == "lite"
    app_client.patch("/api/settings/grade", json={"value": "3"})
    assert _wrap(app_client, _today().isoformat())["mode"] == "full"


# ====== 4. 小结口径与看板一致 ======

def test_summary_matches_board_progress(app_client):
    d = _today()
    ds = d.isoformat()
    a = _add(app_client, ds, "语文背诵", "语文", 20)
    b = _add(app_client, ds, "数学计算", "数学", 15)
    c = _add(app_client, ds, "英语抄写", "英语", 10)
    _add(app_client, ds, "选做阅读", "语文", 10, is_optional=True)
    app_client.patch(f"/api/assignments/{a}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{a}/completion", json={"actual_minutes": 30})
    app_client.patch(f"/api/assignments/{b}/status", json={"status": "skipped"})

    board = app_client.get(f"/api/board?date={ds}").json()["progress"]
    summary = _wrap(app_client, ds)["summary"]
    for key in ("done", "required", "total", "settled", "skipped", "optional",
                "optional_done", "pct", "est_total", "actual_total"):
        assert summary[key] == board[key], f"{key} 与看板口径不一致"
    assert summary["pending_count"] == 1
    assert summary["pending_names"] == ["英语抄写"]
    # c 只有 est 10 但没做, 不计入 actual_total; a 填了 30 按实际算。
    assert summary["actual_total"] == 30


def test_headline_reports_remaining(app_client):
    d = _today().isoformat()
    _add(app_client, d, "任务一")
    _add(app_client, d, "任务二")
    assert _wrap(app_client, d)["headline"] == "今天还剩 2 项没做完"


def test_headline_celebrates_all_done(app_client):
    d = _today().isoformat()
    aid = _add(app_client, d, "唯一任务")
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    assert _wrap(app_client, d)["headline"] == "今天全部收尾"


def test_overdue_count_covers_today_and_past(app_client):
    """当日到点未做 + 往期逾期都要算进来, 两者不重叠。"""
    d = _today()
    now = datetime.now()
    today_dl = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:00")
    if (now - timedelta(minutes=5)).date() != d:
        pytest.skip("测试时刻恰在午夜前 5 分钟内, 跳过当日逾期边界")
    _add(app_client, d.isoformat(), "今天逾期", deadline=today_dl)
    past = d - timedelta(days=1)
    _add(app_client, past.isoformat(), "昨天逾期", deadline=past.isoformat() + "T08:00:00")
    summary = _wrap(app_client, d.isoformat())["summary"]
    assert summary["overdue_count"] == 2


# ====== 5. 建议规则 ======

def test_suggestions_follow_reflection(app_client):
    d = _today().isoformat()
    # 先造一条已收尾的作业, 让"今天没有作业记录"和"还有任务没收尾"都不出现,
    # 三条归因建议才不会被前两条挤掉(规则最多返回 3 条)。
    aid = _add(app_client, d, "唯一任务", est_minutes=20)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 20})
    w = _save(app_client, d, difficulty="hard", focus="bad", mood="bad")
    text = "".join(w["suggestions"])
    assert "偏难" in text
    assert "专注" in text
    assert "情绪" in text
    assert len(w["suggestions"]) <= 3


def test_suggestion_praise_when_all_done(app_client):
    d = _today().isoformat()
    aid = _add(app_client, d, "唯一任务", est_minutes=20)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 20})
    text = "".join(_wrap(app_client, d)["suggestions"])
    assert "表扬" in text


def test_suggestion_overrun_when_slower_than_estimate(app_client):
    d = _today().isoformat()
    aid = _add(app_client, d, "数学卷子", "数学", 20)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 40})
    text = "".join(_wrap(app_client, d)["suggestions"])
    assert "实际比预估多" in text


def test_suggestion_mentions_pending_names(app_client):
    d = _today().isoformat()
    _add(app_client, d, "英语背诵", "英语")
    text = "".join(_wrap(app_client, d)["suggestions"])
    assert "英语背诵" in text


# ====== 6. 历史日期与导出 ======

def test_history_date_wrap_not_today(app_client):
    past = (_today() - timedelta(days=3)).isoformat()
    w = _save(app_client, past, difficulty="easy", mood="good")
    assert w["is_today"] is False
    assert _wrap(app_client, past)["reflection"]["difficulty"] == "easy"


def test_export_includes_reflections(app_client):
    _save(app_client, _today().isoformat(), difficulty="hard", mood="ok")
    r = app_client.get("/api/export")
    assert r.status_code == 200
    data = r.json()
    assert "daily_reflections" in data
    assert data["counts"]["reflections"] == 1
    assert data["daily_reflections"][0]["difficulty"] == "hard"


# ====== 7. 前端契约 ======

@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def wrap_snippet(app_js) -> str:
    start = app_js.index("// ====== 晚间小结 (M3 日结) ======")
    end = app_js.index("// 渲染任务行", start)
    return app_js[start:end]


def test_frontend_calls_daily_wrap_api(app_js):
    assert "renderDailyWrap" in app_js
    assert "`/daily-wrap?date=" in app_js
    assert "api('POST', '/daily-wrap'" in app_js


def test_frontend_only_on_today(app_js):
    assert "if (b.is_today) renderDailyWrap(root, b.date);" in app_js


def test_frontend_lite_mode_skips_focus(wrap_snippet):
    assert "w.mode === 'lite'" in wrap_snippet
    assert "lite ? '' : dwChoicesHtml('focus'" in wrap_snippet


def test_frontend_wrap_uses_no_native_dialogs(wrap_snippet):
    """全站禁止原生 confirm/alert/prompt, 小结卡片同样不能例外。"""
    assert not re.search(r"\b(window\.)?(confirm|alert|prompt)\s*\(", wrap_snippet)


def test_styles_define_daily_wrap(styles):
    for sel in (r"\.daily-wrap-slot\s*\{", r"\.dw-choice\s*\{",
                r"\.dw-choice\.active\s*\{", r"\.dw-stats\s*\{"):
        assert re.search(sel, styles), sel
