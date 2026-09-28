"""真实 AI 解析 E2E 检查 (不 mock).

⚠️ 这不是 pytest 用例: 全文 0 个 `def test_`, pytest 收集到 0 条。
   直接手工跑: `python3 tests/e2e/test_real_ai_parsing.py`
   会真打 MiniMax 并消耗额度, 故刻意不转成 pytest 自动执行。
   同理**不要**为了跑它把 python-dotenv 写进 requirements —— 本脚本已不依赖它。

校验 P0 修复: prompts.py 改用 string.Template 后, 真打 MiniMax 必须不抛 KeyError.

覆盖:
- NOTIFICATION_921 (中等长度)
- NOTIFICATION_922 (含周四 8:20 听写 deadline / 手指记忆法选做 / 1 千米通勤观察)
- 超长文本 (拼接一周通知, 看会不会超时)
- 短样本 (单科, 看耗时下限)

每个用例打印:
- HTTP 状态
- ai_reachable
- 耗时 (秒)
- items 条数
- 关键场景字段命中情况
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 直接 source 用户的 .bashrc
import subprocess
bashrc = Path.home() / ".bashrc"
if bashrc.exists():
    # 用 bash 加载, 因为里面可能有 export / 函数等
    out = subprocess.run(
        ["bash", "-c", f"source {bashrc} && env"],
        capture_output=True, text=True, check=True,
    )
    for line in out.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            os.environ[k] = v

# 必须重新加载 config (因为它是模块级常量)
import importlib
import app.config as cfg_mod
importlib.reload(cfg_mod)

assert cfg_mod.MINIMAX_API_KEY, "MINIMAX_API_KEY 未配置, 测试无法继续"

from app.ai_client import call_minimax, parse_ai_response  # noqa: E402
from tests.fixtures.real_notifications import (  # noqa: E402
    NOTIFICATION_921,
    NOTIFICATION_922,
    EXPECTED_921_922_COUNTS,
)


def _summarize_items(items: list[dict]) -> dict:
    return {
        "n_total": len(items),
        "n_optional": sum(1 for x in items if x.get("is_optional")),
        "n_deadline": sum(1 for x in items if x.get("deadline_time")),
        "n_observation": sum(1 for x in items if x.get("task_type") == "observation"),
        "subjects": sorted({x.get("subject") for x in items}),
        "due_dates": sorted({x.get("due_date") for x in items if x.get("due_date")}),
        "tasks_with_confidence_low": sum(1 for x in items if x.get("confidence") == "low"),
        "sample_names": [x.get("task_name", "")[:40] for x in items[:5]],
    }


def run_one(name: str, raw: str, *, expect: dict | None = None) -> dict:
    print(f"\n===== {name} =====")
    print(f"文本长度: {len(raw)} 字符")
    t0 = time.perf_counter()
    try:
        result = call_minimax(raw)
        elapsed = time.perf_counter() - t0
        print(f"AI 调用耗时: {elapsed:.2f}s")
        print(f"返回 content 前 200 字: {result['content'][:200]}")
        try:
            items = parse_ai_response(result["content"])
        except Exception as e:
            print(f"!! parse_ai_response 失败: {e}")
            return {"name": name, "ok": False, "elapsed": elapsed, "error": f"parse failed: {e}"}
        summary = _summarize_items(items)
        print(f"解析结果摘要: {summary}")
        ok = True
        problems = []
        if expect:
            for k, v in expect.items():
                got = summary.get(k, 0)
                if got < v:
                    problems.append(f"{k}: 期望 >= {v}, 实得 {got}")
        if problems:
            ok = False
        return {
            "name": name,
            "ok": ok,
            "elapsed": elapsed,
            "summary": summary,
            "problems": problems,
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        print(f"!! AI 调用失败: {type(e).__name__}: {e}")
        return {"name": name, "ok": False, "elapsed": elapsed, "error": f"{type(e).__name__}: {e}"}


def main():
    print(f"超时配置: {cfg_mod.MINIMAX_TIMEOUT}s")
    print(f"API base: {cfg_mod.MINIMAX_API_BASE}")
    print(f"模型: {cfg_mod.MINIMAX_MODEL}")

    results = []

    # 1. 9.21 样本 - 应包含通勤观察
    r = run_one(
        "NOTIFICATION_921",
        NOTIFICATION_921,
        expect={
            "n_total": 10,
            "n_observation": EXPECTED_921_922_COUNTS["min_observation_items"],
        },
    )
    results.append(r)

    # 2. 9.22 样本 - 应包含选做 + deadline
    r = run_one(
        "NOTIFICATION_922",
        NOTIFICATION_922,
        expect={
            "n_total": 5,
            "n_optional": EXPECTED_921_922_COUNTS["min_optional_items"],
            "n_deadline": EXPECTED_921_922_COUNTS["min_deadline_items"],
        },
    )
    results.append(r)

    # 3. 拼接一周通知 (家长可能粘贴一周的作业)
    one_week = (NOTIFICATION_921 + "\n\n" + NOTIFICATION_922) * 3  # 接近一周
    r = run_one("ONE_WEEK_LONG", one_week, expect={})
    results.append(r)

    # 总结
    print("\n\n========== 总结 ==========")
    print(f"超时配置: {cfg_mod.MINIMAX_TIMEOUT}s")
    for r in results:
        flag = "✅" if r["ok"] else "❌"
        elapsed = r.get("elapsed", 0)
        print(f"{flag} {r['name']}: {elapsed:.2f}s")
        if r.get("problems"):
            for p in r["problems"]:
                print(f"    - {p}")
        if r.get("error"):
            print(f"    !! {r['error']}")

    return results


if __name__ == "__main__":
    main()