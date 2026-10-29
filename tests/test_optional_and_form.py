"""选做分组 + 手动添加弹窗表单 的视觉契约.

2026-09-28 两条用户反馈:
1. 看板底部「选做」默认展开, 且底色不要黄色高亮 —— 选做的定义就是
   优先级低, 用高饱和底色反而把它顶成视觉焦点, 与意图相反。
2. 手动添加弹窗的表单没对齐 —— 标签被输入框挤成「科/目」竖排。

这两处都是纯样式, 很容易在后续改版时被无意改回去, 所以用可量化
的断言(色值/属性存在性)锁住, 而不是靠人眼复查。

2026-10-02 追加三条(手动添加弹窗截图批注):
3. 「两行空闲」—— 字段行距加网格外边距, 弹窗多出近两行空白。
4. 「是否选做(默认否)」—— 选做字段的标签误写成「类型」, 且默认值
   没有在界面上写明。
5. 「这个位置不对」—— 「截止时间可以不填。」挂在弹窗最底部, 离它
   解释的输入框太远。

同样用可量化断言锁住: 这三处都是"看截图才能发现、改回去又不会报
错"的类型, 靠人复查必然漏。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _var(styles: str, name: str) -> str:
    m = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{3,8}})", styles)
    assert m, f"未找到 CSS 变量 --{name}"
    return m.group(1)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(hex_color: str) -> float:
    def ch(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hex_color)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ====== 1. 选做底色改成中性灰 ======

def test_optional_bg_is_neutral_grey(styles):
    """底色必须是低饱和的灰, 不能再是琥珀黄。

    判定用饱和度而不是"看起来像不像灰" —— RGB 三通道最大值与最小值
    之差小即低饱和, 黄色底这个差值会很大。
    """
    bg = _var(styles, "row-opt-bg")
    r, g, b = _rgb(bg)
    spread = max(r, g, b) - min(r, g, b)
    assert spread <= 12, f"{bg} 饱和度过高(通道差 {spread}), 选做底色应为中性灰"


def test_optional_bg_is_light(styles):
    """必须是浅色, 深灰会让选做变成最重的一块。"""
    bg = _var(styles, "row-opt-bg")
    assert _luminance(bg) > 0.7, f"{bg} 过暗, 选做行应保持浅底"


def test_optional_bg_still_distinct_from_white(styles):
    """再淡也要能和白卡片分开, 否则选做与必做完全无法区分。"""
    bg = _var(styles, "row-opt-bg")
    assert _contrast(bg, "#FFFFFF") >= 1.04, f"{bg} 与白色几乎无差异, 分不出选做"


def test_optional_text_still_readable(styles):
    """改成浅灰后正文对比度必须够 —— 变淡不能变成看不清。"""
    bg = _var(styles, "row-opt-bg")
    assert _contrast("#222222", bg) >= 4.5, f"正文在 {bg} 上对比度不足"


def test_optional_no_longer_overrides_text_brightness(styles):
    """撤回"把选做行文字提亮到 --text-1"的补偿。

    那条规则当初是为在深琥珀底上保住对比度。底色变浅后它反而有害 ——
    选做行的次要文字会比必做行更黑, 与"选做不显眼"完全相反。
    """
    block = re.search(
        r"\.assignment-row\.is-optional \.muted,[\s\S]*?\{([^}]*)\}", styles)
    assert block, "未找到选做行文字规则"
    assert "var(--text-2)" in block.group(1), \
        "选做行次要文字不应再被提到 --text-1, 那会让它比必做行更显眼"
    assert "var(--text-1)" not in block.group(1)


# ====== 2. 选做分组的展开规则 ======

def test_optional_card_uses_optional_group_card(app_js):
    """选做分组必须由共享的 optionalGroupCard() 渲染。

    2026-10-29: 看板与日历详情原先各写了一份 <details>, 展开规则要改
    就得改两处, 迟早有一处漏掉 —— 同一份数据两个界面表现不同, 家长
    会怀疑其中一边显示错了。现在收敛到一处, 两边都调它。
    """
    assert "function optionalGroupCard(" in app_js, "缺少共享的选做分组渲染"
    assert app_js.count("optionalGroupCard(") == 3, \
        "应只有定义 + 看板 + 日历详情三处引用"


def test_optional_group_open_rule_uses_done(app_js):
    """展开与否只看有没有没做完的选做。

    2026-09-28 曾要求选做默认展开, 理由是"家长看不到自己还欠着几件
    选做"。2026-10-29 追加: 全做完之后不再展开 —— 那一屏已经没有欠账,
    摊开只剩重复信息, 还把首屏往下挤。判据用 status !== 'done' 而不是
    "已收尾": 跳过的选做仍可撤销, 折叠起来家长就点不到了。
    """
    m = re.search(r"function optionalGroupOpen\((.*?)\)\s*\{([^}]*)\}", app_js, re.S)
    assert m, "未找到 optionalGroupOpen"
    assert "status !== 'done'" in m.group(2), \
        "判据必须是 status !== 'done' —— 用 settled 会把可撤销的跳过项也藏掉"


def test_optional_card_open_attribute_is_conditional(app_js):
    """details 上的 open 必须由规则决定, 不能写死。

    写死成 open 就等于回到"永远展开", 2026-10-29 的折叠就白做了。
    """
    m = re.search(r'<details class="card optional-card"[^>]*>', app_js)
    assert m, "未找到选做分组的 details 元素"
    assert "open" not in m.group(0), \
        f"open 被写死了, 应由 optionalGroupOpen 决定: {m.group(0)}"
    assert "optionalGroupOpen(items) ? ' open' : ''" in app_js, \
        "open 属性必须来自 optionalGroupOpen 的判断"


def test_optional_group_count_shows_done_ratio(app_js):
    """折叠后只剩一行摘要, 计数必须仍写清"做完了几件"。

    折叠省的是高度, 不是信息 —— 家长看到的应是"选做 (2) 2/2"。
    """
    assert '<span class="count">${doneN}/${items.length}</span>' in app_js, \
        "摘要里的计数必须是 doneN/总数"


# ====== 3. 弹窗表单网格对齐 ======

def test_manual_form_uses_grid(styles):
    """表单用两列网格: 标签固定一列, 控件占另一列。"""
    m = re.search(r"\.form-grid \{(.*?)\}", styles, re.S)
    assert m, "缺少 .form-grid 定义"
    block = m.group(1)
    assert "display: grid" in block
    assert "grid-template-columns" in block


def test_form_label_column_fixed_width(styles):
    """标签列必须是固定宽度, 否则又会被输入框挤压成竖排单字。"""
    m = re.search(r"\.form-grid \{(.*?)\}", styles, re.S)
    cols = re.search(r"grid-template-columns:\s*(\d+)px\s+minmax\(0,\s*1fr\)", m.group(1))
    assert cols, f"标签列未固定宽度: {m.group(1)[:120]}"
    # 「截止时间」四字约 56px, 加上内边距, 低于 64px 就会折行
    assert int(cols.group(1)) >= 64, f"标签列 {cols.group(1)}px 放不下四字标签"


def test_form_label_no_wrap(styles):
    """标签文字禁止换行 —— 这是"竖排成科/目"的直接防线。"""
    m = re.search(r"\.form-grid \.form-label \{(.*?)\}", styles, re.S)
    assert m, "缺少 .form-label 定义"
    assert "white-space: nowrap" in m.group(1)


def test_form_inputs_fill_column(styles):
    """控件铺满所在列, 且 min-width:0 防止日期框固有宽度撑破网格。"""
    m = re.search(r"\.form-grid input:not\(\[type=\"checkbox\"\]\),\s*\.form-grid select \{(.*?)\}",
                  styles, re.S)
    assert m, "缺少 .form-grid 控件宽度规则"
    block = m.group(1)
    assert "width: 100%" in block
    assert "min-width: 0" in block, "缺 min-width:0, 日期框会把网格撑破"


def _manual_form_html(app_js: str) -> str:
    """取出「手动添加」弹窗里那一段 .form-grid。

    必须按 openManualAddModal 这个函数去截, 不能取文件里第一个 form-grid:
    编辑弹窗(openEditAssignmentModal)也用同一套网格, 且定义在前面,
    全文首个匹配会落到编辑表单上, 于是这里断言的其实是另一个弹窗 ——
    手动添加真丢了字段也不会被发现。
    """
    m = re.search(r"function openManualAddModal\(\) \{(.*?)\n\}", app_js, re.S)
    assert m, "未找到 openManualAddModal"
    grid = re.search(r'<div class="form-grid">(.*?)</div>', m.group(1), re.S)
    assert grid, "openManualAddModal 内未找到 .form-grid 表单"
    return grid.group(1)


def test_manual_form_labels_marked(app_js):
    """每个字段都要有 .form-label 标签文字, 否则网格第一列空着。"""
    html = _manual_form_html(app_js)
    for text in ("归属日", "科目", "作业项", "作业类型", "预计用时", "是否选做", "截止时间"):
        assert f'<span class="form-label">{text}</span>' in html, f"{text} 缺少标签"


def test_manual_form_all_fields_present(app_js):
    """七个字段的 id 不能因改布局丢失 —— 提交逻辑按 id 取值。

    作业类型现在是自绘下拉, 它的 input 仍然叫 m-type: 提交时
    getElementById('m-type') 要能取到值, 改名会静默变成 undefined,
    存进去的 task_type 永远是 null。
    """
    html = _manual_form_html(app_js)
    for fid in ("m-due", "m-subject", "m-name", "m-est", "m-opt", "m-dl"):
        assert f'id="{fid}"' in html, f"{fid} 丢失, 提交会取不到值"
    # 作业类型的 input 由 taskTypeComboHtml 生成, 不在 .form-grid 的字面量里,
    # 因此这条断言要落到 app.js 整体上。
    assert "taskTypeComboHtml('m-type'" in app_js, "m-type 下拉缺失"


def test_manual_field_order_matches_edit(app_js):
    """两个弹窗的字段顺序必须一致。

    用户 2026-10-02 指定的顺序是
    归属日→科目→作业项→作业类型→预计用时→是否选做→截止时间。
    之前两个弹窗顺序不同(新增把预计用时排在作业项前面), 家长来回切换
    时同一个字段在两个不同位置, 每次都要重新找。
    """
    spec = ["归属日", "科目", "作业项", "作业类型", "预计用时", "是否选做", "截止时间"]
    manual = re.findall(r'<span class="form-label">([^<]+)</span>', _manual_form_html(app_js))
    assert manual == spec, f"手动添加字段顺序与设计不符: {manual}"

    block = app_js.split("function openEditAssignmentModal(")[1].split("\n}")[0]
    grid = re.search(r'<div class="form-grid">(.*?)</div>', block, re.S).group(1)
    edit = re.findall(r'<span class="form-label">([^<]+)</span>', grid)
    assert edit == spec, f"编辑弹窗字段顺序与设计不符: {edit}"


def test_manual_form_date_pickers_kept(app_js):
    """日期框必须仍带组件类, 否则弹窗里会露出难看的原生控件。"""
    assert 'id="m-due" type="date" class="js-datepicker"' in app_js
    assert 'id="m-dl" type="datetime-local" class="js-datetimepicker"' in app_js


def test_est_input_width_bounded(styles):
    """数字输入框限宽, 不铺满整列 —— 400px 宽的数字框很怪。"""
    m = re.search(r"\.form-grid input\[type=\"number\"\] \{(.*?)\}", styles, re.S)
    assert m, "预计用时未限宽"
    mx = re.search(r"max-width:\s*(\d+)px", m.group(1))
    assert mx and int(mx.group(1)) <= 140, f"限宽 {mx.group(1) if mx else '?'}px 仍过宽"


# ====== 4. 弹窗表单不再有空白行 (2026-10-02 用户批注) ======
#
# 批注原话「两行空闲」: 弹窗里六行字段之间多出近两行空白。
# 根因是两处留白叠加 —— 行距 var(--space-2) 加上网格自身的
# var(--space-3) 上下外边距。所以下面两条分别锁住"去掉外边距"和
# "行距收紧", 缺一条就会退回原来的高度。

def test_form_grid_has_no_outer_margin(styles):
    """网格自身不再留上下外边距 —— 弹窗内边距已经够了, 不必再加一层。"""
    m = re.search(r"\.form-grid \{(.*?)\}", styles, re.S)
    assert m, "缺少 .form-grid 定义"
    margin = re.search(r"margin:\s*([^;]+);", m.group(1))
    assert margin, ".form-grid 未显式声明 margin, 会随 UA 样式表漂移"
    assert margin.group(1).strip() in ("0", "0 0"), \
        f"网格外边距应归零, 实际 {margin.group(1).strip()} —— 空白行就来自这里"


def test_form_grid_row_gap_tightened(styles):
    """行距必须收紧到 var(--space-1)。

    上限锁在 4px: 再宽就又变回"两行空闲"。这里刻意只查行距那一项
    (grid-row-gap), 因为列距 --space-3 是标签与控件之间的呼吸位,
    跟行距不是一回事, 混在一起断言会互相干扰。
    """
    m = re.search(r"\.form-grid \{(.*?)\}", styles, re.S)
    gap = re.search(r"gap:\s*([^;]+);", m.group(1))
    assert gap, ".form-grid 未声明 gap"
    row_gap = gap.group(1).strip().split()[0]
    assert row_gap == "var(--space-1)", \
        f"行距应为 var(--space-1) 收紧空白, 实际 {row_gap}"


def test_form_field_wrapper_allows_hint_below_control(styles):
    """.form-field 必须是 grid —— 说明文字要能落在控件正下方。

    用 grid 而非 flex 是因为 flex 下 align 默认 stretch 会把说明拉高,
    两行文字的说明和一行输入框就对不齐了。
    """
    m = re.search(r"\.form-grid \.form-field \{(.*?)\}", styles, re.S)
    assert m, "缺少 .form-field 定义, 带说明的字段无法把说明放在控件下方"
    assert "display: grid" in m.group(1)
    assert "min-width: 0" in m.group(1), "缺 min-width:0, 日期框会撑破包装"


# ====== 5. 就地说明的位置 (2026-10-02 用户批注「这个位置不对」) ======

def test_deadline_hint_moved_next_to_its_field(app_js):
    """「可以不填」必须紧贴截止时间框, 不能留在弹窗最底部。

    原来它是一句挂在 .form-grid 之后的 <p class="muted">, 离所说明的
    输入框隔着三行, 读起来像在解释整个表单。
    """
    html = _manual_form_html(app_js)
    block = re.search(r'<span class="form-label">截止时间</span>(.*?)</label>', html, re.S)
    assert block, "未找到截止时间字段"
    inner = block.group(1)
    assert 'class="form-field"' in inner, "截止时间未与说明裹进同一个 form-field"
    assert 'class="form-hint"' in inner, "截止时间框旁没有就地说明"
    assert 'id="m-dl"' in inner, "截止时间输入框不在该字段内"


def test_no_stray_hint_paragraph_at_modal_bottom(app_js):
    """弹窗底部不再有游离的 <p class="muted"> 说明。

    这条与上一条互为对照: 只把说明挪走、忘了删原来那句, 弹窗底部
    会同时出现两份内容(且新的已在上方), 造成重复。
    """
    fn = re.search(r"function openManualAddModal\(\) \{(.*?)\n\}", app_js, re.S)
    assert fn, "未找到 openManualAddModal"
    body = fn.group(1)
    tail = body.split("</div>")[-1]
    assert "可以不填" not in tail, "弹窗底部仍残留游离说明, 与控件旁的说明重复"


# ====== 6. 选做字段的标签与默认值 (2026-10-02 用户批注) ======

def test_optional_field_not_mislabeled_as_type(app_js):
    """选做的标签不能叫「类型」。

    批注原话「是否选做(默认否)」: 原标签写「类型」, 但这个控件绑的是
    is_optional(选做), 与作业类型(task_type)完全是两回事 —— 名字和
    行为对不上, 家长会以为在填作业类型。标签必须直说「是否选做」。
    """
    html = _manual_form_html(app_js)
    assert '<span class="form-label">是否选做</span>' in html, "选做字段标签应直说「是否选做」"
    assert '<span class="form-label">类型</span>' not in html, \
        "「类型」是作业类型(task_type)的标签, 用在选做上属于误导"


def test_optional_default_is_explicit(app_js):
    """默认值必须在界面上写明, 而不是让家长猜未勾选是什么意思。

    只画一个空复选框时, 「空」到底是默认否、还是漏勾了, 无法分辨;
    批注要求「默认否」, 就在字段下写明。
    """
    html = _manual_form_html(app_js)
    block = re.search(r'<span class="form-label">是否选做</span>(.*?)</label>', html, re.S)
    assert block, "未找到选做字段"
    assert "默认否" in block.group(1), "选做字段未写明默认值"


def test_optional_checkbox_still_unchecked_by_default(app_js):
    """复选框仍不带 checked —— 默认否要同时体现在文案和状态上。"""
    html = _manual_form_html(app_js)
    assert re.search(r'id="m-opt" type="checkbox"\s*/>', html), \
        "选做复选框默认应未勾选"
    assert 'id="m-opt" type="checkbox" checked' not in html, \
        "选做默认应为否, 不该预勾选"

