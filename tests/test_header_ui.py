"""页头中文化 + flatpickr 日历头部布局测试 (2026-09-28).

用户截图批注了三件事:
  1. 左上角 "study-tracker v1" 改中文名, 去掉版本号
  2. 右上角 "DB ok · 模型 MiniMax-M3 · AI ✓ 可用" 改为当天日期+星期
  3. flatpickr 日历的 "九月 2026" 压住了星期表头

第 3 条是本文件的主要价值: 它曾经是一处真实的布局 bug ——
.flatpickr-current-month 是 absolute + height:34px, 而 .flatpickr-months
是纯 flex 容器没有高度; 主题里给 months 加了 padding-top 又给
current-month 加了 top, 等于整体下移 14px 而容器高度没涨,
下方的星期表头就被盖住了。这类 bug 只有把布局契约锁住才不会复发。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "app" / "static" / "vendor"

node = shutil.which("node")
jsdom_available = False
if node:
    try:
        subprocess.run([node, "-e", "require.resolve('jsdom')"], cwd=ROOT,
                       capture_output=True, timeout=30, check=True)
        jsdom_available = True
    except Exception:
        jsdom_available = False

pytestmark = pytest.mark.skipif(
    not jsdom_available, reason="需要 node 与 jsdom"
)


# ---------- 静态检查 (不需要 jsdom, 但同文件便于对照) ----------

@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def theme_css() -> str:
    return (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


def _rule(css: str, sel: str) -> str:
    """取某条规则的内容。

    必须允许行首空白: 主题里 .flatpickr-current-month 是逗号分组的
    续行(前面带缩进)。也不能命中注释 —— 注释里出现的类名后面跟的是
    空格加文字, 而不是 " {"。
    """
    esc = re.escape(sel)
    m = re.search(r"^[ \t]*" + esc + r" \{", css, re.M)
    if not m:
        return ""
    end = css.find("\n}", m.start())
    return css[m.start():end] if end != -1 else ""


# ====== 1. 左上角中文名 ======

def test_logo_is_chinese_name(index_html):
    m = re.search(r"<h1>(.*?)</h1>", index_html, re.S)
    assert m, "缺少 header h1"
    text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    assert text == "作业小助手", text


def test_logo_has_no_version(index_html):
    h1 = re.search(r"<h1>(.*?)</h1>", index_html, re.S).group(1)
    assert "v1" not in h1.lower()
    # 任何数字都可疑 —— 版本号是唯一可能的数字来源
    assert not re.search(r"\d", h1), h1


def test_logo_has_no_english_codename(index_html):
    h1 = re.search(r"<h1>(.*?)</h1>", index_html, re.S).group(1)
    assert "study-tracker" not in h1.lower()
    assert "tracker" not in h1.lower()


def test_title_is_chinese(index_html):
    title = re.search(r"<title>(.*?)</title>", index_html, re.S).group(1)
    assert "作业小助手" in title
    assert "study-tracker" not in title.lower()


def test_footer_does_not_leak_api_path(index_html):
    """页脚原本显示 API 路径, 对家长无意义且暴露技术细节。"""
    foot = re.search(r"<footer>(.*?)</footer>", index_html, re.S).group(1)
    assert "/api/health" not in foot


def _tab_label(btn_inner: str) -> str:
    """取出 Tab 按钮里的可见文字。

    2026-10-12 起按钮里多了 <svg> 图标与 <span> 包裹, 整体比对会失败;
    而这里要守的恰恰是"文字没被改坏", 所以必须先把标签剥掉, 只留汉字。
    """
    return re.sub(r"<svg.*?</svg>|<[^>]+>", "", btn_inner, flags=re.S).strip()


# ====== 1b. Tab 名称 (2026-11-01 双角色改造: 6 个 Tab -> 3 个) ======
#
# 导航精简成 3 个 Tab: 今日 / 日历 / 复盘。家长端与孩子端共用同一套
# Tab 键 (board / list / review), 只是标签不同 —— 孩子端第三个叫
# 「成绩单」, 因为"复盘"是给家长看的词。
#
# 家长专属页 (登记 / 周计划 / 设置) 从 Tab 栏里退出去, 改成:
#   - 今日页右上「＋ 登记作业」
#   - 日历页右上「周计划」
#   - 右上角头像菜单里的「设置」
# 它们在 DOM 里仍然留着一个**隐藏的深链落点**按钮 (data-tab="entry" 等,
# 直接打开 #/entry 仍能落到那一页), 但不带 .tab-ico —— 带图标的才算 Tab。
PARENT_TABS = [("board", "今日"), ("list", "日历"), ("review", "复盘")]
KID_TAB_LABELS = {"board": "今日", "list": "日历", "review": "成绩单"}
# 顶栏 Tab 用到的图标 symbol —— 三个 Tab 各一个 (2026-11-01 双角色改造后
# 导航只剩三个 Tab, 原来给"登记/周计划/设置"画的三个 symbol 已随入口
# 一起退到页面里, 不再是 Tab 图标)。
TAB_SYMBOLS = {"i-tab-today", "i-tab-calendar", "i-tab-review"}


def _nav_tabs(index_html: str) -> list:
    """只取带 .tab-ico 的那些按钮 —— 那才是 Tab。

    深链落点 (登记/周计划/设置) 也带 data-tab, 但它们是页面入口而不是
    Tab: 一个图标位都不占。
    """
    out = []
    for m in re.finditer(r'<button[^>]*data-tab="([^"]+)"[^>]*>(.*?)</button>',
                         index_html, re.S):
        if '<svg class="tab-ico"' in m.group(2):
            out.append((m.group(1), _tab_label(m.group(2))))
    return out


def test_three_tabs_in_nav(index_html):
    """导航只有三个 Tab, 且顺序固定。"""
    assert _nav_tabs(index_html) == PARENT_TABS


def test_list_tab_renamed_to_calendar(index_html):
    m = re.search(r'<button[^>]*data-tab="list"[^>]*>(.*?)</button>', index_html)
    assert m, "缺少日历 Tab"
    assert _tab_label(m.group(1)) == "日历", m.group(1)


def test_tab_labels_all_chinese(index_html):
    """三个 Tab 都不该出现英文代号或版本号。"""
    labels = [l for _, l in _nav_tabs(index_html)]
    for l in labels:
        assert not re.search(r"[A-Za-z]", l), l


def test_kid_tab_labels_defined(app_js):
    """孩子端同一套 Tab 键, 标签换成孩子的说法 (「成绩单」而不是「复盘」)。"""
    m = re.search(r"const TAB_LABELS = \{(.*?)\n\};", app_js, re.S)
    assert m, "缺少 TAB_LABELS 定义"
    block = m.group(1)
    for key, label in KID_TAB_LABELS.items():
        assert f"{key}: '{label}'" in block, f"孩子端 {key} 应为 {label}"
    # 家长端仍然用专业术语
    assert "review: '复盘'" in block


def test_parent_only_entries_are_not_tabs(index_html):
    """登记 / 周计划 / 设置 留在 DOM 里当深链落点, 但不进 Tab 栏。"""
    for tab in ("entry", "week", "settings"):
        m = re.search(r'<button[^>]*data-tab="%s"[^>]*>(.*?)</button>' % tab, index_html)
        assert m, f"缺少 {tab} 深链落点"
        assert '<svg class="tab-ico"' not in m.group(1), f"{tab} 不该再占 Tab 的图标位"
        assert "display:none" in m.group(0), f"{tab} 深链落点必须默认隐藏"


def test_parent_only_page_entries_exist(index_html):
    """家长专属页的入口: 今日页「＋ 登记作业」、日历页「周计划」、
    右上角头像菜单里的「设置」。"""
    assert 'id="btn-entry-today"' in index_html
    assert 'id="btn-week-plan"' in index_html
    assert 'id="role-menu"' in index_html
    assert "设置" in index_html


# ====== 1b. 「登记作业」核心入口 (2026-11-02) ======
#
# 用户反馈: 「右上角『登记作业』这个核心功能入口按键, 改为更加显眼的
# 操作按键」。
#
# 改之前它是 ghost —— 透明底 + 透明边框 + 浅灰字, 渲染出来就是右上角
# 一行灰字, 家长认不出那里有个能按的键。同款反馈在这个项目里出现过两次
# (2026-10-30 弹窗「取消」键、2026-11-02 日历页视图切换器), 修法都是
# 同一句: 提升为一眼能认出的实心按键。


def _entry_btn(index_html: str) -> str:
    m = re.search(r'<button[^>]*id="btn-entry-today"[^>]*>', index_html)
    assert m, "找不到今日页的「登记作业」入口"
    return m.group(0)


def test_entry_cta_is_a_solid_button_not_ghost(index_html):
    """入口必须是实心按键, 不能是 ghost。"""
    tag = _entry_btn(index_html)
    cls = re.search(r'class="([^"]*)"', tag)
    assert cls, f"入口按钮没有 class: {tag}"
    classes = cls.group(1).split()
    assert "ghost" not in classes, f"入口不该再用 ghost (透明底透明边): {tag}"
    assert "cta-entry" in classes, f"入口应带 cta-entry: {tag}"
    assert "view-parent-only" in classes, "入口仍须是家长专属 (孩子端看不到)"


def test_entry_cta_has_icon(index_html):
    """入口配个加号图形, 与 Tab 图标同一套语言 (先认形再认字)。"""
    m = re.search(r'<button[^>]*id="btn-entry-today"[^>]*>(.*?)</button>',
                  index_html, re.S)
    assert m, "找不到「登记作业」入口的内容"
    inner = m.group(1)
    assert 'href="#i-plus"' in inner, f"入口缺少加号图标: {inner}"
    assert '<symbol id="i-plus"' in index_html, "i-plus 未在 svg-defs 中定义"
    assert "登记作业" in inner, f"入口文案丢失: {inner}"


def test_entry_cta_uses_role_accent(styles_css):
    """底色走角色主操作色 (家长端=深靛蓝「教师用笔」), 不另造颜色。

    --accent + --accent-ink 的实心按钮在日历页视图切换器里已有先例;
    写死 hex 会让两端角色各走各的色板, 也和第 2 节的色板纪律冲突
    (红/蓝/绿/紫都是学科色, 不能拿来当全局按钮底色)。
    """
    rule = _rule(styles_css, ".cta-entry")
    assert rule, "缺少 .cta-entry 样式"
    assert "var(--accent)" in rule, "底色应取角色主操作色 --accent"
    assert "var(--accent-ink)" in rule, "字色应取 --accent-ink"
    assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", rule), "不该写死颜色"
    assert "var(--radius-pill)" in rule, "入口应是药丸形"


def test_entry_cta_does_not_pin_min_height(styles_css):
    """不能在这里写死 min-height。

    窄屏那组全局规则 (button { min-height: 44px }) 要把触控目标抬到
    44px; 而 .cta-entry 是 (0,1,0), 压得过裸 button (0,0,1) —— 一旦
    这里写死 min-height, 触控目标就再也抬不上去了。
    """
    rule = _rule(styles_css, ".cta-entry")
    assert "min-height" not in rule, \
        "min-height 交给全局触控目标规则, 别在 .cta-entry 里写死"


# ====== 1c. 家长专属入口的首帧防闪 (2026-11-03) ======
#
# 用户反馈: 「登记作业」的按键, 有时候刷新页面会闪现。
#
# 根因: 家长专属入口 (.view-parent-only) 的可见性**只由 JS 决定** ——
# app.js 的 applyRoleNav() 要等脚本执行完才写 style.display='none';
# 而 app.js 是 <body> 末尾的普通 <script> (289KB), 浏览器会在它下载期间
# 先画一帧已解析的 DOM。孩子端于是先看到一帧家长专属入口, 脚本一跑就消失。
#
# 「有时候」= 闪现时长取决于 app.js 的下载速度:
#   命中缓存时 ~38ms (看不出来) / 弱网冷加载时 20 秒以上 (刺眼)。
# 2026-11-02 把入口从 .ghost (透明底) 改成实心药丸键之后, 这个一直存在的
# 隐患才第一次肉眼可见。
#
# 修法: 可见性改由 CSS 按 [data-role] 决定 —— <head> 里的内联脚本在 body
# 解析**之前**就把 data-role 落到 <html> 上, 所以规则从第一帧起就生效。
#
# 这组测试钉的是「**CSS 必须自己就能藏住**」: 只靠 applyRoleNav() 的话,
# 谁把那条 JS 删了/挪晚了, 闪现就会回来, 而这类 bug 没有单测能兜住。


def _kid_hide_rule(css: str) -> str:
    """取出 [data-role="kid"] 下隐藏家长专属入口的那条规则。

    选择器是逗号分组的 (一条规则同时管 .view-parent-only 和
    .role-menu-wrap), 所以不能用上面那个只认 "SEL {" 的 _rule()。

    先剥掉注释: 文件开头的令牌说明里也出现过 [data-role="kid"],
    不剥会匹配到注释而不是规则 (注释里没有 " {", 但上面的 _rule()
    同理会踩到 .flatpickr-theme.css 的续行选择器)。
    """
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r'([^{}]*\[data-role="kid"\][^{}]*)\{', stripped):
        sel = m.group(1)
        if ".view-parent-only" in sel or ".role-menu-wrap" in sel:
            end = stripped.find("\n}", m.start())
            return stripped[m.start():end] if end != -1 else ""
    return ""


def test_parent_only_entries_hidden_by_css_not_only_by_js(styles_css):
    """孩子端必须在第一帧就藏住家长专属入口, 不能等 JS 跑完。"""
    rule = _kid_hide_rule(styles_css)
    assert rule, "缺少 [data-role=\"kid\"] 下隐藏家长专属入口的 CSS 规则"
    assert "display" in rule and "none" in rule, \
        f"该规则必须设 display:none: {rule}"
    assert ".view-parent-only" in rule, \
        f"规则必须覆盖 .view-parent-only (今日页「＋ 登记作业」等): {rule}"
    assert ".role-menu-wrap" in rule, \
        f"规则必须覆盖 .role-menu-wrap (右上角头像菜单): {rule}"


def test_kid_hide_rule_beats_the_component_display_rules(styles_css):
    """隐藏规则必须压得住组件自己的 display。

    .cta-entry / .view-switch / .role-menu-wrap 都是 (0,1,0);
    [data-role="kid"] .view-parent-only 是 (0,2,0) —— 特异度更高,
    所以同一层里谁写在前面不重要, 这条规则照样赢。
    这里把「特异度更高」这件事钉住, 免得将来有人把选择器改简单了
    (比如写成 .view-parent-only { display:none }), 那就变成**两个角色
    都不显示**。
    """
    rule = _kid_hide_rule(styles_css)
    assert rule, "缺少 [data-role=\"kid\"] 隐藏规则"
    assert re.search(r'\[data-role="kid"\]\s+\.view-parent-only', rule), \
        f"必须用 [data-role=\"kid\"] .view-parent-only 组合选择器: {rule}"


def test_head_script_sets_role_before_body_is_parsed(index_html):
    """data-role 必须在 <body> 解析之前就落到 <html> 上。

    CSS 规则要能从第一帧生效, 前提是 data-role 在首帧之前就已就位 ——
    这正是 <head> 里那段内联脚本的职责 (app.js 那时还没加载)。
    """
    head = index_html.split("<body>", 1)[0]
    assert "st.role" in head, \
        "首帧防闪脚本不在 <head> 里 —— data-role 会晚于第一帧"
    assert 'dataset.role' in head, "首帧脚本没有写 data-role"
    # 脚本必须在 body 之前
    assert index_html.index("st.role") < index_html.index("<body>"), \
        "首帧脚本位置错了: 必须排在 <body> 之前"



def test_role_switch_control_exists(index_html):
    """角色切换按钮 (家长端「切到孩子」/ 孩子端「家长」)。"""
    assert 'class="role-switch"' in index_html
    assert 'id="role-switch"' in index_html


# ====== 1c. Tab 图标 (2026-10-12) ======

def test_every_tab_has_an_icon(index_html):
    """三个 Tab 各配一个图形: 低龄用户先认形再认字。"""
    for tab, sym in [("board", "i-tab-today"), ("list", "i-tab-calendar"),
                     ("review", "i-tab-review")]:
        m = re.search(r'<button[^>]*data-tab="%s"[^>]*>(.*?)</button>' % tab, index_html)
        assert m, f"缺少 {tab} Tab"
        assert f'href="#{sym}"' in m.group(1), f"{tab} 缺少图标 {sym}"
        assert '<symbol id="%s"' % sym in index_html, f"{sym} 未在 svg-defs 中定义"


def test_tab_icons_inherit_current_color(index_html):
    """图标描边必须是 currentColor: 选中态只改 button 的 color,
    图标才能跟着变色, 不必为每个 Tab 各画一份彩色版。"""
    syms = re.findall(r'<symbol id="i-tab-[^"]+".*?</symbol>', index_html, re.S)
    ids = {re.search(r'<symbol id="([^"]+)"', s).group(1) for s in syms}
    assert ids == TAB_SYMBOLS, ids
    for s in syms:
        assert "currentColor" in s, s[:60]
        assert "#4A3728" not in s and "stroke=\"#" not in s, "图标不应写死颜色"


def test_tab_icons_hidden_from_a11y_tree(index_html):
    """图标纯装饰, 读屏只该念出文字, 不能念两遍。"""
    assert len(re.findall(r'<svg class="tab-ico" aria-hidden="true">', index_html)) == 3
    # 文字必须包在 span 里, 否则图标与汉字会挤在一行
    assert len(re.findall(r'<span>[^<]+</span>', index_html)) >= 3


# ====== 1d. 双角色接线 (2026-11-01) ======

def test_role_applied_before_first_paint(index_html):
    """data-role 必须在 <head> 里就落到 <html> 上, 否则首帧会闪一帧旧皮肤。

    同一段内联脚本顺手把护眼模式也落上 —— 两个都是"皮肤开关"。
    """
    head = index_html.split("</head>")[0]
    assert "document.documentElement" in head, "角色必须在 head 里就地设置"
    assert "dataset.role" in head
    assert "st.role" in head
    assert "st.eyecare" in head


def test_role_fonts_are_system_fonts(index_html, styles_css):
    """孩子端楷体 / 家长端宋体, 但**不引 CDN**。

    本项目是本地部署, 要求离线可用 —— tests/test_datepicker.py 的
    test_no_cdn_dependency 已经钉住"页面不得直接引用外部 CDN"。
    楷体/宋体在 Windows(KaiTi/SimSun)与 macOS(Kaiti SC/Songti SC)
    都自带, 用系统字体既保住了角色区分, 又不引入网络依赖。
    """
    for bad in ("fonts.googleapis.com", "fonts.gstatic.com", "LXGW+WenKai+TC"):
        assert bad not in index_html, f"不应依赖外部字体 CDN: {bad}"
    # 字体栈写在 styles.css 里, 两个角色的 display 字体不能是同一个
    assert "KaiTi" in styles_css or "Kaiti" in styles_css, "孩子端 display 字体应为楷体"
    assert "SimSun" in styles_css or "Songti" in styles_css, "家长端 display 字体应为宋体"


def test_privacy_note_moved_to_settings(index_html):
    """隐私说明从页脚搬进设置页 (规范 §8): 首页不再常驻这行字。"""
    foot = re.search(r"<footer>(.*?)</footer>", index_html, re.S).group(1)
    assert "不上传到任何服务器" not in foot, "首页不该再常驻隐私声明"
    settings = re.search(r'<section id="view-settings".*?</section>', index_html, re.S).group(0)
    assert "不上传到任何服务器" in settings, "隐私说明应移进设置页"
    assert 'id="api-info"' in settings, "#api-info 这个 id 要保留"


def test_footer_element_still_there(index_html):
    """footer 元素本身必须保留: 移动端 CSS 依赖它给手势条留出空间。"""
    assert re.search(r"<footer>", index_html)


def test_view_id_unchanged(index_html):
    """用户可见名改了, 但内部 id 必须保持 view-list。

    switchTab('list')、CSS 与测试都按这个 id 定位; 改它牵连多处而无收益。
    """
    assert 'id="view-list"' in index_html
    assert 'data-tab="list"' in index_html


def test_no_stale_list_wording_in_ui(index_html, app_js):
    """界面上不应再出现旧的「列表」叫法。

    只查用户可见部分: 内部 id(view-list / data-tab="list")保留不动。
    """
    for src in (index_html, app_js):
        # 去掉 HTML 注释与 JS 行注释后再查
        s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
        s = "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))
        # 保留中文引号内的用法, 只找裸的「列表」/列表页/列表」
        assert "「列表」" not in s, "空状态引导文案仍指向旧 Tab 名"
        assert "列表页" not in s, "注释或文案仍称列表页"


# ====== 2. 右上角日期 + 星期 ======

def test_health_bar_no_longer_shows_dev_info(app_js):
    code = "\n".join(l for l in app_js.splitlines() if not l.strip().startswith("//"))
    assert "DB ok" not in code, "不应再显示 DB 状态"
    assert "模型 ${" not in code, "不应再显示模型名"


def test_render_today_exists(app_js):
    assert "function renderToday()" in app_js
    assert "星期" in app_js


def test_health_bar_uses_today_label(app_js):
    assert 'class="today-label"' in app_js
    assert "health-warn" in app_js


def test_today_reuses_todaystr(app_js):
    """不要自己再拼一遍日期。

    todayStr() 已经处理了 UTC+8 凌晨会退到前一天的问题,
    两套实现迟早漂移。
    """
    block = app_js.split("function renderToday()")[1].split("\n}")[0]
    assert "todayStr()" in block, "renderToday 应复用 todayStr()"
    assert "getFullYear" not in block, "不应重复实现本地日期换算"


def test_date_shown_even_when_health_fails(app_js):
    """接口挂了也要显示"今天是几号" —— 跟后端通不通无关。"""
    assert "连接异常" in app_js
    # 必须定位到 loadHealth 内部的 catch: 文件里还有多处 catch,
    # 取 [-1] 会拿到最后一个(导出失败那个), 断言就失去意义了。
    fn = app_js.split("async function loadHealth()")[1]
    catch = fn.split("} catch (e) {")[1]
    assert "today-label" in catch, "catch 分支也必须渲染日期"
    assert "连接异常" in catch
    assert "e.message" not in catch, "不应把原始错误抛给家长"


def test_ai_warning_only_when_unavailable(app_js):
    assert "AI 未配置" in app_js
    # 可用时不该出现警告文案拼接
    assert "aiOk ? ''" in app_js


def test_health_bar_styles_present():
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    assert "#health-bar .today-label" in css
    assert "#health-bar .health-warn" in css
    assert "font-variant-numeric: tabular-nums" in css


def test_health_bar_color_stable(app_js):
    """AI 状态变化不应让日期颜色闪变。"""
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    ok_true = re.search(r"#health-bar\.ok-true[^{]*\{([^}]*)\}", css)
    ok_false = re.search(r"#health-bar\.ok-false[^{]*\{([^}]*)\}", css)
    if ok_true and ok_false:
        assert ok_true.group(1).strip() == ok_false.group(1).strip(), \
            "两种状态的日期色应一致, 否则 AI 恢复时日期会闪一下"


# ====== 3. flatpickr 日历头部布局 ======

def test_months_has_explicit_height(theme_css):
    """.flatpickr-months 是纯 flex 容器, 库没给高度。

    不显式给, 上面的 absolute 标题就会压住下面的星期表头。
    """
    block = _rule(theme_css, ".flatpickr-months")
    m = re.search(r"height:\s*(\d+)px", block)
    assert m, "months 必须显式声明 height"
    assert int(m.group(1)) >= 34, "至少要托住库给 current-month 的 34px 槽位"


def test_current_month_not_offset_down(theme_css):
    """回归: 曾经给了 top:6px, 叠加 months 的 padding 后整体下移 14px,
    容器高度却没涨, 星期表头被盖住。"""
    block = _rule(theme_css, ".flatpickr-current-month")
    assert "top" in block, "应显式声明 top"
    m = re.search(r"(?:^|\s)top:\s*(-?\d+)(px)?\s*;", block)
    assert m, "top 声明格式异常"
    assert int(m.group(1)) == 0, f"top 应为 0 而非 {m.group(1)}"


def test_current_month_has_height(theme_css):
    block = _rule(theme_css, ".flatpickr-current-month")
    assert re.search(r"height:\s*34px", block), "应与库约定的 34px 槽位一致"


def test_year_input_no_underline(theme_css):
    """库默认给年份输入加下划线, 和月份并排显得杂乱。"""
    block = _rule(theme_css, ".flatpickr-current-month input.cur-year")
    assert "border-bottom: none" in block


def test_year_input_fixed_width(theme_css):
    """不定宽的年份输入在切月时会左右跳动。"""
    block = _rule(theme_css, ".flatpickr-current-month input.cur-year")
    assert re.search(r"width:\s*[\d.]+em", block)


def test_calendar_z_index(theme_css):
    """弹层会盖到录入区的 textarea, 必须显式抬层级。"""
    assert re.search(r"\.flatpickr-calendar \{[\s\S]{0,500}?z-index:\s*\d+", theme_css)


def test_theme_note_allows_layout_fixes():
    """文件头注释原写"不动布局", 但我们确实修过布局。

    注释与代码不符会误导下一个改这里的人, 故锁住这条说明。
    """
    head = (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8").split("*/")[0]
    assert "不动布局" not in head
    assert "DOM 结构" in head, "仍应保留不改 DOM 结构的约束"


# ====== 4. 运行时行为 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, jsPath, fpPath, zhPath] = process.argv.slice(2);
const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
let health = { ai_model: 'MiniMax-M3', ai_reachable: true };
let failHealth = false;
w.fetch = async (u) => {
  if (String(u).includes('/health')) {
    if (failHealth) throw new Error('boom');
    return { ok: true, status: 200, text: async () => JSON.stringify(health) };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify({}) };
};
w.confirm = () => true;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(jsPath, 'utf8') + '\n;globalThis.__h = () => loadHealth();');

(async () => {
  const doc = w.document;
  const out = {};
  const now = new Date();
  const week = '日一二三四五六'[now.getDay()];
  out.week = week;
  out.h1 = doc.querySelector('header h1').textContent.trim();
  out.title = doc.title;
  out.footer = doc.querySelector('footer').textContent.trim();

  await w.__h();
  await new Promise(r => setTimeout(r, 120));
  const bar = doc.getElementById('health-bar');
  out.okText = bar.textContent;
  out.hasTodayLabel = !!bar.querySelector('.today-label');

  health = { ai_model: '-', ai_reachable: false };
  await w.__h();
  await new Promise(r => setTimeout(r, 120));
  out.aiDownText = bar.textContent;

  health = { ai_model: 'x', ai_reachable: true };
  failHealth = true;
  await w.__h();
  await new Promise(r => setTimeout(r, 120));
  out.errText = bar.textContent;

  // 日历结构
  const entry = doc.getElementById('entry-date');
  out.hasInstance = !!entry._fpInstance;
  if (out.hasInstance) {
    entry._fpInstance.open();
    await new Promise(r => setTimeout(r, 80));
    const cal = doc.querySelector('.flatpickr-calendar');
    out.calOpen = !!(cal && cal.classList.contains('open'));
    out.weekdays = [...cal.querySelectorAll('.flatpickr-weekday')].map(x => x.textContent.trim());
    out.hasMonths = !!cal.querySelector('.flatpickr-months');
    out.hasCurrentMonth = !!cal.querySelector('.flatpickr-current-month');
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    import json
    harness = ROOT / "tmp" / "_hdr_harness.js"
    harness.parent.mkdir(exist_ok=True)
    harness.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(harness),
             str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(VENDOR / "flatpickr.min.js"),
             str(VENDOR / "flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90,
        )
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1200:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if harness.exists():
            harness.unlink()


def test_live_logo(live):
    assert live["h1"] == "作业小助手"
    assert "study-tracker" not in live["title"].lower()


def test_live_footer(live):
    assert "/api/health" not in live["footer"]


def test_live_health_shows_date_and_weekday(live):
    assert f"星期{live['week']}" in live["okText"]
    assert live["hasTodayLabel"] is True
    assert "DB ok" not in live["okText"]
    assert "MiniMax" not in live["okText"]


def test_live_health_silent_when_ai_ok(live):
    assert "AI 未配置" not in live["okText"]


def test_live_health_warns_when_ai_down(live):
    assert f"星期{live['week']}" in live["aiDownText"]
    assert "AI 未配置" in live["aiDownText"]


def test_live_health_error_keeps_date(live):
    assert f"星期{live['week']}" in live["errText"]
    assert "连接异常" in live["errText"]
    assert "boom" not in live["errText"], "不应把原始错误抛给用户"


def test_live_calendar_structure(live):
    assert live["hasInstance"] is True
    assert live["calOpen"] is True
    assert live["hasMonths"] is True
    assert live["hasCurrentMonth"] is True
    assert live["weekdays"] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
