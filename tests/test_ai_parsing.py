"""AI Client: JSON 健壮解析 + 字段归一化测试 (不真打 API)."""
from __future__ import annotations

import json

import pytest

from app import config
from app.ai_client import parse_ai_response, _parse_date_loose, _parse_datetime_loose, _coerce_item


# ====== parse_ai_response: 健壮性 ======

def test_parse_pure_json_array():
    content = json.dumps([
        {"subject": "语文", "task_name": "完成知能 P14", "due_date": "2026-09-21",
         "deadline_time": None, "is_optional": False, "est_minutes": 25,
         "task_type": "other", "source_span": "完成知能P14。", "confidence": "high"}
    ], ensure_ascii=False)
    items = parse_ai_response(content)
    assert len(items) == 1
    assert items[0]["subject"] == "语文"
    assert items[0]["task_name"] == "完成知能 P14"


def test_parse_json_with_fence():
    content = "```json\n[{\"subject\":\"数学\",\"task_name\":\"订正\",\"source_span\":\"订正\"}]\n```"
    items = parse_ai_response(content)
    assert len(items) == 1
    assert items[0]["subject"] == "数学"


def test_parse_json_with_preface():
    content = "以下是解析结果:\n\n[{\"subject\":\"英语\",\"task_name\":\"背诵\",\"source_span\":\"背诵\"}]\n\n如有疑问请反馈。"
    items = parse_ai_response(content)
    assert len(items) == 1


def test_parse_single_object_wrapped_as_array():
    content = json.dumps({"subject": "语文", "task_name": "阅读", "source_span": "阅读"}, ensure_ascii=False)
    items = parse_ai_response(content)
    assert len(items) == 1


def test_parse_object_with_items_wrapper():
    content = json.dumps({
        "items": [{"subject": "语文", "task_name": "写作", "source_span": "写作"}]
    }, ensure_ascii=False)
    items = parse_ai_response(content)
    assert len(items) == 1


def test_parse_invalid_raises():
    with pytest.raises(Exception):
        parse_ai_response("这只是一段普通文本, 没有 JSON")


def test_parse_partial_items_dropped():
    """单条 source_span/task_name 都缺, 整条丢弃; 其余保留."""
    content = json.dumps([
        {"subject": "语文", "task_name": "有效条目", "source_span": "原文"},
        {"subject": "数学"},  # task_name 与 source_span 都缺
    ], ensure_ascii=False)
    items = parse_ai_response(content)
    assert len(items) == 1


# ====== _coerce_item: 字段归一化 ======

def test_coerce_subject_fallback_by_keyword():
    raw = {"task_name": "数学练习册 P3", "source_span": "练习册 P3"}
    out = _coerce_item(raw)
    assert out["subject"] == "数学"


def test_coerce_invalid_subject_fallback():
    raw = {"subject": "物理", "task_name": "实验", "source_span": "实验"}
    out = _coerce_item(raw)
    assert out["subject"] == "英语"  # 兜底


def test_coerce_est_minutes_out_of_range():
    raw = {"subject": "语文", "task_name": "写作", "source_span": "写作", "est_minutes": 999}
    out = _coerce_item(raw)
    assert out["est_minutes"] is None


def test_coerce_is_optional_string_truthy():
    raw = {"subject": "数学", "task_name": "练习", "source_span": "练习", "is_optional": "是"}
    out = _coerce_item(raw)
    assert out["is_optional"] is True


def test_coerce_confidence_unknown_to_medium():
    raw = {"subject": "英语", "task_name": "朗读", "source_span": "朗读", "confidence": "可能"}
    out = _coerce_item(raw)
    assert out["confidence"] == "medium"


def test_coerce_missing_source_span_fallback_to_task_name():
    raw = {"subject": "语文", "task_name": "阅读"}
    out = _coerce_item(raw)
    assert out["source_span"] == "阅读"


# ====== 日期宽松解析 ======

