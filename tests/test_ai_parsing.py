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
    """AI 给的越界值必须被拒, 且不能原样落库.

    注: 2026-09-29 起解析结果不再回 None —— 越界值先作废, 再按科目基线兜底
    (语文 20), 避免前端那个写死的 15 又冒出来. 断言重点是"999 没被接受".
    """
    raw = {"subject": "语文", "task_name": "写作", "source_span": "写作", "est_minutes": 999}
    out = _coerce_item(raw)
    assert out["est_minutes"] == 20
    assert out["est_minutes"] != 999


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


# ====== 场景 E 兜底: "并" 拆分 (2026-10-13 家长报障) ======
# 报障: "【10.13数学】1.补全课堂笔记，并完成知能P20-21。" 期望拆成 2 条,
# 实测 AI 合并成 1 条 (prompt 软约束对 M3 不够)。这里守的是客户端确定性拆分。

def _one_item(task_name, **kw):
    item = {"subject": "数学", "task_name": task_name, "source_span": kw.pop("span", task_name)}
    item.update(kw)
    return item


def test_reported_case_splits_into_two():
    """报障原句必须拆成 2 条: 补全课堂笔记 / 完成知能P20-21."""
    items = parse_ai_response(json.dumps([_one_item("补全课堂笔记并完成知能P20-21")], ensure_ascii=False))
    assert len(items) == 2
    assert items[0]["task_name"] == "补全课堂笔记"
    assert items[1]["task_name"] == "完成知能P20-21"


def test_reported_case_with_comma_splits():
    """老师原文里的逗号形态 ("补全课堂笔记，并完成…") 同样要拆."""
    items = parse_ai_response(json.dumps([_one_item("补全课堂笔记，并完成知能P20-21")], ensure_ascii=False))
    assert len(items) == 2
    assert items[1]["task_name"] == "完成知能P20-21"


def test_split_keeps_own_source_span_when_found_in_original():
    """拆出的片段能在原文里原样找到时, source_span 用自己那段."""
    raw = _one_item("补全课堂笔记并完成知能P20-21", span="补全课堂笔记，并完成知能P20-21。")
    items = parse_ai_response(json.dumps([raw], ensure_ascii=False))
    assert [it["source_span"] for it in items] == ["补全课堂笔记", "完成知能P20-21"]


def test_split_falls_back_to_full_span_when_not_found():
    """片段对不上原文时保留整段原文, 不能给一段家长核对不上的话.

    这里故意让 task_name 用了 AI 改写过的写法 (原文是"补全笔记"、"知能 20-21"),
    拆出的片段在原文里都找不到 -> 两条都退回整段原文。
    """
    raw = _one_item("补全课堂笔记并完成知能P20-21", span="补全笔记，并完成知能 20-21。")
    items = parse_ai_response(json.dumps([raw], ensure_ascii=False))
    assert len(items) == 2
    assert all(it["source_span"] == "补全笔记，并完成知能 20-21。" for it in items)


def test_split_recomputes_est_minutes_per_branch():
    """合并时 AI 给的时长是两项之和, 拆开后必须按各自分支重算."""
    raw = _one_item("完成知能P20-21并订正2听本", est_minutes=40, task_type="订正")
    items = parse_ai_response(json.dumps([raw], ensure_ascii=False))
    assert [it["task_name"] for it in items] == ["完成知能P20-21", "订正2听本"]
    # 40 是两项之和, 沿用到任何一条都不准
    assert all(it["est_minutes"] != 40 for it in items)
    # 订正那条按家长配的订正基线走
    assert items[1]["task_type"] == "订正"


def test_same_deliverable_steps_not_split():
    """反向锚点: 同一篇作文的必经步骤不能拆 (prompt 场景 E 反例)."""
    items = parse_ai_response(json.dumps([
        _one_item("订正1号作文本，仔细修改后誊抄在2号作文本上")
    ], ensure_ascii=False))
    assert len(items) == 1


def test_enumeration_comma_not_split():
    """「完成知能P10、11」是同一条里的枚举, 「、」不能当拆分点."""
    items = parse_ai_response(json.dumps([_one_item("完成知能P10、11")], ensure_ascii=False))
    assert len(items) == 1


def test_task_without_conjunction_untouched():
    """没有连接词的原样返回, 拆分只对真正合并的情况生效."""
    items = parse_ai_response(json.dumps([_one_item("阅读课外书")], ensure_ascii=False))
    assert len(items) == 1
    assert items[0]["task_name"] == "阅读课外书"


def test_split_preserves_subject_and_date():
    """拆分只改任务名, 科目/日期等归属字段原样继承."""
    raw = _one_item("补全课堂笔记并完成知能P20-21", subject="数学", due_date="2026-10-13")
    items = parse_ai_response(json.dumps([raw], ensure_ascii=False))
    assert all(it["subject"] == "数学" for it in items)
    assert all(it["due_date"] == "2026-10-13" for it in items)


def test_date_prefix_not_split():
    """左半句只是日期时不拆: 那是同一条作业的时间标注, 不是另一项作业."""
    items = parse_ai_response(json.dumps([_one_item("10月13日，完成订正错题")], ensure_ascii=False))
    assert len(items) == 1
    assert items[0]["task_name"] == "10月13日，完成订正错题"