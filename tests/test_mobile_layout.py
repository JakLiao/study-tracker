"""移动端 ergonomics 回归测试 (2026-09-29).

这一批断言全部来自 375px 真实测量, 不是审美偏好:

  基线 (改之前)                     改之后
  -------------------------------    ------------------------------
  40 个可点元素 < 44px               0 个
  次操作按钮 22px 高                  44px
  "原文"链接 26x19                   44x44
  输入框字号 14px / 13.3px           全部 >= 16px
  预览表 640px 塞进 334px 视口        转成卡片, 0 横向滚动
  header 占 88px                     48px
  导航在屏幕顶端(拇指够不到)          固定在底部

两条不能被"顺手优化"掉的约束:
  1. 桌面端的紧凑密度是有意为之 —— test_row_actions 明确断言 .status-btn
     基础档 22 <= h < 34。所以触控目标只能放在窄屏媒体查询里, 不能去动
     基础规则, 否则会把桌面版的信息密度一起毁掉。
  2. 手机横屏时宽度会涨到 667~932px, 只写 max-width 断点会漏掉横屏。
     断点必须带"(或 高度 <=500px)"这一路。

布局类断言用 CSS 文本 + jsdom 静态检查完成: jsdom 不做布局, 量不出
像素, 但能锁住"规则是否存在、特异度是否够得着、data-label 是否齐全"。
真正量像素的那一轮在浏览器里用 375px iframe 做的, 结论已写进注释。
"""
from __future__ import annotations

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
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def _media_blocks(css: str):
    """逐个切出顶层 @media ... { ... } 块。"""
    out, i = [], 0
    while True:
        m = re.compile(r"@media[^{]*\{").search(css, i)
        if not m:
            return out
        depth, k = 1, m.end()
        while depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        out.append(css[m.start():k])
        i = k


def _phone_block(css: str, anchor: str) -> str:
    """返回"包含 anchor 这段唯一文本"的 @media 块。

    不能拿 anchor 直接去 css.index 再回溯 @media: 像 "min-height: 44px"
    这种字面量在文件里多处出现, 回溯会落到毫不相干的
    prefers-reduced-motion 块上去。改成遍历所有块找包含关系。
    """
    css = _strip_comments(css)
    for b in _media_blocks(css):
        if anchor in b:
            return b
    raise AssertionError(f"没有任何 @media 块包含 {anchor!r}")


# ====== viewport / 安全区 ======

def test_viewport_fit_cover(index_html):
    """没有 viewport-fit=cover, env(safe-area-inset-*) 在刘海屏上恒为 0,
    后面写的安全区padding 全部白写。"""
    m = re.search(r'<meta name="viewport" content="([^"]+)"', index_html)
    assert m, "缺少 viewport meta"
    assert "width=device-width" in m.group(1)
    assert "viewport-fit=cover" in m.group(1)


def test_zoom_is_not_disabled(index_html):
    """user-scalable=no / maximum-scale=1 会锁死缩放, 属无障碍红线。"""
    m = re.search(r'<meta name="viewport" content="([^"]+)"', index_html)
    assert "user-scalable=no" not in m.group(1)
    assert "maximum-scale=1" not in m.group(1)


def test_header_respects_top_notch(styles):
    css = _strip_comments(styles)
    assert re.search(r"header\s*\{[^}]*padding-top:[^;]*env\(safe-area-inset-top", css)


def test_footer_respects_bottom_gesture(styles):
    css = _strip_comments(styles)
    assert re.search(r"footer\s*\{[^}]*padding-bottom:[^;]*env\(safe-area-inset-bottom", css)


def test_modal_uses_dvh_with_vh_fallback(styles):
    """85vh 在移动端含地址栏, 弹窗底部按钮会被地址栏压住。
    dvh 必须写在 vh 之后 —— 老浏览器不认 dvh 时沿用 vh。"""
    block = re.search(r"\.modal-body\s*\{([^}]*)\}", _strip_comments(styles)).group(1)
    vh = block.index("max-height: 85vh")
    dvh = block.index("max-height: 85dvh")
    assert vh < dvh, "vh 兜底必须在前, dvh 在后"


# ====== 触控目标 ======

def test_phone_breakpoint_catches_landscape(styles):
    """手机横屏宽度会到 667~932px, 只写 max-width 会整段漏掉。"""
    css = _strip_comments(styles)
    assert "(max-width: 767px), (max-height: 500px)" in css, \
        "窄屏断点必须同时覆盖宽度与高度(横屏手机靠高度判定)"


def test_touch_targets_block_has_44px(styles):
    block = _phone_block(styles, "min-width: 44px")
    assert ".status-btn" in block
    assert "min-height: 44px" in block


