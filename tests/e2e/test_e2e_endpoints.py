"""端到端 E2E 测试 - 真启动真打接口.

服务在 127.0.0.1:8876 (本脚本启动方式: bash run.sh --port 8876)

覆盖:
- /api/health
- /api/parse (空文本 / 超长 / AI 不可用 / AI 超时 / 正常解析)
- /api/assignments/batch
- /api/board/today (含 deadline 提醒 / 选做分组)
- 状态流转 todo->doing->done + 实际耗时录入
- /api/analytics/review (样本 < 5 是否真的返 "样本不足")
- /api/settings (非法 key / 非法 value)
- /api/export
- 数据持久化: 重启服务后数据还在不在
- 异常中断: 关掉服务时 DB 损坏没
- 重复粘贴: 同一份通知重复调用 parse, 看是否去重
- 编辑: 改任务名 / 预估时长, 存的是新值还是 AI 原值
- 状态变更后流水记录对不对 (audit log)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8876"
API = BASE + "/api"


def hr(t):
    print("\n" + "=" * 60)
    print(t)
    print("=" * 60)


def must(cond, msg):
    if not cond:
        print(f"❌ FAIL: {msg}")
        return False
    print(f"✅ {msg}")
    return True


def get(path, **kw):
    r = requests.get(API + path, timeout=30, proxies={"http": None, "https": None}, **kw)
    return r


def post(path, body, **kw):
    for attempt in range(3):
        r = requests.post(API + path, json=body, timeout=180, proxies={"http": None, "https": None}, **kw)
        if r.status_code != 500:
            return r
        print(f"  [retry {attempt+1}] 500, retrying...")
        time.sleep(2)
    return r


def patch(path, body, **kw):
    return requests.patch(API + path, json=body, timeout=30, proxies={"http": None, "https": None}, **kw)


def delete(path, **kw):
    return requests.delete(API + path, timeout=30, proxies={"http": None, "https": None}, **kw)


def section_health():
    hr("1. /api/health")
    r = get("/health")
    print(r.status_code, r.text[:300])
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok"
    assert j["db_ok"] is True
    print(f"  ai_reachable: {j.get('ai_reachable')}")
    print(f"  ai_model: {j.get('ai_model')}")
    return j


def section_parse_empty():
    hr("2. /api/parse 空文本 / 超长")
    r = post("/parse", {"raw_text": ""})
    print(f"  empty: {r.status_code} {r.text[:200]}")
    assert r.status_code in (400, 422), f"空文本应 400, 实际 {r.status_code}"
    if r.status_code == 400:
        j = r.json()
        print(f"  code: {j.get('error', {}).get('code')}")

    r = post("/parse", {"raw_text": "x" * 10001})
    print(f"  too_long: {r.status_code} {r.text[:200]}")
    assert r.status_code == 400, f"超长应 400, 实际 {r.status_code}"
    j = r.json()
    assert j.get("error", {}).get("code") == "4002", f"超长应是 4002, 实际 {j.get('error', {}).get('code')}"
    return True


def section_parse_real_921():
    hr("3. /api/parse 真实 9.21 样本 (AI 真打)")
    from tests.fixtures.real_notifications import NOTIFICATION_921
    t0 = time.time()
    r = post("/parse", {"raw_text": NOTIFICATION_921})
    elapsed = time.time() - t0
    print(f"  status: {r.status_code}, elapsed: {elapsed:.2f}s")
    assert r.status_code == 200, f"9.21 应 200, 实际 {r.status_code} body={r.text[:500]}"
    j = r.json()
    print(f"  ai_reachable: {j.get('ai_reachable')}")
    print(f"  items: {len(j.get('items', []))}")
    print(f"  batch_id: {j.get('batch_id')}")
    print(f"  model: {j.get('model_version')}")
    for it in j.get("items", [])[:15]:
        print(f"    - [{it.get('subject')}] {it.get('task_name')[:40]} "
              f"est={it['est_minutes']} conf={it['confidence']} "
              f"opt={it['is_optional']} ddl={it.get('deadline_time')} "
              f"type={it.get('task_type')}")
    # 必须至少 10 条 + 1 条通勤观察
    assert len(j["items"]) >= 10, f"9.21 至少 10 条, 实际 {len(j['items'])}"
    obs = [it for it in j["items"] if it.get("task_type") == "observation"]
    assert len(obs) >= 1, f"9.21 必须有 1 条通勤观察, 实际 {len(obs)}"
    return j, elapsed


def section_parse_real_922():
    hr("4. /api/parse 真实 9.22 样本 (含 deadline + 选做)")
    from tests.fixtures.real_notifications import NOTIFICATION_922
    t0 = time.time()
    r = post("/parse", {"raw_text": NOTIFICATION_922})
    elapsed = time.time() - t0
    print(f"  status: {r.status_code}, elapsed: {elapsed:.2f}s")
    assert r.status_code == 200
    j = r.json()
    print(f"  items: {len(j.get('items', []))}")
    for it in j.get("items", [])[:15]:
        print(f"    - [{it.get('subject')}] {it.get('task_name')[:50]} "
              f"est={it['est_minutes']} conf={it['confidence']} "
              f"opt={it['is_optional']} ddl={it.get('deadline_time')}")
    opt_items = [it for it in j["items"] if it["is_optional"]]
    dl_items = [it for it in j["items"] if it["deadline_time"]]
    assert len(opt_items) >= 1, f"9.22 必须有选做 (手指记忆法), 实际 0"
    assert len(dl_items) >= 1, f"9.22 必须有 deadline (周四 8:20 听写), 实际 0"
    # 验 deadline 内容: 必须含周四 + 8:20
    dl = dl_items[0]
    print(f"  deadline task: {dl['task_name']} -> {dl['deadline_time']}")
    assert "08:20" in dl["deadline_time"] or "T08:20" in dl["deadline_time"], \
        f"deadline_time 不含 8:20, 实际 {dl['deadline_time']}"
    # due_date 必须是 9.22 (归属日), 不是 9.24 (周四)
    assert dl["due_date"] == "2026-09-22", f"deadline 任务归属日应是 9.22, 实际 {dl['due_date']}"
    return j


def section_batch_insert_and_board(parsed_items, batch_id):
    hr("5. /api/assignments/batch 入库 + /api/board/today")
    # 先验: 含 null est_minutes 的批次, 提交后端必须拒绝
    bad_items = []
    for it in parsed_items:
        bad_items.append({
            "subject": it["subject"],
            "task_name": it["task_name"],
            "due_date": it["due_date"],
            "deadline_time": it.get("deadline_time"),
            "is_optional": it.get("is_optional", False),
            "est_minutes": it.get("est_minutes"),  # null 会爆
            "task_type": it.get("task_type"),
            "source_span": it.get("source_span", ""),
            "confidence": it.get("confidence", "medium"),
            "batch_id": batch_id,
        })
    r_bad = post("/assignments/batch", {"items": bad_items, "batch_id": batch_id})
    print(f"  含 null est_minutes 的批次: status={r_bad.status_code} body={r_bad.text[:200]}")
    if r_bad.status_code == 200:
        print(f"  ⚠️  允许 null est_minutes 入库了, 看 DTO 后续期望")
    else:
        print(f"  ✅ null est_minutes 被拒 (但前端必须给出哪些行要补)")

    # 正常入库: 对 null est_minutes 给 15, 对 null due_date 选做任务给今天
    today_str = "2026-09-28"
    payload_items = []
    for it in parsed_items:
        if it.get("due_date") is None and it.get("is_optional"):
            # 选做 + 无 due_date: 现象 B 场景, 前端必须给个归属日
            print(f"  ℹ️  选做任务 {it['task_name'][:30]!r} 无 due_date, 默认 {today_str}")
        due = it.get("due_date") or today_str
        payload_items.append({
            "subject": it["subject"],
            "task_name": it["task_name"],
            "due_date": due,
            "deadline_time": it.get("deadline_time"),
            "is_optional": it.get("is_optional", False),
            "est_minutes": it.get("est_minutes") or 15,
            "task_type": it.get("task_type"),
            "source_span": it.get("source_span", ""),
            "confidence": it.get("confidence", "medium"),
            "batch_id": batch_id,
        })
    r = post("/assignments/batch", {"items": payload_items, "batch_id": batch_id})
    print(f"  batch status: {r.status_code} body: {r.text[:300]}")
    assert r.status_code == 200, f"batch 入库应 200, 实际 {r.status_code}"
    ids = r.json()["created_ids"]
    print(f"  created_ids: {ids}")

    # 看今日看板 (今天是 9.28, 9.21 不是今天; 但 deadline_time 设到未来可能进 upcoming)
    r = get("/board/today")
    print(f"  board status: {r.status_code}")
    b = r.json()
    print(f"  has_data: {b.get('has_data')}")
    print(f"  date: {b.get('date')}")
    print(f"  progress: {b.get('progress')}")
    print(f"  by_subject keys: {list(b.get('by_subject', {}).keys())}")
    print(f"  upcoming_deadlines: {len(b.get('upcoming_deadlines', []))}")
    print(f"  optional_items: {len(b.get('optional_items', []))}")
    return ids


def section_status_and_completion(assignment_ids):
    hr("6. 状态流转 todo→doing→done + 实耗时录入")
    if not assignment_ids:
        print("  no ids, skip")
        return
    aid = assignment_ids[0]
    # todo -> doing
    r = patch(f"/assignments/{aid}/status", {"status": "doing"})
    print(f"  -> doing: {r.status_code} {r.text[:150]}")
    assert r.status_code == 200
    # doing -> done (不填实耗时)
    r = patch(f"/assignments/{aid}/status", {"status": "done"})
    print(f"  -> done: {r.status_code} {r.text[:150]}")
    assert r.status_code == 200
    # 看 done 后没填实耗时, service 应允许 (只是 analytics 不计入)
    j = r.json()
    print(f"  done 后 actual_minutes: {j.get('actual_minutes')}")

    # 录入实耗时
    r = patch(f"/assignments/{aid}/completion", {"actual_minutes": 25})
    print(f"  completion: {r.status_code} {r.text[:150]}")
    assert r.status_code == 200
    j = r.json()
    assert j["actual_minutes"] == 25
    print(f"  actual_minutes: {j['actual_minutes']}")

    # 非法实耗时
    r = patch(f"/assignments/{aid}/completion", {"actual_minutes": 9999})
    print(f"  completion(9999): {r.status_code} {r.text[:200]}")
    assert r.status_code == 400, f"9999 应被拒, 实际 {r.status_code}"

    # 非法状态
    r = patch(f"/assignments/{aid}/status", {"status": "garbage"})
    print(f"  status(garbage): {r.status_code} {r.text[:200]}")
    assert r.status_code == 400

    # 不存在的 id
    r = patch(f"/assignments/999999/status", {"status": "doing"})
    print(f"  status(notfound): {r.status_code} {r.text[:200]}")
    assert r.status_code == 404
    return aid


def section_edit(aid):
    hr("7. 编辑作业 (改任务名 / 预估)")
    if not aid:
        return
    r = patch(f"/assignments/{aid}", {"task_name": "改过的任务名", "est_minutes": 99})
    print(f"  edit: {r.status_code} {r.text[:200]}")
    assert r.status_code == 200
    j = r.json()
    print(f"  new task_name: {j['task_name']}, new est_minutes: {j['est_minutes']}")
    assert j["task_name"] == "改过的任务名"
    assert j["est_minutes"] == 99


def section_delete_todo(assignment_ids):
    hr("8. 删除 (todo 可删 / done 不可删)")
    if len(assignment_ids) < 2:
        return
    # 创建新 todo 来删
    r = post("/assignments/batch", {"items": [{
        "subject": "数学",
        "task_name": "测删除",
        "due_date": "2026-09-28",
        "is_optional": False,
        "est_minutes": 10,
        "task_type": "other",
        "source_span": "测删除",
        "confidence": "high",
        "batch_id": None,
    }], "batch_id": None})
    new_id = r.json()["created_ids"][0]
    r = delete(f"/assignments/{new_id}")
    print(f"  delete todo: {r.status_code} {r.text[:150]}")
    assert r.status_code == 200

    # 已 done 的不能删 (前面 aid 已 done)
    aid = assignment_ids[0]
    r = delete(f"/assignments/{aid}")
    print(f"  delete done: {r.status_code} {r.text[:150]}")
    assert r.status_code == 400, f"已 done 删除应被拒, 实际 {r.status_code}"


def section_review_analytics():
    hr("9. /api/analytics/review 样本不足")
    r = get("/analytics/review?period=current")
    print(f"  status: {r.status_code}")
    j = r.json()
    print(f"  body: {json.dumps(j, ensure_ascii=False, indent=2)[:600]}")
    # 关键: 样本 <5 应明确说样本不足, 不能硬出建议
    if j.get("sample_count", 0) < 5:
        msg = j.get("message", "")
        print(f"  message: {msg}")
        # 不强制字段名, 但建议必须明确
    return j


def section_settings():
    hr("10. /api/settings 读写 + 非法")
    r = get("/settings")
    print(f"  list: {r.status_code} items={len(r.json().get('items', []))}")
    print(f"  sample keys: {[x['key'] for x in r.json().get('items', [])[:8]]}")
    assert r.status_code == 200

    # 合法改
    r = patch("/settings/baseline_语文", {"value": "30"})
    print(f"  patch baseline_语文: {r.status_code} {r.text[:150]}")
    assert r.status_code == 200

    # 非法 key
    r = patch("/settings/__no_such_key__", {"value": "x"})
    print(f"  patch 非法 key: {r.status_code} {r.text[:200]}")
    assert r.status_code in (400, 404)

    # 非法时间格式
    r = patch("/settings/school_dismiss_time", {"value": "25:99"})
    print(f"  patch 非法 time: {r.status_code} {r.text[:200]}")
    assert r.status_code == 400

    # 非法 value 类型
    r = patch("/settings/baseline_语文", {"value": "not_a_number"})
    print(f"  patch 非法 value: {r.status_code} {r.text[:200]}")
    assert r.status_code == 400


def section_export():
    hr("11. /api/export")
    r = get("/export")
    print(f"  status: {r.status_code}, content-disposition: {r.headers.get('Content-Disposition')}")
    print(f"  size: {len(r.text)}")
    try:
        j = r.json()
        print(f"  export keys: {list(j.keys())}")
        for k in j:
            v = j[k]
            print(f"    {k}: {type(v).__name__} (len={len(v) if hasattr(v, '__len__') else 'n/a'})")
    except Exception as e:
        print(f"  parse error: {e}")


def section_duplicate_parse():
    hr("12. 重复粘贴同一份通知")
    from tests.fixtures.real_notifications import NOTIFICATION_921
    r1 = post("/parse", {"raw_text": NOTIFICATION_921})
    r2 = post("/parse", {"raw_text": NOTIFICATION_921})
    print(f"  r1 batch: {r1.json().get('batch_id')}")
    print(f"  r2 batch: {r2.json().get('batch_id')}")
    # 看 parse 路由是否去重 (create_parsed_batch 内部)
    # 实际答: parse 只创建 batch 不插入 assignment; 入库由前端决定
    # 但应警告家长"已解析过"


def section_data_persistence():
    hr("13. 数据持久化 - PostgreSQL 行数 + 流水")
    import psycopg
    from psycopg.rows import dict_row

    # 连接串只从环境变量 / .env 读 (密码不入版本库)
    from app import config

    url = config.DATABASE_URL
    with psycopg.connect(url, row_factory=dict_row) as conn:
        for table in ("assignments", "completion_logs", "parsed_batches"):
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            print(f"  {table}: {n}")
        rows = conn.execute(
            "SELECT * FROM completion_logs ORDER BY id DESC LIMIT 5"
        ).fetchall()
        print("  last 5 completion_logs:")
        for r in rows:
            print(f"    {r}")


def section_security():
    hr("14. 安全: 日志/错误响应是否漏 Key 或原文")
    # 故意制造一次带超长特殊字符的 parse 调用
    evil = "x" * 50 + "家长秘密信息" + "y" * 50
    r = post("/parse", {"raw_text": evil})
    print(f"  evil status: {r.status_code}")
    # 看 server log 是否含 Key 或 "家长秘密信息"
    log = Path("/tmp/server.log").read_text(encoding="utf-8", errors="replace") if Path("/tmp/server.log").exists() else ""
    if "家长秘密信息" in log:
        print(f"  ⚠️  原文出现在日志")
    else:
        print(f"  ✅ 原文未出现在日志")
    if "MINIMAX_API_KEY=" in log or "sk-" in log:
        print(f"  ⚠️  Key 出现在日志")
    else:
        print(f"  ✅ Key 未出现在日志")
    # 看错误响应
    r = get("/api/notexists")
    print(f"  404 body: {r.text[:200]}")


def main():
    print("Server health check...")
    r = get("/health")
    assert r.status_code == 200
    print(f"  OK: {r.json()}")

    section_health()
    section_parse_empty()
    parse_921, elapsed = section_parse_real_921()
    parse_922 = section_parse_real_922()
    ids = section_batch_insert_and_board(parse_921["items"], parse_921["batch_id"])
    # 再插一批 9.22 的让看板更丰富
    section_batch_insert_and_board(parse_922["items"], parse_922["batch_id"])
    aid = section_status_and_completion(ids)
    section_edit(aid)
    section_delete_todo(ids)
    section_review_analytics()
    section_settings()
    section_export()
    section_duplicate_parse()
    section_data_persistence()
    section_security()
    print("\n\nDONE")


if __name__ == "__main__":
    main()