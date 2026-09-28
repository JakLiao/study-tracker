"""/api/parse 路由测试 (mock AI)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tests.fixtures.real_notifications import NOTIFICATION_921, NOTIFICATION_922, EXPECTED_921_922_COUNTS


# ====== Mock AI 真实样本期望输出 ======
MOCK_921_AI_OUTPUT = [
    {
        "subject": "英语", "task_name": "一起作业 (U1)",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 15, "task_type": "other",
        "source_span": "一起作业", "confidence": "high",
    },
    {
        "subject": "英语", "task_name": "描写本 P11-12",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 20, "task_type": "抄写",
        "source_span": "描写本P11-12", "confidence": "high",
    },
    {
        "subject": "英语", "task_name": "背诵 P18-19",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 15, "task_type": "背诵",
        "source_span": "背课文P18-19", "confidence": "high",
    },
    {
        "subject": "语文", "task_name": "完成知能 P14",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 25, "task_type": "other",
        "source_span": "完成知能P14", "confidence": "high",
    },
    {
        "subject": "语文", "task_name": "订正作文《猜猜他是谁》誊抄到 2 号",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 30, "task_type": "订正",
        "source_span": "订正1号作文本《猜猜他是谁》", "confidence": "high",
    },
    {
        "subject": "语文", "task_name": "阅读课外书",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 20, "task_type": "阅读",
        "source_span": "阅读课外书", "confidence": "medium",
    },
    {
        "subject": "数学", "task_name": "订正知能 P10-11",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 15, "task_type": "订正",
        "source_span": "订正知能P10-11", "confidence": "high",
    },
    {
        "subject": "数学", "task_name": "预习 P16-17",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 15, "task_type": "预习",
        "source_span": "预习书本P16-17", "confidence": "high",
    },
    {
        "subject": "数学", "task_name": "1 千米体验记录单 (通勤观察)",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 10, "task_type": "observation",
        "source_span": "在笔记本上完成1千米体验记录单", "confidence": "low",
    },
    {
        "subject": "数学", "task_name": "计算练习 (学到哪写到哪)",
        "due_date": "2026-09-21", "deadline_time": None,
        "is_optional": False, "est_minutes": 10, "task_type": "计算",
        "source_span": "练计算【学到哪一课写到哪一课】", "confidence": "medium",
    },
    {
        "subject": "英语", "task_name": "背诵 U2 指南重点短语和句子",
        "due_date": "2026-09-22", "deadline_time": "2026-09-25T08:20:00",
        "is_optional": False, "est_minutes": 15, "task_type": "背诵",
        "source_span": "背诵U2指南重点短语和句子，周四早读8:20听写", "confidence": "high",
    },
    {
        "subject": "英语", "task_name": "一起作业",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 15, "task_type": "other",
        "source_span": "一起作业", "confidence": "high",
    },
    {
        "subject": "英语", "task_name": "知能 P10、11",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 20, "task_type": "other",
        "source_span": "知能P10、11", "confidence": "high",
    },
    {
        "subject": "语文", "task_name": "完成知能 P15-16 (课上已部分完成)",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 18, "task_type": "other",
        "source_span": "完成知能P15-16（部分课上已完成）", "confidence": "medium",
    },
    {
        "subject": "语文", "task_name": "预习《犟龟》第 9 课",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 20, "task_type": "预习",
        "source_span": "预习9《犟龟》", "confidence": "high",
    },
    {
        "subject": "语文", "task_name": "阅读课外书",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 20, "task_type": "阅读",
        "source_span": "阅读课外书", "confidence": "medium",
    },
    {
        "subject": "数学", "task_name": "第一单元综合复习",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 30, "task_type": "other",
        "source_span": "第一单元综合复习（已发）", "confidence": "medium",
    },
    {
        "subject": "数学", "task_name": "计算练习",
        "due_date": "2026-09-22", "deadline_time": None,
        "is_optional": False, "est_minutes": 10, "task_type": "计算",
        "source_span": "计算练习（根据学习进度去完成）", "confidence": "medium",
    },
    {
        "subject": "数学", "task_name": "手指记忆法练习 (选做)",
        "due_date": None, "deadline_time": None,
        "is_optional": True, "est_minutes": None, "task_type": "other",
        "source_span": "有部分同学还未理解手指记忆法", "confidence": "medium",
    },
]


@pytest.fixture
def mock_ai_ok(monkeypatch):
    """强制开启 AI Key + 返回 mock 真实样本."""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-fake-key-for-test-only")
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.routers.parse as parse_mod
    importlib.reload(parse_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    from fastapi.testclient import TestClient

    def fake_call(raw_text):
        # 根据原文选择返回 9.21 还是 9.22 的子集
        items = MOCK_921_AI_OUTPUT
        if "9.22" in raw_text and "9.21" not in raw_text:
            items = [it for it in items if it["due_date"] in (None, "2026-09-22") or it["deadline_time"] is not None]
        elif "9.21" in raw_text and "9.22" not in raw_text:
            items = [it for it in items if it["due_date"] in (None, "2026-09-21")]
        return {"content": json.dumps(items, ensure_ascii=False), "model": "test-mock"}

    monkeypatch.setattr(parse_mod, "call_minimax", fake_call)
    return TestClient(main_mod.app)


def test_health_no_key(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["ai_reachable"] is False
    assert data["db_ok"] is True


def test_parse_no_key_returns_manual_friendly(app_client):
    r = app_client.post("/api/parse", json={"raw_text": "test"})
    assert r.status_code == 200
    data = r.json()
    assert data["ai_reachable"] is False
    assert data["items"] == []
    assert data["batch_id"] is None


def test_parse_empty_text_rejected(mock_ai_ok):
    r = mock_ai_ok.post("/api/parse", json={"raw_text": ""})
    # 4001 文本为空 -> 通过 AppError -> 400
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4001"


def test_parse_too_long_rejected(mock_ai_ok):
    r = mock_ai_ok.post("/api/parse", json={"raw_text": "x" * 10001})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4002"


def test_parse_921_real_sample(mock_ai_ok):
    r = mock_ai_ok.post("/api/parse", json={"raw_text": NOTIFICATION_921})
    assert r.status_code == 200
    data = r.json()
    assert data["ai_reachable"] is True
    items = data["items"]
    # 至少 10 条 (9.21 真实样本)
    assert len(items) >= 10, f"9.21 解析条数 {len(items)} < 10"
    # 必须有通勤观察
    obs = [it for it in items if it["task_type"] == "observation"]
    assert len(obs) >= EXPECTED_921_922_COUNTS["min_observation_items"]


def test_parse_922_real_sample_includes_optional_and_deadline(mock_ai_ok):
    r = mock_ai_ok.post("/api/parse", json={"raw_text": NOTIFICATION_922})
    assert r.status_code == 200
    items = r.json()["items"]
    # 9.22 必须有 1 条选做 (手指记忆法)
    opt_items = [it for it in items if it["is_optional"]]
    assert len(opt_items) >= EXPECTED_921_922_COUNTS["min_optional_items"]
    # 必须有 1 条 deadline_time (周四 8:20)
    dl_items = [it for it in items if it["deadline_time"]]
    assert len(dl_items) >= EXPECTED_921_922_COUNTS["min_deadline_items"]


def test_parse_returns_batch_id(mock_ai_ok):
    r = mock_ai_ok.post("/api/parse", json={"raw_text": "1. 测试任务"})
    assert r.status_code == 200
    assert r.json()["batch_id"] is not None


def test_parse_duplicate_detected(mock_ai_ok):
    raw = "1. 唯一任务 A\n2. 唯一任务 B"
    r1 = mock_ai_ok.post("/api/parse", json={"raw_text": raw})
    assert r1.status_code == 200
    bid1 = r1.json()["batch_id"]
    # 第二次同 raw_text -> 同 batch_id
    r2 = mock_ai_ok.post("/api/parse", json={"raw_text": raw})
    assert r2.json()["batch_id"] == bid1