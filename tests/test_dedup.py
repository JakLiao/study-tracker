"""作业录入去重测试 (2026-09-28).

覆盖三条去重口径:
  1. 同一天内, 同科目 + 归一化后同名 -> 跳过
  2. 跨天同名 -> 不跳过 (小学生背课文要背好几遍, 跨天重复是常态)
  3. 同一天内不同科目同名 -> 不跳过 (语文"复习第一单元" vs 数学"复习第一单元")
"""
from __future__ import annotations

import pytest

from app.services.normalize import dedup_fingerprint, normalize_task_name


def _payload(**overrides):
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
    return {"batch_id": None, "items": [item]}


def _post(client, **overrides):
    return client.post("/api/assignments/batch", json=_payload(**overrides))


# ====== 归一化单测 ======

@pytest.mark.parametrize("a,b", [
    ("完成知能 P14-16", "完成知能Ｐ１４－１６"),      # 全角
    ("完成知能P14 - 16", "完成知能 P14-16"),          # 空格
    ("听写 Unit 1", "听写 unit 1"),                   # 大小写
    ("P15—16", "P15-16"),                             # 破折号
    ("手指记忆法练习 (选做)", "手指记忆法练习（选做）"),  # 选做后缀
    ("  听写   Unit 1  ", "听写Unit1"),                # 冗余空格
])
def test_normalize_same(a, b):
    assert normalize_task_name(a) == normalize_task_name(b)


@pytest.mark.parametrize("a,b", [
    ("背诵古诗二首", "预习古诗三首"),
    ("计算练习", "口算练习"),
    ("读书30分钟", "阅读30分钟"),
])
def test_normalize_different(a, b):
    assert normalize_task_name(a) != normalize_task_name(b)


def test_normalize_empty():
    assert normalize_task_name("") == ""
    assert normalize_task_name("   ") == ""


def test_fingerprint_varies_by_subject_and_date():
    base = dedup_fingerprint("语文", "复习第一单元", "2026-09-21")
    assert base == dedup_fingerprint("语文", "复习第一单元 ", "2026-09-21")
    assert base != dedup_fingerprint("数学", "复习第一单元", "2026-09-21")
    assert base != dedup_fingerprint("语文", "复习第一单元", "2026-09-22")


# ====== 接口层去重 ======

def test_second_identical_entry_is_skipped(app_client):
    """同一天同一科目同名重复录入 -> 第二次跳过."""
    first = _post(app_client)
    assert first.status_code == 200
    assert len(first.json()["created_ids"]) == 1
    assert first.json()["skipped"] == []

    second = _post(app_client)
    assert second.status_code == 200
    body = second.json()
    assert body["created_ids"] == [], "重复项不应创建新记录"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["reason"] == "已存在相同作业"
    assert body["skipped"][0]["subject"] == "语文"


def test_skip_absorbs_formatting_differences(app_client):
    """全角/空格/选做后缀差异应被归一化吸收, 判为重复."""
    _post(app_client, task_name="完成知能 P14-16")
    r = _post(app_client, task_name="完成知能Ｐ１４－１６")
    assert r.json()["created_ids"] == []
    assert len(r.json()["skipped"]) == 1


def test_different_day_same_name_is_kept(app_client):
    """跨天同名不判重复 —— 背课文要背好几遍是真实场景."""
    _post(app_client, task_name="背课文P18-19", due_date="2026-09-21")
    r = _post(app_client, task_name="背课文P18-19", due_date="2026-09-22")
    assert len(r.json()["created_ids"]) == 1, "跨天同名必须保留"
    assert r.json()["skipped"] == []


def test_same_day_different_subject_is_kept(app_client):
    """同一天不同科目同名不判重复 —— 语数都能"复习第一单元"."""
    _post(app_client, subject="语文", task_name="复习第一单元")
    r = _post(app_client, subject="数学", task_name="复习第一单元")
    assert len(r.json()["created_ids"]) == 1, "跨科目同名必须保留"
    assert r.json()["skipped"] == []


def test_different_name_same_day_is_kept(app_client):
    _post(app_client, task_name="背诵古诗二首")
    r = _post(app_client, task_name="预习古诗三首")
    assert len(r.json()["created_ids"]) == 1
    assert r.json()["skipped"] == []


def test_duplicates_within_one_batch(app_client):
    """同一次提交里的自我重复也要拦掉."""
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [
            {**_payload()["items"][0], "task_name": "口算练习"},
            {**_payload()["items"][0], "task_name": "口算 练习"},
            {**_payload()["items"][0], "task_name": "背诵课文"},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["created_ids"]) == 2, "三条里应只建两条"
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["reason"] == "本次提交内重复"


def test_skipped_keeps_created_ids_order(app_client):
    """created_ids 顺序应与 items 中未被跳过的部分保持一致."""
    r = app_client.post("/api/assignments/batch", json={
        "batch_id": None,
        "items": [
            {**_payload()["items"][0], "task_name": "作业甲"},
            {**_payload()["items"][0], "task_name": "作业甲"},   # 重复, 跳过
            {**_payload()["items"][0], "task_name": "作业乙"},
        ],
    })
    ids = r.json()["created_ids"]
    assert len(ids) == 2
    listed = app_client.get("/api/assignments", params={"date": "2026-09-21"}).json()
    rows = listed if isinstance(listed, list) else listed.get("items", [])
    by_id = {r["id"]: r["task_name"] for r in rows}
    assert by_id[ids[0]] == "作业甲"
    assert by_id[ids[1]] == "作业乙"


def test_completed_assignment_also_blocks_duplicate(app_client):
    """已完成/已跳过的作业同样应挡住重复录入 —— 否则家长会误以为漏做了."""
    aid = _post(app_client).json()["created_ids"][0]
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    r = _post(app_client)
    assert r.json()["created_ids"] == []
    assert len(r.json()["skipped"]) == 1


def test_edit_does_not_break_dedup(app_client):
    """改名后应按新名字判重, 旧名不再占位."""
    aid = _post(app_client, task_name="原名").json()["created_ids"][0]
    app_client.patch(f"/api/assignments/{aid}", json={"task_name": "新名"})
    r = _post(app_client, task_name="新名")
    assert r.json()["created_ids"] == [], "改后的名字应参与查重"
    assert len(r.json()["skipped"]) == 1