def test_parse_date_loose_iso():
    assert _parse_date_loose("2026-09-21") == "2026-09-21"


def test_parse_date_loose_with_slash():
    assert _parse_date_loose("2026/9/21") == "2026-09-21"


def test_parse_date_loose_short_md():
    assert _parse_date_loose("9.21") is not None
    assert _parse_date_loose("9-21") is not None


def test_parse_date_loose_invalid():
    assert _parse_date_loose(None) is None
    assert _parse_date_loose("") is None
    assert _parse_date_loose("xxx") is None
    assert _parse_date_loose("无") is None


# ====== datetime 宽松解析 ======

def test_parse_datetime_loose_iso():
    assert _parse_datetime_loose("2026-09-25T08:20:00") == "2026-09-25T08:20:00"


def test_parse_datetime_loose_no_seconds():
    assert _parse_datetime_loose("2026-09-25T08:20") == "2026-09-25T08:20:00"


def test_parse_datetime_loose_invalid():
    assert _parse_datetime_loose(None) is None
    assert _parse_datetime_loose("周四早读") is None


# ====== 4 类真实场景模拟 (mock AI 返回, 验证归一化能容纳) ======

def test_scenario_a_deadline_not_today():
    """场景 A: 周四早读 8:20 听写 -> deadline_time != due_date."""
    raw = {
        "subject": "英语",
        "task_name": "背诵 U2 指南重点短语和句子",
        "due_date": "2026-09-22",
        "deadline_time": "2026-09-25T08:20:00",
        "is_optional": False,
        "est_minutes": 15,
        "task_type": "背诵",
        "source_span": "背诵U2指南重点短语和句子，周四早读8:20听写",
        "confidence": "high",
    }
    out = _coerce_item(raw)
    assert out["due_date"] == "2026-09-22"
    assert out["deadline_time"] == "2026-09-25T08:20:00"
    assert "周四听写" not in out["task_name"]  # task_name 不应混淆


def test_scenario_b_partial_applicable():
    """场景 B: 部分同学适用 -> is_optional=true."""
    raw = {
        "subject": "数学",
        "task_name": "手指记忆法练习 (选做)",
        "due_date": None,
        "deadline_time": None,
        "is_optional": True,
        "est_minutes": None,
        "task_type": "other",
        "source_span": "有部分同学还未理解手指记忆法，可以回家再练习一下",
        "confidence": "medium",
    }
    out = _coerce_item(raw)
    assert out["is_optional"] is True
    assert out["due_date"] is None
    assert out["confidence"] == "medium"


def test_scenario_c_part_done_in_class():
    """场景 C: 部分课上已完成 -> est_minutes 下调 (AI 应给已下调值)."""
    raw = {
        "subject": "语文",
        "task_name": "完成知能 P15-16 (课上已部分完成)",
        "due_date": "2026-09-22",
        "deadline_time": None,
        "is_optional": False,
        "est_minutes": 18,  # 比同类 25 已下调
        "task_type": "other",
        "source_span": "完成知能P15-16（部分课上已完成），订正前面作业",
        "confidence": "medium",
    }
    out = _coerce_item(raw)
    assert out["est_minutes"] == 18
    assert "部分完成" in out["task_name"]
    assert out["confidence"] == "medium"


def test_scenario_d_commute_observation():
    """场景 D: 通勤观察 -> task_type=observation, confidence=low."""
    raw = {
        "subject": "数学",
        "task_name": "1 千米体验记录单 (通勤观察)",
        "due_date": "2026-09-21",
        "deadline_time": None,
        "is_optional": False,
        "est_minutes": 10,
        "task_type": "observation",
        "source_span": "在笔记本上完成1千米体验记录单，可只画表格，也可自行打印记录单",
        "confidence": "low",
    }
    out = _coerce_item(raw)
    assert out["task_type"] == "observation"
    assert out["confidence"] == "low"