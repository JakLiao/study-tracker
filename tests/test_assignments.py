"""/api/assignments + 看板测试."""
from __future__ import annotations

from datetime import date


def _add_one(app_client, **overrides):
    payload = {
        "batch_id": None,
        "items": [{
            "subject": "语文",
            "task_name": "测试作业",
            "due_date": "2026-09-21",
            "deadline_time": None,
            "is_optional": False,
            "est_minutes": 20,
            "task_type": "other",
            "source_span": "(测试)",
            "confidence": "high",
        }],
    }
    payload["items"][0].update(overrides)
    r = app_client.post("/api/assignments/batch", json=payload)
    assert r.status_code == 200
    return r.json()["created_ids"][0]


def test_batch_create_minimal(app_client):
    aid = _add_one(app_client)
    assert aid > 0
    r = app_client.get(f"/api/assignments?date=2026-09-21")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == aid


def test_batch_create_empty_rejected(app_client):
    r = app_client.post("/api/assignments/batch", json={"batch_id": None, "items": []})
    assert r.status_code in (400, 422)


def test_batch_create_missing_due_date_rejected(app_client):
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [{
            "subject": "语文", "task_name": "x", "est_minutes": 10,
            "source_span": "x", "confidence": "high",
        }],  # due_date 缺失
    })
    assert r.status_code in (400, 422)


def test_status_patch_cycle(app_client):
    aid = _add_one(app_client)
    for s in ("doing", "done", "skipped", "todo"):
        r = app_client.patch(f"/api/assignments/{aid}/status", json={"status": s})
        assert r.status_code == 200
        assert r.json()["status"] == s


def test_status_patch_invalid_rejected(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}/status", json={"status": "flying"})
    # Literal["todo","doing","done","skipped"] 在 pydantic 层就拦住, 走 RequestValidationError -> 4003
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_status_patch_not_found(app_client):
    r = app_client.patch("/api/assignments/999999/status", json={"status": "done"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4008"


def test_completion_patch(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 18})
    assert r.status_code == 200
    assert r.json()["actual_minutes"] == 18


def test_completion_patch_out_of_range(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 999})
    # pydantic Field(ge=0, le=240) 在 schema 层就拦, 走 4003
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_optional_patch(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}/optional", json={"is_optional": True})
    assert r.status_code == 200
    assert r.json()["is_optional"] is True


def test_edit_assignment(app_client):
    aid = _add_one(app_client)
    r = app_client.patch(f"/api/assignments/{aid}", json={
        "task_name": "改名后的作业",
        "est_minutes": 30,
    })
    assert r.status_code == 200
    # 验证
    r2 = app_client.get(f"/api/assignments?date=2026-09-21")
    items = r2.json()["items"]
    assert items[0]["task_name"] == "改名后的作业"
    assert items[0]["est_minutes"] == 30


def test_delete_todo_succeeds(app_client):
    aid = _add_one(app_client)
    r = app_client.delete(f"/api/assignments/{aid}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True
    r2 = app_client.get(f"/api/assignments?date=2026-09-21")
    assert r2.json()["items"] == []


def test_delete_done_forbidden(app_client):
    aid = _add_one(app_client)
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    r = app_client.delete(f"/api/assignments/{aid}")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4010"


def test_list_by_date_format(app_client):
    r = app_client.get("/api/assignments?date=xxx")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4006"


def test_board_today_empty(app_client):
    # 注入: 把"今天"强行设为 2026-09-21 (无作业)
    from unittest.mock import patch
    from datetime import datetime
    fake_today = datetime(2026, 9, 21, 10, 0, 0)
    with patch("app.services.assignment_service.datetime") as mock_dt, \
         patch("app.routers.board.datetime") as mock_dt_b:
        mock_dt.now.return_value = fake_today
        mock_dt_b.now.return_value = fake_today
        # assignment_service 用的是 datetime.now() 直接, import 进来 patch 即可
        r = app_client.get("/api/board/today")
    assert r.status_code == 200
    data = r.json()
    assert data["has_data"] is False


def test_board_today_with_data(app_client):
    # 注入 today
    from unittest.mock import patch
    from datetime import datetime
    fake_today = datetime(2026, 9, 21, 10, 0, 0)
    with patch("app.services.assignment_service.datetime") as mock_dt, \
         patch("app.routers.board.datetime") as mock_dt_b:
        mock_dt.now.return_value = fake_today
        mock_dt_b.now.return_value = fake_today
        _add_one(app_client)
        r = app_client.get("/api/board/today")
    data = r.json()
    assert data["has_data"] is True
    assert data["progress"]["total"] == 1
    assert "语文" in data["by_subject"]


def test_upcoming_deadlines_in_board(app_client):
    # 加一条 deadline_time 在未来的作业
    aid = _add_one(app_client, deadline_time="2026-09-25T08:20:00")
    from unittest.mock import patch
    from datetime import datetime
    fake_today = datetime(2026, 9, 21, 10, 0, 0)
    with patch("app.services.assignment_service.datetime") as mock_dt, \
         patch("app.routers.board.datetime") as mock_dt_b:
        mock_dt.now.return_value = fake_today
        mock_dt_b.now.return_value = fake_today
        r = app_client.get("/api/board/today")
    data = r.json()
    assert len(data["upcoming_deadlines"]) == 1
    assert data["upcoming_deadlines"][0]["id"] == aid


def test_optional_in_optional_section(app_client):
    _add_one(app_client, is_optional=True)
    from unittest.mock import patch
    from datetime import datetime
    fake_today = datetime(2026, 9, 21, 10, 0, 0)
    with patch("app.services.assignment_service.datetime") as mock_dt, \
         patch("app.routers.board.datetime") as mock_dt_b:
        mock_dt.now.return_value = fake_today
        mock_dt_b.now.return_value = fake_today
        r = app_client.get("/api/board/today")
    data = r.json()
    assert len(data["optional_items"]) == 1
    assert data["by_subject"]["语文"] == []  # 不在主科目分组