"""登记页预览表紧凑化测试 (2026-09-28).

用户截图批注五处:
  1. Tab「解析」改名为「登记」
  2. 表格右侧多出一截没利用(左边空间也没用上)
  3. 「置信」列文字换行, 竖着排三行
  4. 「原文」列影响行高
  5. 删除操作是红色, 危险

根因: 预览表 10 列挤在 main 的 720px 里, 浏览器按内容分配宽度,
长文本把宽度吃光, 末尾几列只能折行, 行高暴涨。

这些用例锁住:
  1. 固定列宽 + 不换行, 行高稳定
  2. 置信度改用单字符图标
  3. collectRows 仍能正确读回置信度与原文 —— 这是最容易踩的坑,
     标记 class 从 confidence-pill 换成 conf-dot, 读取处必须同步,
     否则低置信度会静默退化成 medium, "需核对"提醒在入库时丢掉
  4. 删行按钮为中性色
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

node = shutil.which("node")
jsdom_available = False
if node:
    try:
        subprocess.run([node, "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


def _code(src: str) -> str:
    s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))


def _block(css: str, sel: str) -> str:
    css_no_comment = re.sub(r"/\*[\s\S]*?\*/", "", css)
    m = re.search(r"^[ \t]*" + re.escape(sel) + r" \{", css_no_comment, re.M)
    if not m:
        return ""
    end = css_no_comment.find("\n}", m.start())
    return css_no_comment[m.start():end] if end != -1 else ""


# ====== 1. Tab 改名 ======

def test_entry_tab_renamed(index_html):
    """「解析」描述的是 AI 侧机制, 家长做的事是"把作业登记进来"。"""
    m = re.search(r'<button[^>]*data-tab="entry"[^>]*>(.*?)</button>', index_html)
    assert m, "缺少登记 Tab"
    # 2026-10-12 起按钮内含 <svg> 图标与 <span> 包裹, 剥掉后再比文字
    label = re.sub(r"<svg.*?</svg>|<[^>]+>", "", m.group(1), flags=re.S).strip()
    assert label == "登记", m.group(1)


def test_internal_tab_id_unchanged(index_html):
    """改名只动可见文案, data-tab / view id 必须不动。"""
    assert 'data-tab="entry"' in index_html
    assert 'id="view-entry"' in index_html


def test_action_wording_kept(index_html):
    """页面内动作文案保持"解析" —— 那是按钮, 不是 Tab 名。"""
    assert 'id="btn-parse"' in index_html
    assert ">解析</button>" in index_html


# ====== 1b. 录入区表单 (2026-10-12 家长截图批注) ======

def test_batch_date_label_is_short(index_html):
    """"本次作业归属日"在 flex 行里被 input[type=date] 的 width:100%
    挤成"本次作业归/属日"两行。标签文案先收短。"""
    assert "本次作业归属日" not in index_html, "旧的长标签仍在, 窄宽度下会折行"
    assert ">归属日期" in index_html


def test_batch_date_label_does_not_wrap(styles):
    """光改文案不够: input 基础规则是 width:100%, 换个稍长的标签
    照样会被挤成两行。必须锁住 nowrap + 日期框定宽。"""
    b = _block(styles, ".entry-batch label")
    assert "white-space: nowrap" in b, "标签必须禁止折行"
    css = re.sub(r"/\*[\s\S]*?\*/", "", styles)
    assert re.search(r'\.entry-batch label input\[type="date"\] \{[^}]*width: 150px', css), \
        "日期框需定宽, 否则会吃掉标签的横向空间"


def test_entry_tools_exist(index_html):
    """示例原先只写在 placeholder 里, 一开始输入就消失, 家长无法回看格式。"""
    for attr in ('id="btn-fill-sample"', 'id="btn-clear-raw"'):
        assert attr in index_html, f"缺少 {attr}"


def test_char_count_has_id(index_html, app_js):
    """入库后复位 warn 的代码按 id="char-count" 取元素, 之前 HTML 里只有
    class 没有 id, 那段复位从来没生效过。"""
    assert 'id="char-count"' in index_html, "字数统计容器缺 id, warn 复位会静默失效"
    assert "syncCharCount()" in app_js
    assert "getElementById('char-count')" not in app_js, \
        "旧的按 id 复位写法已被 syncCharCount 取代, 别再引回"


def test_raw_text_scales_with_viewport(styles):
    """固定 160px 时内容只占屏顶三分之一, 页面重心严重偏上。"""
    b = re.search(r"#raw-text \{([^}]*)\}", re.sub(r"/\*[\s\S]*?\*/", "", styles))
    assert b, "缺少 #raw-text 高度规则"
    assert "clamp(" in b.group(1) and "vh" in b.group(1), "录入框高度应随视口伸缩"


# ====== 2. 固定列宽 ======

def test_table_layout_fixed(styles):
    """按内容分配宽度是行高暴涨的根源, 必须改成固定布局。"""
    b = _block(styles, ".preview-table")
    assert "table-layout: fixed" in b
    assert re.search(r"width:\s*100%", b)


def _col_widths(styles):
    """按列号(1-based)返回 {列号: 宽度}, 不依赖数组下标。

    用 re.findall 的顺序列表取下标会被 auto 列坑到: 正则只抓 \\w+ 会把
    "auto" 也抓进来, 但一旦某列漏写或调序, 下标全部前移, 断言会去检查
    错误的列并给出误导性的报错。
    """
    out = {}
    for col, w in re.findall(
            r"\.preview-table th:nth-child\((\d+)\),\s+\.preview-table td:nth-child\(\d+\)\s+\{ width: (\w+)", styles):
        out[int(col)] = w
    return out


def test_all_columns_have_width(styles):
    """9 列全部定宽, 任务名列 auto 吃掉剩余空间。

    「截止」列已移除, 列数从 10 变 9。
    """
    ws = re.findall(
        r"\.preview-table th:nth-child\(\d+\),\s+\.preview-table td:nth-child\(\d+\)\s+\{ width: (\w+)", styles)
    assert len(ws) == 9, ws
    assert ws[2] == "auto", "第 3 列(任务名)应为 auto"
    assert all(re.fullmatch(r"\d+px", w) for i, w in enumerate(ws) if i != 2), ws


def test_cells_no_wrap(styles):
    b = _block(styles, ".preview-table td")
    assert "white-space: nowrap" in b
    assert "text-overflow: ellipsis" in b
    assert "overflow: hidden" in b


def test_header_no_wrap(styles):
    """表头也不能换行。

    之前只给 td 加了 nowrap, th 漏在外面。「预估分」三字在 66px 里
    刚好放得下, 换成「预计用时」四字后表头会折成两行, 把刚压下去的
    行高又顶回来 —— 属于改文案才暴露的隐性回归, 必须锁住。
    """
    b = _block(styles, ".preview-table th")
    assert "white-space: nowrap" in b, "表头缺少 nowrap, 长表头会折行"


def test_task_column_gets_usable_width(styles):
    """任务名列必须真的"吃到剩余", 而不是被固定列挤成一条缝。

    桌面 main 上限 720px, 表格实得 688px。固定列原来合计 560px,
    "任务名吃掉剩余"实际只吃到 128px —— 七个汉字就被截断, 家长
    根本没法核对是哪条作业, 而这恰恰是预览表唯一存在的理由。

    这条锁的是**预算关系**而不是某个绝对值: 桌面端各列还要按内容微调
    (控件列不能低于装下控件的下限, 见下面两条), 一改就会连锁反应。
    真正不能破的是"固定列 + 任务名下限 <= 688px"。
    """
    widths = _col_widths(styles)
    fixed = sum(int(re.sub(r"px$", "", w)) for c, w in widths.items() if c != 3)
    assert fixed + 180 <= 688, (
        f"固定列合计 {fixed}px, 加上任务名下限 180px 超出桌面表格的 688px, "
        f"表格会横向溢出: {widths}"
    )
    assert widths[3] == "auto", "任务名列仍应是 auto(吃掉剩余)"


def test_est_column_wide_enough(styles):
    """「预计用时」四字 + 单元格内边距, 列宽要放得下。

    四字约 56px, 加左右各 12px 内边距约 80px, 故下限取 80px。
    「截止」列移除后该列从第 7 位前移到第 6 位, 按列号取值而非下标 ——
    下标法在调序后会检查到「置信」列(42px)并报出误导性的断言失败。
    """
    widths = _col_widths(styles)
    assert 6 in widths, widths
    # 抓到的值带 px 后缀(如 "82px"), 先剥掉再比数值
    est = re.sub(r"px$", "", widths[6])
    assert est.isdigit(), f"第 6 列宽度应为固定像素: {widths[6]}"
    assert int(est) >= 80, f"第 6 列(预计用时) 宽度 {est}px 不足以容纳四字表头"


def test_action_column_header_and_width(styles, app_js):
    """末列表头是「操作」, 列宽要放得下两个字。

    原来是空表头 + 「删」按钮(40px), 加上「操作」二字后必须加宽,
    否则表头被 nowrap 截断 —— 表头折行会把行高又顶回来。
    只在预览表那一段里找, 不能全文搜 "<th></th>": 原文弹窗里另有
    一张键值表, 它的末列表头本来就是空的, 与预览表无关。
    """
    thead = re.search(r"<table class=\"preview-table\">.*?</thead>", app_js, re.S)
    assert thead, "未找到预览表表头"
    head = thead.group(0)
    assert "<th>操作</th>" in head
    assert "<th></th>" not in head, "预览表末列表头不应为空"
    widths = _col_widths(styles)
    w = re.sub(r"px$", "", widths[9])
    assert w.isdigit(), widths[9]
    # 「操作」两字约 28px + 左右内边距约 24px
    assert int(w) >= 52, f"第 9 列(操作) 宽度 {w}px 放不下两字表头"


def test_subject_column_fits_two_chars(styles):
    """科目列里装的是三段按钮, 78px 的列必须真的装得下两个字。

    原来是原生 select: 78px 的列扣掉单元格左右各 12px 只剩 54px; select
    再扣掉基础样式的左右各 12px, 内容区只剩 30px —— 放不下"语文"两个字
    加右侧下拉箭头, 浏览器实际只渲染出一个"语"字, 家长分不清语文还是
    数学。

    2026-11-03 起科目列换成三段按钮 (全出血贴住单元格两侧), 于是这条
    断言从"select 的内边距要收窄"改成"该格内边距要归零 + 按钮字号要够
    小到 26px 一段装得下两个字"。列宽本身不许变 —— 变了就意味着从任务名
    列抢走了宽度, 而任务名正是家长核对 AI 解析结果的依据。
    """
    widths = _col_widths(styles)
    raw = re.sub(r"px$", "", widths[2])
    assert raw.isdigit(), f"第 2 列(科目) 宽度应为固定像素: {widths[2]}"
    col = int(raw)
    assert col == 78, f"科目列宽变成 {col}px —— 列宽没有余量, 加宽就是从任务名列抢的"

    # 该格左右内边距归零: 三段按钮要吃满整列。
    cell = _block(styles, ".preview-table td.col-subject")
    assert cell, "预览表内未找到针对科目单元格的样式"
    tight = cell.replace(" ", "")
    for side in ("left", "right"):
        assert f"padding-{side}:0" in tight, \
            f"科目格 padding-{side} 未归零, 三段按钮吃不满整列"

    # 每段 78/3 = 26px, 字号得够小才放得下两个字。
    btn = _block(styles, ".preview-table .seg-btn")
    assert btn, "预览表内未找到分段按钮样式"
    assert "var(--fs-caption-sm)" in btn, "分段按钮字号必须是次级字号, 否则一段放不下两个字"
    assert re.search(r"flex:\s*1\s+1\s+0", btn), "三段按钮应等分整列宽度"


# ====== 3b. 「预估分」->「预计用时」(2026-09-28) ======

def test_no_stale_old_wording(app_js):
    """同一个字段在两个界面不能叫不同名字。"""
    code = _code(app_js)
    assert "预估分" not in code, "仍残留旧叫法「预估分」"


def test_preview_header_uses_new_name(app_js):
    code = _code(app_js)
    assert "<th>预计用时</th>" in code


def test_manual_form_label_uses_new_name(app_js):
    """手动添加弹窗的标签同步 —— 改一处不改另一处会让人以为
    是两个不同的字段。

    2026-09-28 表单改网格布局后, 标签文字从 <label> 直属文本变成
    <span class="form-label">, 这里跟着改断言形式, 但守的仍是同一个意图:
    弹窗里必须出现「预计用时」这四个字。
    """
    code = _code(app_js)
    assert '<span class="form-label">预计用时</span>' in code


def test_validation_toast_uses_new_name(app_js):
    code = _code(app_js)
    assert "预计用时必须在 1~180 分钟" in code
    assert "预估分钟必须在" not in code


def test_source_modal_uses_new_name(app_js):
    """原文弹窗是完整句式, 应与表头一致。"""
    code = _code(app_js)
    assert "预计用时: ${item.est_minutes} 分钟" in code
    assert "预估用时: ${item.est_minutes} 分钟" not in code


def test_board_inline_label_kept(app_js):
    """看板行内保持简写「预估」—— 它紧贴数字(预估 30)且与
    「实际用时」成对, 展开成「预计用时 30」反而冗余。"""
    code = _code(app_js)
    assert '<span class="est-label">预估</span>' in code


def test_settings_labels_keep_baseline_wording(app_js):
    """设置页说的是 AI 估算基准(各科预估用时), 与家长填的
    预计用时不是一回事, 不应跟着改。"""
    code = _code(app_js)
    assert "各科预估用时（分钟）" in code
    assert "各作业类型预估用时（分钟）" in code


def test_source_column_truncates(styles):
    b = _block(styles, ".preview-table .col-src small")
    assert "white-space: nowrap" in b
    assert "text-overflow: ellipsis" in b
    assert "display: block" in b


def test_narrow_screen_scrolls(styles):
    """窄屏 10 列压不动, 让外层横向滚动而不是压更窄。"""
    assert re.search(r"@media \(max-width: 760px\)[\s\S]{0,200}?overflow-x: auto", styles)
    assert re.search(r"@media \(max-width: 760px\)[\s\S]{0,200}?min-width: 640px", styles)


def test_inputs_fill_column(styles):
    """fixed 布局下 input 不铺满会留空洞。"""
    b = _block(styles, ".preview-table input, .preview-table select")
    assert re.search(r"width:\s*100%", b)
    assert "min-width: 0" in b


# ====== 3. 置信度图标 ======

def test_confidence_dot_defined(app_js):
    assert "function confidenceDot(" in app_js
    assert "CONFIDENCE_META" in app_js


def test_meta_declared_before_use(app_js):
    """先定义再用。顺序颠倒因函数体延迟求值不会真抛错, 但容易误读。"""
    assert app_js.index("const CONFIDENCE_META") < app_js.index("function confidenceDot")


def test_confidence_meta_has_all_levels(app_js):
    m = re.search(r"const CONFIDENCE_META = \{(.*?)\};", app_js, re.S)
    assert m
    for lvl in ("high", "medium", "low"):
        assert lvl in m.group(1)


def test_pill_kept_for_wide_layouts(app_js):
    """看板与原文弹窗有宽度, 仍需带文字的标签。"""
    assert "function confidencePill(" in app_js


def test_pill_and_dot_both_styled(styles):
    assert re.search(r"\.confidence-pill \{", styles)
    assert re.search(r"\.conf-dot \{", styles)
    for lvl in ("high", "medium", "low"):
        assert re.search(r"\.conf-dot\." + lvl, styles)


def test_conf_dot_is_single_char(app_js):
    """多字标签必然折行, 必须是单字符图标。"""
    m = re.search(r"const CONFIDENCE_META = \{(.*?)\};", app_js, re.S)
    icons = re.findall(r"icon: '(.+?)'", m.group(1))
    assert icons and all(len(i) <= 2 for i in icons), icons


def test_conf_dot_has_title_and_aria(app_js):
    """图标化后信息不能丢: title 给悬停, aria 给读屏。"""
    b = app_js.split("function confidenceDot(")[1].split("\n}")[0]
    assert 'title="${conf.tip}"' in b
    assert "aria-label=" in b


# ====== 3c. 置信度按档位标色 (2026-11-02) ======

def test_confidence_tokens_defined(styles):
    """三档各一套色令牌, 颜色只在 :root 定义一次。"""
    for tok in ("--conf-high", "--conf-high-t", "--conf-medium", "--conf-medium-t",
                "--conf-low", "--conf-low-t"):
        assert f"{tok}:" in styles, f"缺少置信度色令牌 {tok}"


def test_confidence_tokens_reuse_palette(styles):
    """令牌必须是现有色板的别名, 不能新增色值。

    新增色值就得重算 docs/07 §2 与 docs/08 的对比度表, 而置信度色
    复用学科/状态色板的绿-琥珀-红三色, 对比度基线不用动。"""
    for tok, alias in (("--conf-high", "--st-done-c"), ("--conf-high-t", "--st-done-t"),
                       ("--conf-medium", "--st-doing-c"), ("--conf-medium-t", "--st-doing-t"),
                       ("--conf-low", "--st-late-c"), ("--conf-low-t", "--st-late-t")):
        m = re.search(re.escape(tok) + r":\s*([^;]+);", styles)
        assert m, f"{tok} 未定义"
        assert alias in m.group(1), f"{tok} 应指向 {alias}, 实际 {m.group(1)}"


def test_confidence_high_is_not_neutral(styles):
    """高置信度不能是中性灰。

    之前 .conf-dot.high 用 --text-3, 在录入页那一列里和普通弱化文字
    长得一模一样, 家长扫过去读不出置信度状态 (功能设计 §2.4 第 10 条
    要求"明确标记置信度")。"""
    for sel in (".conf-dot.high", ".confidence-pill.high"):
        b = _block(styles, sel)
        assert b, f"缺少 {sel} 规则"
        assert "--text-3" not in b, f"{sel} 仍用中性灰, 读不出置信状态"
        assert "var(--conf-high)" in b, f"{sel} 应使用 --conf-high"


def test_confidence_levels_use_distinct_colors(styles):
    """三档必须是三个不同的颜色 —— 共用一色等于没标色。"""
    tokens = []
    for lvl in ("high", "medium", "low"):
        b = _block(styles, f".conf-dot.{lvl}")
        m = re.search(r"color:\s*var\((--conf-[a-z-]+)\)", b)
        assert m, f"{lvl} 档没有用置信度色令牌: {b.strip()}"
        tokens.append(m.group(1))
    assert len(set(tokens)) == 3, f"三档颜色必须互不相同: {tokens}"


def test_dot_and_pill_share_same_colors(styles):
    """圆点(录入页)与标签(看板/原文弹窗)必须同色 —— 一套颜色编码贯穿全站。"""
    for lvl in ("high", "medium", "low"):
        dot = re.search(r"color:\s*var\((--conf-[a-z-]+)\)",
                         _block(styles, f".conf-dot.{lvl}"))
        pill = re.search(r"color:\s*var\((--conf-[a-z-]+)\)",
                         _block(styles, f".confidence-pill.{lvl}"))
        assert dot and pill, f"{lvl} 档缺颜色规则"
        assert dot.group(1) == pill.group(1), \
            f"{lvl} 档两处颜色不一致: {dot.group(1)} vs {pill.group(1)}"


def test_confidence_level_normalized(app_js):
    """档位必须先归一再渲染。

    颜色是按 class 挂上去的, 档位认不出来就没有颜色 (高置信度会退回
    中性灰, 正是这次要修的问题); 原值直接拼进 class 还会把奇怪字符串
    带进 DOM, 后面 collectRows 再从 class 上读档位就读不回来。"""
    code = _code(app_js)
    assert "function normalizeConfidence(" in code, "缺少档位归一函数"
    body = code.split("function normalizeConfidence(")[1].split("\n}")[0]
    assert "'medium'" in body, "未知档位应落到 medium"
    for fn in ("confidenceDot", "confidencePill"):
        blk = code.split(f"function {fn}(")[1].split("\n}")[0]
        assert "normalizeConfidence(" in blk, f"{fn} 必须先归一档位"


def test_preview_row_uses_normalized_level(app_js):
    """预览表的行 class 与默认勾选也要用归一后的档位。

    否则上游给的不是 low 时, 低置信度那行会被默认勾上, "需核对"的
    提醒就静默丢了。"""
    code = _code(app_js)
    body = code.split("function renderPreviewTable(")[1]
    assert "normalizeConfidence(" in body
    assert "confidence-${it.confidence}" not in body, "行 class 仍直接用原始档位"
    assert "it.confidence !== 'low'" not in body, "默认勾选仍直接比原始档位"


# ====== 4. collectRows 必须同步 (关键回归) ======

def test_collect_rows_uses_new_conf_class(app_js):
    """标记 class 换了, 读取处必须跟着换。

    否则 classList 永远匹配不到 -> 全部落到 medium ->
    低置信度的"需核对"提醒在入库时静默丢失。
    """
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert ".col-conf .conf-dot" in body, "应从 .col-conf .conf-dot 读取置信度"
    assert ".confidence-pill" not in body, "不应再读旧的 confidence-pill"


def test_collect_rows_uses_named_source_cell(app_js):
    """原文不能用 querySelector('small')(取第一个 small), 要用 .col-src。"""
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert ".col-src small" in body
    assert re.search(r"querySelector\('small'\)", body) is None, \
        "仍取'第一个 small', 以后行内多出个 small 就会读错字段"


def test_collect_rows_has_fallback(app_js):
    """缺标记时要有兜底, 不能抛错。"""
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert ": 'medium'" in body


def test_collect_rows_reads_est_via_helper(app_js):
    """预计用时不能直接 parseInt(input.value)。

    数字输入框会把浏览器不认的字符串 (全角数字 / 带空格 / 带单位) 静默消毒成
    空串: 表格里每行都显示合法数字, collectRows 读回来却是 NaN, 整批就被
    "预计用时必须在 1~180 分钟" 拦下 (2026-11-05 家长报障)。
    必须走 readEstMinutes: 读不出值时回落到本行渲染时留存的 data-est。
    """
    code = _code(app_js)
    body = code.split("function collectRows(")[1]
    assert "readEstMinutes(get('est_minutes'), tr)" in body
    assert "parseInt(get('est_minutes').value" not in body
    assert "tr.dataset.est = String(estVal)" in code, \
        "渲染时要把合法的预计用时留一份在 tr.dataset.est 上, 读不出值时才有得回落"
    assert 'value="${estVal}"' in code


def test_read_est_minutes_falls_back_and_normalizes(app_js):
    code = _code(app_js)
    body = code.split("function readEstMinutes(")[1].split("\n}")[0]
    assert "parseEstInput(tr.dataset.est)" in body, "输入框读不出值时要回落 data-est"
    assert "estDefault(" in body, "data-est 也没有时要按科目基线兜底"
    # 中文输入法打出的全角数字在 number 输入框里同样是空值, 必须能转回半角
    assert "String.fromCharCode(c.charCodeAt(0) - 0xFEE0)" in _code(app_js)


def test_est_error_points_to_the_row(app_js):
    """越界时要说清是哪一行, 否则家长只能在整张表里逐行找。"""
    code = _code(app_js)
    body = code.split("async function confirmBatch(")[1]
    assert "预计用时必须在 1~180 分钟" in body
    assert "${it.task_name}" in body


def test_rows_have_named_classes(app_js):
    code = _code(app_js)
    for cls in ("col-conf", "col-src", "col-del"):
        assert f'class="{cls}"' in code, f"缺少 {cls}"


def test_source_cell_has_title(app_js):
    """截断了就得能看全文。

    用正则而不是字面量拼接: 原来断言的是 'class="col-src"><small title=',
    要求 class 与 > 紧挨着 —— 2026-09-29 给这个 td 加了 data-label
    (窄屏卡片布局的标签, 见 styles.css §26c) 之后, 中间多了一个属性,
    字面量就断了。契约本身没变: col-src 单元格里第一个元素仍是带 title
    的 <small>。这里放宽的只是"属性之间可以有别的东西", 不是放宽语义。
    """
    code = _code(app_js)
    assert re.search(r'class="col-src"[^>]*>\s*<small title=', code)


# ====== 5. 删行按钮中性色 ======

def test_row_del_no_danger_class(app_js):
    code = _code(app_js)
    assert not re.search(r'class="[^"]*danger[^"]*row-del', code)
    assert re.search(r'class="ghost row-del"', code)


def test_row_del_neutral_colors(styles):
    b = _block(styles, ".preview-table .row-del")
    assert b, "缺少 .preview-table .row-del 规则"
    for danger in ("--status-overdue", "--status-skipped", "#C0392B", "red"):
        assert danger not in b, f"不应使用危险色 {danger}"


def test_row_del_has_hover(styles):
    assert re.search(r"\.preview-table \.row-del:hover \{", styles)
    h = _block(styles, ".preview-table .row-del:hover")
    assert "var(--border)" in h or "var(--surface-2)" in h


def test_row_del_aria_label(app_js):
    code = _code(app_js)
    assert 'aria-label="删除这一行"' in code


# ====== 6. 运行时 (jsdom) ======

HARNESS = r"""
const http = require('http');
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath] = process.argv.slice(2);

