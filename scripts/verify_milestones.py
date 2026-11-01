#!/usr/bin/env python3
"""里程碑完成度验证器 (M0-M6).

为什么不用"按行切 hunk"
------------------------
上一版验证器把 diff 的 hunk 按"行"切开, 再把切出来的片段拼回文件去跑检查。
按行切会把多行语句、Python 三引号串、JS 模板字符串从中间劈开, 于是:

  * export_service.py 报缩进错误 —— 其实是切坏的, 不是代码坏了;
  * assignment_service.py "漏掉 tenant 导入" —— 其实是 import 行留在了别的片;
  * JS 侧 M1/M5 被劈坏 —— 模板字符串被拦腰截断。

这版只认作者自己写在代码里的阶段标记:

  * Python:  # ====== 统一任务池 (M1) ======   /  # M4: 撤销完成 = 撤销打卡
  * JS:      // ====== 晚间小结 (M3 日结) ======
  * HTML:    <!-- ====== 周计划 (M5) ====== -->

切点落在"作者自己声明的段落边界"上, 天然落在完整语句之间。切点再分三档:

  安全  —— 切点不在任何多行 token 内部, 两侧都是完整语句;
  片段  —— 切点落在容器字面量内部 (dict/list/调用参数), 该块是容器的一角,
           代码本身没坏, 但单独抽出来不是完整单元;
  危险  —— 切点落在多行字符串/模板串内部, 切开会真的把代码劈坏。

只有"危险"才算问题; "片段"会列出来供人眼确认。

用法::

    python3 scripts/verify_milestones.py            # 全量验证
    python3 scripts/verify_milestones.py --no-tests # 跳过 pytest
    python3 scripts/verify_milestones.py --dump out/milestones  # 导出切出来的段落
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MILESTONES = [f"M{i}" for i in range(7)]

# ---------------------------------------------------------------------------
# 1. 阶段标记识别
# ---------------------------------------------------------------------------
#
# 只认两种写法, 避免把 MiniMax-M3 这类模型名误判成里程碑 M3:
#
#   (a) 标记出现在注释正文开头, 如  # M4: xxx  /  # M5 每日负荷上限
#   (b) 标记被圆括号包起来,   如  (M3 日结)  /  (M1)  /  (M2 执行台)
#
# (a) 排除掉 "注意: M3 不支持 OpenAI ..." (M3 在句中, 不是开头)
# (b) 排除掉 "(prompt 软约束对 M3 不够)" (M3 前面不是紧邻的左括号)

_COMMENT_PREFIX = re.compile(r"^\s*(?:#+|//+|<!--+|--+|\*+)\s*")
_PAREN_TAG = re.compile(r"\(\s*M([0-6])\b[^()]*\)")
_LEAD_TAG = re.compile(r"^M([0-6])(?![0-9.])\b")


def _strip_comment(line: str) -> str | None:
    """取注释正文; 不是注释行则返回 None."""
    m = _COMMENT_PREFIX.match(line)
    if not m:
        return None
    body = line[m.end():]
    if body.endswith("-->"):
        body = body[:-3]
    return body.strip()


def find_markers(text: str) -> list[tuple[int, str, str]]:
    """返回 [(行号, 里程碑, 标记原文)], 行号从 1 开始."""
    found: list[tuple[int, str, str]] = []
    for idx, raw in enumerate(text.split("\n"), start=1):
        body = _strip_comment(raw)
        if body is None:
            continue
        hit = None
        m = _PAREN_TAG.search(body)
        if m:
            hit = m.group(1)
        else:
            m = _LEAD_TAG.match(body)
            if m:
                hit = m.group(1)
        if hit:
            found.append((idx, f"M{hit}", raw.strip()))
    return found


# ---------------------------------------------------------------------------
# 2. Python: 切点状态 (tokenize)
# ---------------------------------------------------------------------------


def python_boundary_info(path: Path, boundary_lines: list[int]) -> dict[int, dict]:
    """返回 {切点行: {"depth": 该处括号深度, "spans": 跨切点的多行 token}}."""
    info: dict[int, dict] = {
        bl: {"depth": 0, "spans": []} for bl in boundary_lines
    }
    try:
        src = path.read_text(encoding="utf-8")
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError) as exc:
        return {bl: {"depth": 0, "spans": [f"文件无法分词: {exc}"]} for bl in boundary_lines}

    bracket_stack: list[str] = []
    pending = sorted(boundary_lines)
    span_idx = 0     # 扫描跨切点的多行 token
    depth_idx = 0    # 记录切点处的括号深度

    for tok in tokens:
        ttype, tstr, start, end, _ = tok
        sline, eline = start[0], end[0]

        # 切点行之前的多行 token => 切开会劈开
        # 先跳过"起点早于本 token"的切点 (它们已经被前面的 token 处理过),
        # 再看落在 (sline, eline] 区间里的切点 —— 那才是被这个 token 跨过的。
        while span_idx < len(pending) and pending[span_idx] < sline:
            span_idx += 1
        while span_idx < len(pending) and pending[span_idx] <= eline:
            bl = pending[span_idx]
            if sline < bl <= eline and ttype not in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.COMMENT,
                tokenize.INDENT,
                tokenize.DEDENT,
            ):
                info[bl]["spans"].append(
                    f"{tokenize.tok_name.get(ttype, ttype)}@{sline}-{eline}"
                )
            span_idx += 1

        # 记录切点处的括号深度 (取切点行第一个 token 之前的深度)
        while depth_idx < len(pending) and pending[depth_idx] <= sline:
            info[pending[depth_idx]]["depth"] = len(bracket_stack)
            depth_idx += 1

        if ttype == tokenize.OP:
            if tstr in "([{":
                bracket_stack.append(tstr)
            elif tstr in ")]}":
                if bracket_stack:
                    bracket_stack.pop()

    return info


# ---------------------------------------------------------------------------
# 3. JS: 切点状态 (自写扫描器)
# ---------------------------------------------------------------------------

_JS_BLOCK_KEYWORDS = {"else", "do", "try", "finally"}


def js_line_states(src: str) -> list[dict]:
    """逐行返回扫描状态.

    state = {
        "mode": code | line_comment | block_comment | sq | dq | template,
        "expr_open": 尚未闭合的表达式括号 (  [  {obj )  —— 对象字面量也归在这,
                    因为它和调用参数一样是"多行容器";
        "block_open": 尚未闭合的块级花括号 {block —— 函数体/if 块, 正常;
        "in_template": 是否在模板字符串内;
    }
    """
    n = len(src)
    i = 0
    mode = "code"
    expr_open: list[str] = []
    block_open = 0
    frames: list[dict] = []          # 模板串及其 ${} 插值嵌套
    states: list[dict] = []
    last_word = ""                   # 上一个标识符 (判断 '{' 是块还是对象)
    last_char = ""                   # 上一个有效字符

    def snapshot():
        states.append(
            {
                "mode": mode,
                "expr_open": list(expr_open),
                "block_open": block_open,
                "in_template": any(f["kind"] == "template" for f in frames),
            }
        )

    def is_word_char(ch: str) -> bool:
        return ch.isalnum() or ch in "_$"

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "\n":
            snapshot()
            i += 1
            continue

        if mode == "code":
            if ch == "/" and nxt == "/":
                mode = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                mode = "block_comment"
                i += 2
                continue
            if ch == "'":
                mode = "sq"
                i += 1
                continue
            if ch == '"':
                mode = "dq"
                i += 1
                continue
            if ch == "`":
                frames.append({"kind": "template"})
                mode = "template"
                i += 1
                continue
            if ch == "$" and nxt == "{":
                frames.append({"kind": "interp", "depth": 1})
                mode = "code"
                i += 2
                continue
            if ch in "([{":
                if ch == "{":
                    # 块级花括号: 前面是 ) ] } ; { 或关键字 else/do/try/finally
                    if last_char in ")]};{" or last_word in _JS_BLOCK_KEYWORDS:
                        block_open += 1
                    elif last_char == ">" and not expr_open:
                        pass  # 箭头函数体
                    else:
                        expr_open.append("{")
                else:
                    expr_open.append(ch)
                last_char = ch
                last_word = ""
            elif ch in ")]}":
                if ch == "}" and frames and frames[-1]["kind"] == "interp":
                    frames[-1]["depth"] -= 1
                    if frames[-1]["depth"] == 0:
                        frames.pop()
                        mode = "template"
                else:
                    if ch == "}" and block_open > 0:
                        block_open -= 1
                    elif expr_open:
                        expr_open.pop()
                last_char = ch
                last_word = ""
            elif is_word_char(ch):
                if last_word and (last_word[-1].isalnum() or last_word[-1] in "_$"):
                    last_word += ch
                else:
                    last_word = ch
                last_char = ch
            elif not ch.isspace():
                last_char = ch
                last_word = ""
            i += 1
            continue

        if mode == "line_comment":
            if ch == "\n":
                mode = "code"
            i += 1
            continue
        if mode == "block_comment":
            if ch == "*" and nxt == "/":
                mode = "code"
                i += 2
                continue
            i += 1
            continue
        if mode in ("sq", "dq"):
            if ch == "\\":
                i += 2
                continue
            if (mode == "sq" and ch == "'") or (mode == "dq" and ch == '"'):
                mode = "code"
            i += 1
            continue
        if mode == "template":
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                frames.pop()
                mode = "code"
                i += 1
                continue
            i += 1
            continue

    snapshot()
    return states


def js_boundary_info(path: Path, boundary_lines: list[int]) -> dict[int, dict]:
    info: dict[int, dict] = {bl: {"mode": "code", "expr_open": [], "spans": []}
                              for bl in boundary_lines}
    src = path.read_text(encoding="utf-8")
    states = js_line_states(src)
    for bl in boundary_lines:
        idx = bl - 1
        if idx >= len(states):
            info[bl]["spans"].append("行号越界")
            continue
        st = states[idx]
        info[bl]["mode"] = st["mode"]
        info[bl]["expr_open"] = st["expr_open"]
        if st["mode"] in ("sq", "dq", "block_comment"):
            info[bl]["spans"].append(f"切点落在 {st['mode']} 内")
        if st["in_template"]:
            info[bl]["spans"].append("切点落在模板字符串内")
    return info


# ---------------------------------------------------------------------------
# 4. JS: 顶层段落独立解析 (node --check)
# ---------------------------------------------------------------------------

_TOP_SECTION = re.compile(r"^//\s*=+\s*.*\bM([0-6])\b.*=+\s*$")


def js_top_sections(src: str) -> list[dict]:
    """把 app.js 按顶层 `// ====== ... (M<n>) ======` 标记切成段."""
    lines = src.split("\n")
    marks: list[tuple[int, str]] = []
    for idx, raw in enumerate(lines):
        m = _TOP_SECTION.match(raw)
        if m:
            marks.append((idx, f"M{m.group(1)}"))
    out: list[dict] = []
    for pos, (idx, ms) in enumerate(marks):
        end = marks[pos + 1][0] if pos + 1 < len(marks) else len(lines)
        out.append(
            {
                "milestone": ms,
                "start_line": idx + 1,
                "end_line": end,
                "text": "\n".join(lines[idx:end]),
            }
        )
    return out


def node_check(tmp_dir: Path, name: str, text: str) -> tuple[bool, str]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    f = tmp_dir / f"{name}.js"
    f.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        ["node", "--check", str(f)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


# ---------------------------------------------------------------------------
# 5. 里程碑清单 (每个里程碑该有的东西)
# ---------------------------------------------------------------------------


@dataclass
class MilestoneSpec:
    key: str
    title: str
    test_file: str
    files: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    routers: list[str] = field(default_factory=list)
    frontend: list[str] = field(default_factory=list)


SPECS: list[MilestoneSpec] = [
    MilestoneSpec(
        key="M0",
        title="数据底座: 多租户 + 迁移",
        test_file="tests/test_m0_tenant_migration.py",
        files=[
            "app/services/tenant.py",
            "app/db.py",
            "app/migrations/runner.py",
            "app/migrations/steps.py",
        ],
        tables=["families", "children"],
    ),
    MilestoneSpec(
        key="M1",
        title="统一任务池: 项目 / 培训 / 习惯",
        test_file="tests/test_m1_task_pool.py",
        files=[
            "app/routers/projects.py",
            "app/services/project_service.py",
            "app/services/assignment_service.py",
        ],
        tables=["projects"],
        routers=["projects"],
        frontend=[
            ("app/static/app.js", "快速添加 (M1 统一任务池)"),
            ("app/static/app.js", "设置页: 项目管理 (M1)"),
            ("app/static/index.html", "快速添加 (M1 统一任务池)"),
        ],
    ),
    MilestoneSpec(
        key="M2",
        title="执行台: 逾期任务可见",
        test_file="tests/test_m2_execution.py",
        files=["app/routers/board.py", "app/services/assignment_service.py"],
        frontend=[("app/static/app.js", "已逾期 (M2 执行台)")],
    ),
    MilestoneSpec(
        key="M3",
        title="日结: 晚间三问 + 每晚小结",
        test_file="tests/test_m3_daily_wrap.py",
        files=[
            "app/routers/daily_wrap.py",
            "app/services/daily_wrap_service.py",
            "app/migrations/steps.py",
        ],
        tables=["daily_reflections"],
        routers=["daily_wrap"],
        frontend=[("app/static/app.js", "晚间小结 (M3 日结)")],
    ),
    MilestoneSpec(
        key="M4",
        title="训练打卡: 时长 / 组数 / 段落",
        test_file="tests/test_m4_training.py",
        files=[
            "app/routers/training.py",
            "app/services/training_service.py",
        ],
        tables=["training_logs"],
        routers=["training"],
        frontend=[("app/static/app.js", "训练打卡 (M4)")],
    ),
    MilestoneSpec(
        key="M5",
        title="周课表 / 周负荷 / 周计划",
        test_file="tests/test_m5_week_plan.py",
        files=[
            "app/routers/week_plan.py",
            "app/services/week_plan_service.py",
            "app/services/schedule_service.py",
        ],
        tables=["schedule_slots", "plan_items"],
        routers=["week_plan"],
        frontend=[
            ("app/static/app.js", "周计划 (M5)"),
            ("app/static/index.html", "周计划 (M5)"),
        ],
    ),
    MilestoneSpec(
        key="M6",
        title="本周课后周报 + CSV",
        test_file="tests/test_m6_weekly_report.py",
        files=[
            "app/routers/report.py",
            "app/services/report_service.py",
        ],
        routers=["report"],
        frontend=[("app/static/app.js", "本周课后周报 (M6)")],
    ),
]


# ---------------------------------------------------------------------------
# 6. 验证主体
# ---------------------------------------------------------------------------


def collect_markers() -> tuple[dict[str, list[dict]], list[dict]]:
    """扫描 app/ 下所有源码, 按标记把代码块归属到各里程碑.

    返回 (owners, cut_notes); cut_notes 记录每个切点的档位 (安全/片段/危险)。
    """
    owners: dict[str, list[dict]] = {m: [] for m in MILESTONES}
    cuts: list[dict] = []

    src_files: list[Path] = sorted((ROOT / "app").rglob("*.py"))
    src_files += sorted((ROOT / "app" / "static").rglob("*.js"))
    src_files += sorted((ROOT / "app" / "static").rglob("*.html"))

    for path in src_files:
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        marks = find_markers(text)
        if not marks:
            continue
        lines = text.split("\n")
        boundary_lines = [ln for ln, _, _ in marks]

        if path.suffix == ".py":
            info = python_boundary_info(path, boundary_lines)
        else:
            info = js_boundary_info(path, boundary_lines)

        for ln, ms, _raw in marks:
            st = info.get(ln, {"depth": 0, "spans": [], "expr_open": []})
            spans = list(st.get("spans") or [])
            expr_open = list(st.get("expr_open") or [])
            depth = st.get("depth", 0)
            if spans:
                grade = "危险"
            elif expr_open or depth:
                grade = "片段"
            else:
                grade = "安全"
            if spans:
                detail = spans
            elif expr_open:
                detail = expr_open
            elif depth:
                detail = [f"括号深度={depth}"]
            else:
                detail = []
            cuts.append(
                {
                    "file": str(rel),
                    "line": ln,
                    "milestone": ms,
                    "grade": grade,
                    "detail": detail,
                }
            )

        for idx, (ln, ms, raw) in enumerate(marks):
            end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines) + 1
            owners[ms].append(
                {
                    "file": str(rel),
                    "line": ln,
                    "marker": raw,
                    "block_lines": [ln, end - 1],
                    "block": "\n".join(lines[ln - 1:end - 1]),
                    "grade": next(c["grade"] for c in cuts
                                  if c["file"] == str(rel) and c["line"] == ln),
                }
            )
    return owners, cuts


def check_spec(spec: MilestoneSpec) -> list[str]:
    problems: list[str] = []
    for f in spec.files:
        if not (ROOT / f).exists():
            problems.append(f"缺文件: {f}")
    if spec.tables:
        ddl = "\n".join(
            (ROOT / f).read_text(encoding="utf-8")
            for f in ("app/migrations/steps.py", "app/db.py")
            if (ROOT / f).exists()
        )
        for t in spec.tables:
            if not re.search(rf"\b{re.escape(t)}\b", ddl):
                problems.append(f"DDL 里找不到表: {t}")
    if spec.routers:
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        for r in spec.routers:
            if f"from .routers import {r} as" not in main:
                problems.append(f"app/main.py 未 import 路由 {r}")
            if f"{r}_router.router" not in main:
                problems.append(f"app/main.py 未注册路由 {r}")
    for rel, needle in spec.frontend:
        p = ROOT / rel
        if not p.exists():
            problems.append(f"缺文件: {rel}")
            continue
        if needle not in p.read_text(encoding="utf-8"):
            problems.append(f"{rel} 里找不到里程碑标记: {needle}")
    return problems


def run_tests(spec: MilestoneSpec) -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", spec.test_file, "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = [ln for ln in proc.stdout.strip().split("\n") if ln.strip()]
    return ("通过" if proc.returncode == 0 else "失败"), (tail[-1] if tail else "")


def self_test() -> bool:
    """自检: 证明检测器真能抓到"切点落在多行 token 内部"这种坏切点.

    没有自检的话, "全部安全"只是一句没人验证过的断言。
    """
    import tempfile

    cases: list[tuple[str, str, str, str, str]] = [
        (
            "py-危险",
            "py",
            "危险",
            'x = """\n# M3: 假标记落在三引号串里\n"""\n',
            "标记落在多行字符串内部",
        ),
        (
            "py-片段",
            "py",
            "片段",
            "foo(\n    # M3: 标记落在调用参数里\n    a, b)\n",
            "标记落在多行调用参数内部",
        ),
        (
            "py-安全",
            "py",
            "安全",
            "def f():\n    # M3: 正常语句级标记\n    return 1\n",
            "标记在语句级",
        ),
        (
            "js-危险",
            "js",
            "危险",
            "const s = `\n// ====== 假标记 (M3) ======\n`;\n",
            "标记落在模板字符串内部",
        ),
        (
            "js-片段",
            "js",
            "片段",
            "foo(\n  // ====== 假标记 (M3) ======\n  a, b);\n",
            "标记落在多行调用参数内部",
        ),
        (
            "js-安全",
            "js",
            "安全",
            "// ====== 真标记 (M3) ======\nfunction f() { return 1; }\n",
            "标记在顶层语句级",
        ),
    ]

    print("=" * 80)
    print("验证器自检 (negative control)")
    print("=" * 80)
    ok = True
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, kind, expect, src, why in cases:
            if kind == "py":
                p = tmp / f"{name}.py"
                p.write_text(src, encoding="utf-8")
                marks = find_markers(src)
                info = python_boundary_info(p, [ln for ln, _, _ in marks])
                st = info.get(marks[0][0], {"depth": 0, "spans": []})
                grade = "危险" if st["spans"] else ("片段" if st["depth"] else "安全")
            else:
                p = tmp / f"{name}.js"
                p.write_text(src, encoding="utf-8")
                marks = find_markers(src)
                info = js_boundary_info(p, [ln for ln, _, _ in marks])
                st = info.get(marks[0][0], {"spans": [], "expr_open": []})
                grade = "危险" if st["spans"] else ("片段" if st["expr_open"] else "安全")
            hit = grade == expect
            ok = ok and hit
            print(
                f"  {'✓' if hit else '✗'} {name:<10} 期望={expect:<4} 实得={grade:<4} ({why})"
            )
    print("-" * 80)
    print("自检通过" if ok else "自检失败 —— 检测器不可信, 别信上面的结论")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tests", action="store_true", help="跳过 pytest")
    ap.add_argument("--dump", default="", help="把切出来的里程碑段落导出到该目录")
    ap.add_argument("--json", default="", help="同时输出 JSON 报告到该路径")
    ap.add_argument("--self-test", action="store_true", help="只跑验证器自检")
    args = ap.parse_args()

    if args.self_test:
        return 0 if self_test() else 1

    owners, cuts = collect_markers()
    dump_dir = Path(args.dump) if args.dump else None
    if dump_dir:
        dump_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = (dump_dir / "_syntax") if dump_dir else Path("/tmp/_ms_syntax")

    rows: list[dict] = []
    failed = False

    for spec in SPECS:
        blocks = owners.get(spec.key, [])
        problems: list[str] = []
        notes: list[str] = []

        if not blocks:
            notes.append("源码里没有该里程碑的阶段标记, 只能按文件/表/测试验收")
        problems += check_spec(spec)

        # 切点档位
        my_cuts = [c for c in cuts if c["milestone"] == spec.key]
        for c in my_cuts:
            if c["grade"] == "危险":
                problems.append(
                    f"切点危险 {c['file']}:{c['line']} 落在多行 token 内部 {c['detail']}"
                )
            elif c["grade"] == "片段":
                notes.append(
                    f"切点为片段 {c['file']}:{c['line']} 位于容器字面量内部 {c['detail']}"
                )

        # JS 顶层段落独立解析
        js_ok = "-"
        js_path = ROOT / "app/static/app.js"
        if js_path.exists():
            js_src = js_path.read_text(encoding="utf-8")
            secs = [s for s in js_top_sections(js_src) if s["milestone"] == spec.key]
            if secs:
                allok = True
                for sec in secs:
                    ok, err = node_check(
                        tmp_dir, f"{spec.key}_{sec['start_line']}", sec["text"]
                    )
                    if not ok:
                        allok = False
                        problems.append(
                            f"app.js 段落 {sec['start_line']}-{sec['end_line']} "
                            f"独立解析失败: {err.splitlines()[:3]}"
                        )
                js_ok = "通过" if allok else "失败"

        test_state, test_tail = ("跳过", "") if args.no_tests else run_tests(spec)
        if test_state == "失败":
            problems.append(f"pytest 失败: {test_tail}")

        if dump_dir:
            md = dump_dir / spec.key
            md.mkdir(parents=True, exist_ok=True)
            for i, b in enumerate(blocks):
                safe = re.sub(r"[^\w.-]+", "_", b["file"])
                (md / f"{i+1:02d}_{safe}_{b['line']}.txt").write_text(
                    b["block"], encoding="utf-8"
                )

        verdict = "通过" if not problems else "失败"
        if verdict == "失败":
            failed = True
        rows.append(
            {
                "milestone": spec.key,
                "title": spec.title,
                "block_count": len(blocks),
                "blocks": [
                    {"file": b["file"], "line": b["line"], "marker": b["marker"],
                     "grade": b["grade"]}
                    for b in blocks
                ],
                "js_section_check": js_ok,
                "test": test_state,
                "test_detail": test_tail,
                "notes": notes,
                "problems": problems,
                "verdict": verdict,
            }
        )

    print("=" * 80)
    print("里程碑完成度验证 · 按阶段标记切分")
    print("=" * 80)
    print(f"{'里程碑':<7}{'标记块':>6}{'切点':>6}{'JS段落':>8}{'测试':>7}  {'结论':<5} 标题")
    print("-" * 80)
    for r in rows:
        grades = [b["grade"] for b in r["blocks"]]
        cut = "—" if not grades else (
            "安全" if all(g == "安全" for g in grades)
            else ("危险" if "危险" in grades else "片段")
        )
        print(
            f"{r['milestone']:<7}{r['block_count']:>6}{cut:>6}"
            f"{r['js_section_check']:>8}{r['test']:>7}  {r['verdict']:<5} {r['title']}"
        )
    print("-" * 80)

    for r in rows:
        if r["notes"]:
            print(f"\n[{r['milestone']}] 说明:")
            for n in r["notes"]:
                print(f"  · {n}")
        if r["problems"]:
            print(f"\n[{r['milestone']}] 未通过:")
            for p in r["problems"]:
                print(f"  ! {p}")

    if not failed:
        print("\n全部里程碑通过。")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"rows": rows, "cuts": cuts},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