def test_status_btn_variants_are_enumerated(styles):
    """只写 .status-btn 压不住 .status-btn.done(0,2,0) —— 后者会保留 34px。"""
    block = _phone_block(styles, "min-width: 44px")
    for cls in (".status-btn.done", ".status-btn.skipped", ".status-btn.redo",
                ".status-btn.undo", ".status-btn.opt-toggle", ".status-btn.danger"):
        assert cls in block, f"{cls} 未纳入触控目标规则"


def test_nav_tabs_not_clobbered_by_generic_rule(styles):
    """第 19 节给主导航 52px, 触控规则里若再写一遍 nav#tabs button,
    同特异度下会把 52 压回 44。"""
    block = _phone_block(styles, "min-width: 44px")
    assert "nav#tabs button" not in block, "主导航高度应由第 19 节独占"


def test_source_link_gets_both_dimensions(styles):
    """"原文"是裸 span, 只给 min-height 的话宽度仍只有 34px。"""
    block = _phone_block(styles, ".assignment-row .source-link")
    assert "min-height: 44px" in block
    assert "min-width: 44px" in block


def test_desktop_density_untouched(styles):
    """回归护栏: 桌面紧凑档是设计决定, 22 <= h < 34, 窄屏规则不许改它。"""
    base = styles.split(".status-btn {")[1].split("\n}")[0]
    h = int(re.search(r"min-height:\s*(\d+)px", base).group(1))
    assert 22 <= h < 34


def test_touch_spacing(styles):
    """相邻操作留 8px, 避免误触"完成/删除"。"""
    block = _phone_block(styles, "min-width: 44px")
    assert re.search(r"\.row-actions\s*\{\s*gap:\s*var\(--space-2\)", block)


# ====== 输入框字号 ======

def test_inputs_are_16px_on_phone(styles):
    """iOS Safari 对 <16px 输入框聚焦时自动放大且不还原, 16px 是唯一解。"""
    block = _phone_block(styles, ".setting-card .save-btn { min-height: 44px; }")
    assert "font-size: 16px" in block


def test_input_rule_matches_base_selectors(styles):
    """基础规则是 input[type="text"] (0,1,1); 窄屏只写裸 input (0,0,1)
    会被压过去, 等于没改。选择器必须逐条对齐。"""
    block = _phone_block(styles, ".setting-card .save-btn { min-height: 44px; }")
    for sel in ('input[type="text"]', 'input[type="number"]',
                'input[type="date"]', 'input[type="time"]', "select", "textarea"):
        assert sel in block, f"窄屏输入规则漏了 {sel}"


def test_setting_input_is_covered(styles):
    """设置页 .setting-input 自带 14px 字号(0,1,0), 会压过裸 input。"""
    block = _phone_block(styles, ".setting-card .save-btn { min-height: 44px; }")
    assert ".setting-input" in block


def test_save_button_is_covered(styles):
    """.setting-card .save-btn 是 (0,2,0), 必须带完整前缀才压得住 34px。"""
    block = _phone_block(styles, ".setting-card .save-btn { min-height: 44px; }")
    assert ".setting-card .save-btn" in block, "写成 .save-btn 会被基础规则压回去"


def test_checkbox_is_not_stretched(styles):
    """原生 checkbox 撑到 44px 高会被画成怪方块, 单独给 24px。"""
    block = _phone_block(styles, ".setting-card .save-btn { min-height: 44px; }")
    cb = re.search(r'input\[type="checkbox"\]\s*\{([^}]*)\}', block)
    assert cb, "缺少 checkbox 的窄屏尺寸规则"
    assert "24px" in cb.group(1)


# ====== 底部导航 ======

def test_nav_is_actually_fixed_on_phone(styles):
    """之前叫"底部 Tab Bar"却只是 header 里换行排列的一行, main 与 toast
    为它预留的 56px 一直空着。必须真的 fixed 到底部。"""
    block = _phone_block(styles, "box-shadow: 0 -2px 12px rgba(15, 20, 25, 0.04)")
    assert re.search(r"nav#tabs\s*\{[^}]*position:\s*fixed", block)
    assert re.search(r"nav#tabs\s*\{[^}]*bottom:\s*0", block)


def test_nav_respects_home_indicator(styles):
    block = _phone_block(styles, "box-shadow: 0 -2px 12px rgba(15, 20, 25, 0.04)")
    assert "env(safe-area-inset-bottom" in block