const ITEMS = { items: [
  { subject: '语文', task_name: '完成课时作业P18-19,订正2题', due_date: '2026-09-28',
    deadline_time: null, is_optional: false, est_minutes: 25, confidence: 'low',
    source_span: '1、完成课时作业P18-19,订正2题。2.练字10 小(抄写词句)。' },
  { subject: '数学', task_name: '预习卡', due_date: '2026-09-28',
    // 非空: 验证表格已无「截止」列, 但 AI 解析出的 deadline_time 仍原样入库。
    // 这条是「周四早读8:20听写」这类只有 AI 能提取的信息, 丢了就是丢数据。
    deadline_time: '2026-09-30T08:20:00', is_optional: false, est_minutes: 15, confidence: 'high',
    source_span: '2.预习卡。' },
  { subject: '英语', task_name: '阅读课外书', due_date: '2026-09-28',
    deadline_time: null, is_optional: true, est_minutes: 20, confidence: 'medium',
    source_span: '3.阅读课外书。' },
]};

const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
let batch = null;
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/parse') && o && o.method === 'POST') return ok(ITEMS);
  if (s.includes('/assignments/batch') && o && o.method === 'POST') { batch = JSON.parse(o.body); return ok({ created_ids: [1,2,3] }); }
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  return ok({});
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
// charts.js 必须先于 app.js 加载 (与 index.html 的顺序一致): 复盘页的图
// 走 charts.js 的封装。jsdom 没有 canvas 包, 图表会走"退化成表格"那条
// 降级路径 —— 这正是降级逻辑要守的行为, 所以这里**不**加载 echarts.min.js。
w.eval(fs.readFileSync(require('path').join(require('path').dirname(appJsPath), 'charts.js'), 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8') + '\n;globalThis.__e = () => switchTab("entry");globalThis.__cd = l => confidenceDot(l);');

(async () => {
  const out = { tabs: [] };
  out.tabs = [...doc.querySelectorAll('#tabs button')].map(b => b.textContent.trim());
  w.__e();
  await new Promise(r => setTimeout(r, 200));
  doc.getElementById('entry-date').value = '2026-09-28';
  doc.getElementById('raw-text').value = '测试原文';
  doc.getElementById('btn-parse').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 400));

  out.hasTable = !!doc.querySelector('.preview-table');
  out.rowCount = doc.querySelectorAll('.preview-table tbody tr').length;
  out.headers = [...doc.querySelectorAll('.preview-table thead th')].map(t => t.textContent.trim());
  out.cellCount = doc.querySelectorAll('.preview-table tbody tr')[0].children.length;
  out.hasDeadlineInput = !!doc.querySelector('[data-field="deadline_time"]');
  out.datasetDl = [...doc.querySelectorAll('.preview-table tbody tr')].map(tr => tr.dataset.dl);

  const dots = [...doc.querySelectorAll('.col-conf .conf-dot')];
  out.dots = dots.map(d => ({
    level: d.classList.contains('high') ? 'high' : d.classList.contains('medium') ? 'medium'
         : d.classList.contains('low') ? 'low' : '?',
    text: d.textContent.trim(), title: d.getAttribute('title'), aria: d.getAttribute('aria-label'),
  }));
  const srcCell = doc.querySelector('.col-src small');
  out.srcTitle = srcCell ? srcCell.getAttribute('title') : null;
  const del = doc.querySelector('.row-del');
  out.del = del ? { cls: del.getAttribute('class'), aria: del.getAttribute('aria-label') } : null;

  // 档位归一: 未知/中文写法必须落到三档之一, 否则渲染出来没有颜色
  out.oddLevels = ['低', 'LOW', '', null].map(l => {
    const m = String(w.__cd(l)).match(/class="conf-dot ([a-z]+)"/);
    return m ? m[1] : '?';
  });

  // 提交: 验证 collectRows 读回正确
  doc.getElementById('btn-confirm').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  await new Promise(r => setTimeout(r, 300));
  out.batch = batch;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_pv_harness.js"
    h.parent.mkdir(exist_ok=True)
    h.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(h), str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(ROOT / "app/static/vendor/flatpickr.min.js"),
             str(ROOT / "app/static/vendor/flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1500:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if h.exists():
            h.unlink()


needs_jsdom = pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")


@needs_jsdom
def test_live_tab_is_登记(live):
    """导航改成 3 个可见 Tab (今日/日历/复盘) + 3 个家长专属隐藏落点。

    2026-11-01 双角色改造: 原来的 6 个 Tab 里, 登记/周计划/设置 退到
    页面内入口与头像菜单, 但 DOM 上必须保留 (深链 #/entry 等仍要能路由),
    所以隐藏但存在 —— 这里按 DOM 顺序断言全部 6 个按钮。
    """
    assert live["tabs"] == ["今日", "日历", "复盘", "登记", "周计划", "设置"]


@needs_jsdom
def test_live_table_renders(live):
    assert live["hasTable"] is True
    assert live["rowCount"] == 3


@needs_jsdom
def test_live_dots_use_single_char(live):
    for d in live["dots"]:
        assert len(d["text"]) == 1, d
        assert d["title"], "图标必须保留 title 提示"
        assert d["aria"], "图标必须有 aria-label"


@needs_jsdom
def test_live_dot_levels_correct(live):
    assert [d["level"] for d in live["dots"]] == ["low", "high", "medium"]


@needs_jsdom
def test_live_source_has_title(live):
    assert live["srcTitle"] and "P18-19" in live["srcTitle"]


@needs_jsdom
def test_live_odd_confidence_levels_normalized(live):
    """未知/中文档位必须归一到三档之一。

    颜色是按 class 挂上去的, 档位认不出来就没有颜色 —— 高置信度会退回
    中性灰, 正是 2026-11-02 要修的那个问题。"""
    assert live["oddLevels"] == ["low", "low", "medium", "medium"], live["oddLevels"]


@needs_jsdom
def test_live_del_button_plain(live):
    assert live["del"]["cls"] == "ghost row-del", live["del"]
    assert "danger" not in live["del"]["cls"]
    assert live["del"]["aria"] == "删除这一行"


@needs_jsdom
def test_live_confidence_survives_roundtrip(live):
    """关键回归: 换了标记 class 后, 置信度必须仍能正确提交。

    低置信度行默认不勾选(设计 §9 坑 3), 所以只提交 2 条 ——
    这本身也验证了低置信度没被静默入库。
    """
    items = live["batch"]["items"]
    assert len(items) == 2, "低置信度行应默认不勾选"
    levels = [i["confidence"] for i in items]
    assert "high" in levels, f"高置信度应被读出: {levels}"
    assert "medium" in levels, f"中置信度应被读出: {levels}"
    assert levels.count("medium") == len(levels) - levels.count("high"), \
        "置信度不应全部退化成同一档"


@needs_jsdom
def test_live_source_span_correct_per_row(live):
    """原文必须取自各自的 .col-src, 不能串行。"""
    items = live["batch"]["items"]
    assert all(i["source_span"] for i in items), "原文不能为空"
    assert len({i["source_span"] for i in items}) == len(items), "各行原文应互不相同"
    high = next(i for i in items if i["confidence"] == "high")
    assert "预习卡" in high["source_span"], "原文与所属行应对应"


# ====== 7. 移除「截止」列 (2026-09-28) ======

@needs_jsdom
def test_live_headers_drop_deadline(live):
    """表头 9 列, 无「截止」, 末列为「操作」。

    表头与单元格数量必须一致 —— 只删 <th> 不删 <td> 会让最后一行多出
    一格空白, 视觉上就是表格右边突出一块。
    """
    assert "截止" not in live["headers"], live["headers"]
    assert live["headers"][-1] == "操作", live["headers"]
    assert len(live["headers"]) == 9, live["headers"]
    assert live["cellCount"] == len(live["headers"]), \
        f"单元格数({live['cellCount']})与表头数({len(live['headers'])})不一致"


@needs_jsdom
def test_live_no_deadline_input(live):
    """表格里不再有截止时间输入框。"""
    assert live["hasDeadlineInput"] is False


@needs_jsdom
def test_live_deadline_survives_without_input(live):
    """关键回归: 表里没有截止列, 但 AI 解析出的 deadline_time 不能丢。

    collectRows 若仍读 get('deadline_time').value, 会拿到 null 再取属性
    抛 TypeError, 整批入库失败; 若简单改成固定 null, 则会静默丢掉
    「周四早读 8:20 听写」这类只有 AI 能提取的信息。两条路都要防。
    """
    items = live["batch"]["items"]
    assert items, "应至少提交一行"
    high = next(i for i in items if i["confidence"] == "high")
    assert high["deadline_time"] == "2026-09-30T08:20:00", \
        f"AI 解析的截止时间应原样入库, 实际 {high['deadline_time']!r}"
    others = [i for i in items if i["confidence"] != "high"]
    assert all(i["deadline_time"] is None for i in others), \
        "AI 未给出截止时间的行应提交 null, 而不是空串"
