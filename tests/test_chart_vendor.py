"""ECharts 的引入方式 (2026-11-02)

钉住三件事:
  1. **本地 vendor, 不引 CDN** —— 项目有"离线可用"的硬红线
     (docs/08 §2.5), 部署环境可能完全没有外网;
  2. **加载顺序** —— echarts → charts.js → dual.js → app.js。
     charts.js 排在 app.js 之前是必须的: app.js 末尾的 switchTab() 在加载期
     就会触发 loadReview(), 那时图表层必须已经在场;
  3. **charts.js 自己不引外网** —— 它是本项目的封装层, 不能偷偷联网。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "app/static/vendor"


def _html() -> str:
    return (ROOT / "app/static/index.html").read_text(encoding="utf-8")


def _charts_js() -> str:
    return (ROOT / "app/static/charts.js").read_text(encoding="utf-8")


# ====== 1. 库文件在位 ======

def test_echarts_is_vendored_locally():
    """ECharts 必须放在 app/static/vendor/ 下, 而不是从网上拉。"""
    f = VENDOR / "echarts.min.js"
    assert f.exists(), f"缺少 {f}"
    size = f.stat().st_size
    assert size > 500_000, f"ECharts 主文件只有 {size} 字节, 可能是下错文件"
    # 认得出是 ECharts 本体
    head = f.read_text(encoding="utf-8", errors="ignore")[:4000]
    assert "echarts" in head.lower()


def test_vendor_readme_documents_echarts():
    """vendor/README.md 要写明版本 / 来源 / 许可 —— 升级时照着做。"""
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    assert "ECharts" in readme
    assert "5.5.1" in readme
    assert "Apache-2.0" in readme
    assert "echarts.min.js" in readme
    # 升级步骤要给出可用的下载源
    assert "unpkg.com/echarts" in readme


# ====== 2. 页面不引任何 CDN ======

def test_page_has_no_cdn_reference():
    """扩展 test_datepicker.py::test_no_cdn_dependency 的覆盖面到图表库。

    页面上出现任何一个外部 CDN, 部署环境没外网时复盘页就会**整块空白**
    (而且是静默的 —— 页面其它部分照样能用, 家长只会以为"这个月没数据")。
    """
    html = _html()
    for bad in ("cdn.jsdelivr", "unpkg.com", "cdnjs.", "googleapis", "esm.sh", "skypack"):
        assert bad not in html, f"不应依赖外部 CDN: {bad}"


def test_charts_js_does_not_reach_out_to_network():
    """charts.js 里不许有 http(s) 引用。"""
    js = _charts_js()
    urls = re.findall(r"https?://[^\s'\"`)]+", js)
    assert not urls, f"charts.js 不该引用外部地址: {urls}"


def test_echarts_is_loaded_from_local_static_path():
    html = _html()
    assert "/static/vendor/echarts.min.js" in html
    # 必须是 src 标签, 不能是 import
    assert re.search(r'<script[^>]+src="/static/vendor/echarts\.min\.js"', html)


# ====== 3. 加载顺序 ======

def test_script_load_order():
    """echarts → charts.js → dual.js → app.js。

    顺序错了会有两种后果:
      - echarts 在 charts.js 之后 -> charts.js 拿不到 echarts 全局;
      - charts.js 在 app.js 之后 -> app.js 加载期就调 loadReview() 建图,
        那时 charts.js 还没执行, 页面直接崩。
    """
    html = _html()
    idx = lambda needle: html.index(needle)
    i_echarts = idx("/static/vendor/echarts.min.js")
    i_charts = idx("/static/charts.js")
    i_dual = idx("/static/dual.js")
    i_app = idx("/static/app.js")
    assert i_echarts < i_charts, "echarts 必须先于 charts.js"
    assert i_charts < i_dual, "charts.js 必须先于 dual.js"
    assert i_charts < i_app, "charts.js 必须先于 app.js"
    assert i_dual < i_app, "dual.js 必须先于 app.js (既有约束)"


def test_charts_js_has_no_side_effects_at_load_time():
    """charts.js 顶层只能有声明, 不能一加载就去碰 app.js 的函数。

    charts.js 排在 app.js **之前**, 顶层若调用 app.js 的函数必然 ReferenceError,
    整个页面白屏。
    """
    js = _charts_js()
    # 顶层 (缩进为 0) 的语句里不能出现对 app.js 全局函数的调用
    top_level_calls = re.findall(r"^(?!function |const |var |let |if \(typeof globalThis|  |\}|\*|/\*|//)[^\n]*\b(subjectColor|orderedSubjects|sharePercents|renderTimeChart|renderSubjectShare)\s*\(",
                                  js, re.M)
    assert not top_level_calls, f"charts.js 顶层不该调用 app.js 的函数: {top_level_calls}"


def test_charts_js_is_not_strict_mode():
    """charts.js **不能**加 'use strict'。

    tests/ 里的 jsdom harness 用 window.eval() 加载它 —— 严格模式下 eval 里的
    函数声明不会泄漏到外层作用域, app.js 随后调用 buildDailyTimeNote() 时
    会 ReferenceError, 复盘页整块白屏 (实测踩过)。
    """
    js = _charts_js()
    head = js[:600]
    assert "'use strict'" not in head and '"use strict"' not in head, \
        "charts.js 不能用 'use strict' —— jsdom 的 eval 场景会丢函数声明"