def test_main_reserves_room_for_fixed_nav(styles):
    """固定底栏浮在内容之上, main 必须留出等高内边距。"""
    block = _phone_block(styles, "box-shadow: 0 -2px 12px rgba(15, 20, 25, 0.04)")
    assert re.search(r"main\s*\{[^}]*padding-bottom:\s*calc\(", block)


def test_nav_touch_target_is_generous(styles):
    block = _phone_block(styles, "box-shadow: 0 -2px 12px rgba(15, 20, 25, 0.04)")
    assert re.search(r"nav#tabs button\s*\{[^}]*min-height:\s*52px", block)


def test_header_stops_wrapping_on_phone(styles):
    """导航脱离文档流后 header 只剩标题一行, 不该再换行占两行。"""
    css = _strip_comments(styles)
    assert "order: 3" in css


# ====== 预览表 -> 卡片 ======

def test_preview_table_becomes_cards(styles):
    block = _phone_block(styles, ".preview-table { min-width: 0")
    assert "display: block" in block
    assert re.search(r"\.preview-table thead\s*\{\s*display:\s*none", block)
    assert re.search(r"\.preview-table td\s*\{[^}]*display:\s*grid", block)


def test_preview_table_width_reset_has_enough_specificity(styles):
    """回归: 基础规则 .preview-table td:nth-child(8){width:92px} 是 (0,2,1)。
    复位宽度必须也用 :nth-child(n), 否则单元格仍卡在 92px, 原文变成
    "一列一个字", 单张卡片能撑到 774px。"""
    block = _phone_block(styles, ".preview-table { min-width: 0")
    assert re.search(r"\.preview-table td:nth-child\(n\)\s*\{\s*width:\s*auto", block)


def test_preview_table_does_not_clobber_grid(styles):
    """复位宽度的那条若顺手写了 display:block, (0,2,1) 会反过来压掉
    (0,1,1) 的 display:grid, 标签和输入框退回上下堆叠, 卡片高度翻倍。"""
    block = _phone_block(styles, ".preview-table { min-width: 0")
    reset = re.search(r"\.preview-table td:nth-child\(n\)\s*\{([^}]*)\}", block)
    assert reset and "display" not in reset.group(1), \
        "宽度复位规则里不应出现 display"


def test_preview_table_unwraps_truncated_cells(styles):
    """桌面端为压行高给 td 强制 nowrap+省略号, 卡片里必须放开:
    任务名常有二三十字, 截断了家长没法核对。"""
    block = _phone_block(styles, ".preview-table { min-width: 0")
    td = re.search(r"\.preview-table td\s*\{([^}]*)\}", block)
    assert "white-space: normal" in td.group(1)
    assert "text-overflow: clip" in td.group(1)
    assert re.search(r"\.preview-table \.col-src small\s*\{[^}]*white-space:\s*normal", block)


def test_preview_container_stops_scrolling(styles):
    block = _phone_block(styles, ".preview-table { min-width: 0")
    assert re.search(r"#parse-result\s*\{\s*overflow-x:\s*visible", block)


def test_every_preview_cell_has_a_label(app_js):
    """卡片布局没有表头, 每格必须自带 data-label, 否则只剩裸输入框。

    必须把范围切在 renderPreviewTable 里 —— 复盘偏差表(26g)也用
    data-label, 直接全文数 <td> 会把两张表的格子混在一起数。
    """
    code = "\n".join(l for l in app_js.splitlines() if not l.strip().startswith("//"))
    block = code.split("function renderPreviewTable(")[1].split("function ")[0]
    tds = re.findall(r"<td([^>]*)>", block)
    assert len(tds) == 9, f"预览表应恰好 9 个单元格, 找到 {len(tds)}"
    labelled = [t for t in tds if "data-label" in t]
    assert len(labelled) == 8, "除删除列外, 每格都要有 data-label"
    assert 'data-label="任务"' in block
    assert 'data-label="归属日"' in block


def test_del_cell_label_suppressed(styles):
    """删除键自带 aria-label, 再加一列"操作"标签纯属噪音。"""
    block = _phone_block(styles, ".preview-table { min-width: 0")
    assert re.search(r"\.preview-table td\.col-del::before\s*\{\s*content:\s*''", block)


# ====== 触屏手感 ======

def test_tap_highlight_and_touch_action(styles):
    """iOS 点击时那层灰闪会让"点了没反应"更明显; touch-action 去掉
    ~300ms 双击缩放等待。"""
    css = _strip_comments(styles)
    assert "-webkit-tap-highlight-color: transparent" in css
    assert re.search(r"touch-action:\s*manipulation", css)


def test_buttons_are_not_text_selectable(styles):
    """长按按钮弹"拷贝/查找"菜单会打断操作, 且按钮本不该能被选中。"""
    css = _strip_comments(styles)
    assert "-webkit-touch-callout: none" in css
    assert re.search(r"user-select:\s*none", css)


