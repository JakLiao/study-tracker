"""M1 统一任务池: 项目 CRUD / 培训与习惯任务 / 去重范围 / 一键添加.

设计口径 (2026-10-30):
- 学科作业 (task_kind=subject) 必须带科目, 不能挂项目;
- 培训/习惯任务 (training/habit) 不能带科目, 用 project_id 归类;
- 去重范围按 kind 分: 学科按科目, 培训/习惯按项目;
- 项目删除是归档, 历史作业仍能查到项目名;
- 一键添加复用批量入库, 连点两次不会产生两条。
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from app import config

DATE = "2026-10-30"


def _mk_project(client, name="钢琴", ptype="piano"):
    r = client.post("/api/projects", json={"name": name, "project_type": ptype})
    assert r.status_code == 200, r.text
    return r.json()


def _batch(client, items):
    return client.post("/api/assignments/batch", json={"batch_id": None, "items": items})


def _subject_item(**overrides):
    item = {
        "subject": "语文",
        "task_name": "背诵课文",
        "due_date": DATE,
        "est_minutes": 15,
        "source_span": "(测试)",
        "confidence": "high",
    }
    item.update(overrides)
    return item


def _training_item(project_id, task_name="曲目练习", **overrides):
    item = {
        "subject": None,
        "task_kind": "training",
        "project_id": project_id,
        "task_name": task_name,
        "due_date": DATE,
        "est_minutes": 20,
        "source_span": "(测试)",
        "confidence": "high",
    }
    item.update(overrides)
    return item


# ====== 项目 CRUD ======

def test_create_project_is_idempotent(app_client):
    first = _mk_project(app_client)
    again = _mk_project(app_client)
    assert first["id"] == again["id"]
    assert app_client.get("/api/projects").json()["items"] == [
        {**first}
    ]


def test_archive_project_hides_it_from_list(app_client):
    p = _mk_project(app_client)
    r = app_client.delete(f"/api/projects/{p['id']}")
    assert r.status_code == 200
    assert r.json() == {"id": p["id"], "status": "archived"}
    assert app_client.get("/api/projects").json()["items"] == []
    archived = app_client.get("/api/projects", params={"include_archived": True}).json()["items"]
    assert [x["status"] for x in archived] == ["archived"]


def test_project_templates_expose_expected_kinds(app_client):
    items = app_client.get("/api/project-templates").json()["items"]
    keys = [i["key"] for i in items]
    assert "piano" in keys and "habit" in keys
    piano = next(i for i in items if i["key"] == "piano")
    assert piano["task_kind"] == "training"
    assert piano["tasks"][0]["est_minutes"] >= 1


# ====== 培训/习惯任务入库 ======

def test_quick_add_training_lands_in_by_kind(app_client):
    p = _mk_project(app_client)
    r = app_client.post("/api/assignments/quick", json={
        "task_name": "曲目练习", "due_date": DATE, "task_kind": "training",
        "project_id": p["id"], "est_minutes": 20, "task_type": "练习",
    })
    assert r.status_code == 200, r.text
    assert r.json()["skipped"] is False

    board = app_client.get("/api/board", params={"date": DATE}).json()
    training = board["by_kind"]["training"]
    assert [x["task_name"] for x in training] == ["曲目练习"]
    assert training[0]["project_name"] == "钢琴"
    assert training[0]["subject"] is None
    # 培训任务也是必做, 计入进度分母
    assert board["progress"]["required"] == 1
    # 学科桶里不能出现它
    assert all(not v for v in board["by_subject"].values())


def test_quick_add_duplicate_is_skipped(app_client):
    p = _mk_project(app_client)
    payload = {
        "task_name": "曲目练习", "due_date": DATE, "task_kind": "training",
        "project_id": p["id"], "est_minutes": 20,
    }
    assert app_client.post("/api/assignments/quick", json=payload).json()["skipped"] is False
    second = app_client.post("/api/assignments/quick", json=payload).json()
    assert second["skipped"] is True
    assert second["created_id"] is None
    items = app_client.get("/api/assignments", params={"date": DATE}).json()["items"]
    assert len(items) == 1


def test_subject_kind_requires_subject(app_client):
    r = app_client.post("/api/assignments/quick", json={
        "task_name": "背单词", "due_date": DATE, "task_kind": "subject",
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_training_kind_rejects_subject(app_client):
    p = _mk_project(app_client)
    r = _batch(app_client, [_training_item(p["id"], subject="语文")])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_unknown_project_rejected(app_client):
    r = _batch(app_client, [_training_item(99999)])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4014"


def test_cross_family_project_rejected(app_client):
    """别人家的项目不能挂到自己的作业上."""
    # 先建一个本家庭项目, 确保默认家庭已经落库 (tenant 取 id 最小的家庭)
    _mk_project(app_client)
    now = datetime.now(timezone.utc)
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        family_id = conn.execute(
            "INSERT INTO families (name, created_at, updated_at) VALUES ('别人家', %s, %s) RETURNING id",
            (now, now),
        ).fetchone()["id"]
        child_id = conn.execute(
            "INSERT INTO children (family_id, name, created_at, updated_at) "
            "VALUES (%s, '别人家孩子', %s, %s) RETURNING id",
            (family_id, now, now),
        ).fetchone()["id"]
        other_pid = conn.execute(
            "INSERT INTO projects (family_id, child_id, name, project_type, status, created_at, updated_at) "
            "VALUES (%s, %s, '钢琴', 'piano', 'active', %s, %s) RETURNING id",
            (family_id, child_id, now, now),
        ).fetchone()["id"]
        conn.execute("COMMIT")

    r = _batch(app_client, [_training_item(other_pid)])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4014"


# ====== 去重范围 ======

def test_same_task_in_two_projects_is_not_deduped(app_client):
    piano = _mk_project(app_client, "钢琴", "piano")
    basket = _mk_project(app_client, "篮球", "basketball")
    r = _batch(app_client, [
        _training_item(piano["id"], "每日练习"),
        _training_item(basket["id"], "每日练习"),
    ])
    assert r.status_code == 200, r.text
    assert len(r.json()["created_ids"]) == 2
    assert r.json()["skipped"] == []


def test_same_task_same_project_is_deduped(app_client):
    p = _mk_project(app_client)
    first = _batch(app_client, [_training_item(p["id"], "每日练习")])
    assert len(first.json()["created_ids"]) == 1
    second = _batch(app_client, [_training_item(p["id"], "每日练习")])
    assert second.json()["created_ids"] == []
    assert second.json()["skipped"][0]["task_kind"] == "training"


def test_subject_and_training_with_same_name_do_not_collide(app_client):
    p = _mk_project(app_client)
    r = _batch(app_client, [
        _subject_item(task_name="口算练习"),
        _training_item(p["id"], "口算练习"),
    ])
    assert r.status_code == 200, r.text
    assert len(r.json()["created_ids"]) == 2


# ====== 列表过滤 ======

def test_list_assignments_filters_by_kind_and_project(app_client):
    p = _mk_project(app_client)
    _batch(app_client, [
        _subject_item(),
        _training_item(p["id"], "曲目练习"),
    ])
    only_training = app_client.get(
        "/api/assignments", params={"date": DATE, "task_kind": "training"}
    ).json()["items"]
    assert [x["task_name"] for x in only_training] == ["曲目练习"]
    by_project = app_client.get(
        "/api/assignments", params={"date": DATE, "project_id": p["id"]}
    ).json()["items"]
    assert len(by_project) == 1


# ====== 编辑归属 ======

def test_edit_kind_and_project_moves_item(app_client):
    p = _mk_project(app_client)
    aid = _batch(app_client, [_subject_item()]).json()["created_ids"][0]
    r = app_client.patch(f"/api/assignments/{aid}", json={
        "task_kind": "training", "project_id": p["id"], "subject": None,
    })
    assert r.status_code == 200, r.text
    board = app_client.get("/api/board", params={"date": DATE}).json()
    assert board["by_kind"]["training"][0]["id"] == aid
    assert all(not v for v in board["by_subject"].values())


def test_edit_kind_without_clearing_subject_rejected(app_client):
    aid = _batch(app_client, [_subject_item()]).json()["created_ids"][0]
    r = app_client.patch(f"/api/assignments/{aid}", json={"task_kind": "habit"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4015"


def test_edit_project_requires_existing_project(app_client):
    aid = _batch(app_client, [_subject_item()]).json()["created_ids"][0]
    r = app_client.patch(f"/api/assignments/{aid}", json={
        "task_kind": "training", "project_id": 99999, "subject": None,
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4014"


# ====== 导出 ======

def test_export_includes_projects(app_client):
    p = _mk_project(app_client)
    _batch(app_client, [_training_item(p["id"])])
    body = app_client.get("/api/export").json()
    assert body["counts"]["projects"] == 1
    assert [x["id"] for x in body["projects"]] == [p["id"]]
    assert body["assignments"][0]["project_id"] == p["id"]