def test_reduced_motion_covers_transitions(styles):
    """已为花瓣动画做过一份, 这里补上过渡/阴影类, 避免仍有元素在动。"""
    css = _strip_comments(styles)
    for m in re.finditer(r"@media \(prefers-reduced-motion: reduce\)\s*\{", css):
        start = m.end()
        depth, k = 1, start
        while depth:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
            k += 1
        block = css[start:k]
        if "transition-duration" in block and "button" in block:
            return
    pytest.fail("缺少覆盖 button 过渡的 prefers-reduced-motion 规则")


def test_landscape_compacts_chrome(styles):
    """375px 高的横屏上, header(88px)+底栏(52px) 曾吃掉三成高度。"""
    css = _strip_comments(styles)
    assert re.search(r"@media \(max-height: 500px\)\s*\{", css)


# ====== 复盘偏差表 (截图复核后补) ======
# 375px 截图里这一页曾是最惨的: table 只有 width:100%, 7 列无列宽约束,
# 浏览器把"建议"那列压到约 30px, 整段中文变成"实/际/比/预/估/多/6/分/钟…"
# 一行一个字, 单行撑到 250px。与 26c 是同一类病, 治法也相同。

def test_weekly_table_becomes_cards(styles):
    block = _phone_block(styles, ".weekly-table { display: block; }")
    assert re.search(r"\.weekly-table thead\s*\{\s*display:\s*none", block)
    assert re.search(r"\.weekly-table td\s*\{[^}]*display:\s*grid", block)
    assert 'content: attr(data-label)' in block


def test_weekly_advice_spans_full_width(styles):
    """"建议"是一段话, 不是个值 —— 必须整行, 否则又被压成窄条。"""
    block = _phone_block(styles, ".weekly-table { display: block; }")
    adv = re.search(r"\.weekly-table td\.col-advice\s*\{([^}]*)\}", block)
    assert adv, "缺少 col-advice 的整行规则"
    assert "grid-column: 1 / -1" in adv.group(1)
    assert "display: block" in adv.group(1)
    # 它自带标签文案, 再加一列"建议:"纯属重复
    assert re.search(r"td\.col-advice::before\s*\{\s*content:\s*''", block)


def test_weekly_subject_tag_not_stretched(styles):
    """网格项默认行内轴 stretch, 科目徽章会被拉成横贯整行的色带
    (截图里"语文"两个字占了 311px)。"""
    block = _phone_block(styles, ".weekly-table { display: block; }")
    td = re.search(r"\.weekly-table td\s*\{([^}]*)\}", block)
    assert "justify-items: start" in td.group(1)


def test_weekly_cells_have_labels(app_js):
    code = "\n".join(l for l in app_js.splitlines() if not l.strip().startswith("//"))
    block = code.split('class="weekly-table"')[1].split("r.top_overestimate")[0]
    assert block.count("data-label=") == 7, "偏差表 7 列都要有 data-label"
    assert 'class="col-advice"' in block
    # 表头必须是真 thead, 否则窄屏没法整体收起来
    assert "<thead>" in block and "</thead>" in block
    assert "<tbody>" in block


# ====== 设置页字典编辑器 (截图复核后补) ======

def test_settings_kv_has_no_horizontal_overflow(styles):
    """键/值各一个 <input>, 固有宽度约 170px, 两个就超过 344px 可用宽度,
    整页被迫横向滚动 —— 手机上最糟的一类问题。"""
    block = _phone_block(styles, ".settings-kv-table { table-layout: fixed; width: 100%; }")
    assert re.search(r"\.settings-kv-table input\s*\{[^}]*width:\s*100%", block)
    assert re.search(r"\.settings-kv-table input\s*\{[^}]*min-width:\s*0", block)


def test_settings_kv_delete_button_is_tappable(styles):
    block = _phone_block(styles, ".settings-kv-table { table-layout: fixed; width: 100%; }")
    assert re.search(r"\.kv-del\s*\{[^}]*min-height:\s*44px", block)


# ====== 预览卡片的红字提示 (截图复核后补) ======

def test_need_date_hint_spans_card(styles):
    """挤在标签右边窄列里会被折成三行, 比不提示更抢眼。"""
    block = _phone_block(styles, ".preview-table { min-width: 0")
    hint = re.search(r"\.preview-table \.need-date-hint\s*\{([^}]*)\}", block)
    assert hint, "缺少 need-date-hint 的整行规则"
    assert "grid-column: 1 / -1" in hint.group(1)
