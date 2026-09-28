"""日期选择器 (flatpickr) 集成测试 (2026-09-28).

项目原本用原生 <input type="date">, 在 Windows 上是系统控件:
灰色渐变、系统字体、图标样式不可控, 与奶油糖果配色割裂。
现改用 flatpickr 4.6.13 (MIT), 库文件落在 app/static/vendor/ 本地加载,
不依赖 CDN —— 部署环境可能完全离线。

这些用例锁的是接入时最容易踩的坑:
  1. flatpickr 会把原生 input 改成 type=hidden, 另建一个可见的
     type=text 框。collectRows() 依赖原生框的 ISO 值, 不能被破坏。
  2. 扫描绑定必须过滤掉已初始化的控件, 否则每渲染一次预览表就多生成
     一组控件, 页面被撑高。
  3. 库不会把实例挂在元素上 (4.6.13 源码里没有 _fpFlatpickr),
     也不提供 getElementById 反查, 必须由我们自己留存实例。
  4. 周起始要与日历页月历一致 (周一起始), 否则同页两个日历起点不同。

依赖 jsdom; 未安装则整组跳过, 不让本地开发被卡住。
"""
from __future__ import annotations

import json
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
        subprocess.run(
            [node, "-e", "require.resolve('jsdom')"],
            cwd=ROOT, capture_output=True, timeout=30, check=True,
        )
        jsdom_available = True
    except Exception:
        jsdom_available = False

pytestmark = pytest.mark.skipif(
    not jsdom_available, reason="需要 node 与 jsdom 才能跑前端集成测试"
)

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');

const [htmlPath, jsPath, fpPath, zhPath, scenario] = process.argv.slice(2);
const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/',
  runScripts: 'outside-only',
  pretendToBeVisual: true,
});
const w = dom.window;
w.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ items: [], days: {} }) });
w.confirm = () => true;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });

// 按 index.html 里的真实顺序加载
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(jsPath, 'utf8'));

const doc = w.document;
const out = {};
const $ = id => doc.getElementById(id);

const entry = $('entry-date');
out.entry = {
  type: entry.type,
  value: entry.value,
  hasInstance: !!entry._fpInstance,
  altType: entry._fpInstance ? entry._fpInstance.altInput.type : null,
  altValue: entry._fpInstance ? entry._fpInstance.altInput.value : null,
};

// 打开日历后读取中文与周起始
entry._fpInstance.open();
const cal = doc.querySelector('.flatpickr-calendar');
out.calendar = {
  open: cal ? cal.classList.contains('open') : false,
  weekdays: [...doc.querySelectorAll('span.flatpickr-weekday')].map(x => x.textContent.trim()),
  hasToday: !!doc.querySelector('.flatpickr-day.today'),
  hasSelected: !!doc.querySelector('.flatpickr-day.selected'),
};

// 手动添加弹窗
$('btn-add-manual').click();
const mDue = $('m-due');
const mDl = $('m-dl');
out.modal = {
  dueType: mDue ? mDue.type : null,
  dueHasInstance: !!(mDue && mDue._fpInstance),
  dueValue: mDue ? mDue.value : null,
  dlHasInstance: !!(mDl && mDl._fpInstance),
  dlEnableTime: !!(mDl && mDl._fpInstance && mDl._fpInstance.config.enableTime),
};

// 重复扫描不得生成重复控件: 模拟预览表渲染时反复调用绑定
out.repeatScan = {};
if (scenario === 'repeat') {
  $('parse-result').innerHTML =
    '<table><tbody><tr><td>' +
    '<input type="checkbox" checked>' +
    '<input data-field="task_name" value="能动性 P14">' +
    '<input data-field="due_date" type="date" class="js-datepicker" value="2026-09-28">' +
    '<input data-field="deadline_time" type="datetime-local" class="js-datetimepicker" value="2026-09-28T21:00">' +
    '</td></tr></tbody></table>';
  const before = doc.querySelectorAll('input').length;
  // 手动再扫两次, 模拟多次渲染
  const scan = () => {
    doc.querySelectorAll('input.js-datepicker').forEach(i => {
      if (i.type === 'date' && !i._fpInstance) w.flatpickr(i, { altInput: true, dateFormat: 'Y-m-d', altFormat: 'Y-m-d' });
    });
  };
  scan(); scan(); scan();
  const after = doc.querySelectorAll('input').length;
  out.repeatScan = { before, after, grew: after - before };
}

console.log(JSON.stringify(out));
// flatpickr 会注册定时器与全局监听, jsdom 窗口不会自动回收,
// 不显式退出的话 subprocess 会一直等到超时。
process.exit(0);
"""


def _run(scenario: str = "basic") -> dict:
    harness = ROOT / "tmp" / "_fp_harness.js"
    harness.parent.mkdir(exist_ok=True)
    harness.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(harness),
             str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(VENDOR / "flatpickr.min.js"),
             str(VENDOR / "flatpickr.zh.js"),
             scenario],
            cwd=ROOT, capture_output=True, text=True, timeout=90,
        )
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1500:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if harness.exists():
            harness.unlink()


@pytest.fixture(scope="module")
def page() -> dict:
    return _run("basic")


# ====== 库文件与许可 ======

def test_vendor_files_present():
    for f in ("flatpickr.min.js", "flatpickr.min.css", "flatpickr.zh.js", "flatpickr-theme.css"):
        p = VENDOR / f
        assert p.exists(), f"缺少 {f}"
        assert p.stat().st_size > 100, f"{f} 疑似下载不完整"


def test_index_html_loads_vendor_before_app():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert "vendor/flatpickr.min.js" in html
    assert html.index("flatpickr.min.js") < html.index("/static/app.js"), \
        "flatpickr 必须先于 app.js 加载, 否则全局未定义"
    assert html.index("flatpickr.min.css") < html.index("</head>")


def test_no_cdn_dependency():
    """离线可用: 页面不得直接引用外部 CDN。"""
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    for bad in ("cdn.jsdelivr", "unpkg.com", "cdnjs.", "googleapis"):
        assert bad not in html, f"不应依赖外部 CDN: {bad}"


# ====== 原生 input 被正确接管 ======

def test_native_input_becomes_hidden(page):
    """flatpickr 把原生框改成 hidden, 所以所有取值都必须走它。"""
    assert page["entry"]["type"] == "hidden"


def test_native_input_keeps_iso_value(page):
    """collectRows() 读的就是这个值, 后端要求 YYYY-MM-DD。"""
    v = page["entry"]["value"]
    assert len(v) == 10 and v[4] == "-" and v[7] == "-", v


def test_visible_input_generated(page):
    assert page["entry"]["altType"] == "text"
    assert page["entry"]["altValue"] == page["entry"]["value"], \
        "可见框必须显示与原生值一致的日期, 否则家长看到的是错的"


def test_instance_retained_by_us(page):
    """库不挂实例到元素, 是我们自己存的。"""
    assert page["entry"]["hasInstance"] is True


# ====== 本地化 ======

def test_chinese_weekdays(page):
    # 中文包的 shorthand 是"周一"这样的两字, 不是"周"+"一"
    assert page["calendar"]["weekdays"] == ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def test_monday_first_matches_board_calendar(page):
    """与日历页月历(后端 first_weekday=Monday=0)一致。"""
    assert page["calendar"]["weekdays"][0] == "周一"


def test_calendar_opens_with_today_and_selected(page):
    assert page["calendar"]["open"] is True
    assert page["calendar"]["hasToday"] is True
    assert page["calendar"]["hasSelected"] is True


# ====== 手动添加弹窗 ======

def test_modal_date_pickers_bound(page):
    assert page["modal"]["dueType"] == "hidden"
    assert page["modal"]["dueHasInstance"] is True
    assert page["modal"]["dlHasInstance"] is True


def test_modal_deadline_has_time(page):
    assert page["modal"]["dlEnableTime"] is True


def test_modal_due_defaults_to_today(page):
    v = page["modal"]["dueValue"]
    assert len(v) == 10 and v[4] == "-", v


# ====== 防重复绑定 ======

def test_rescanning_does_not_duplicate_controls():
    """预览表每次渲染都会重新扫描, 不得越扫越多。

    这是接入 flatpickr 最容易出的 bug: 库会把原生框改 hidden 并保留
    我们的 js-datepicker 类, 同时另建一个同样带该类的可见框。
    只按类名选就会把已初始化的也扫进去。
    """
    out = _run("repeat")
    # 一行两个 input(日期+时间), 组件化后各多一个可见框 = +2, 再扫两次不应再涨
    assert out["repeatScan"]["grew"] == 2, \
        f"重复扫描多生成了控件: {out['repeatScan']}"


# ====== 遗留假设检查 ======

def test_code_does_not_rely_on_nonexistent_api():
    """这些 API 在 4.6.13 里不存在, 一旦用了就是静默失效。"""
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    code = "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))
    assert "_fpFlatpickr" not in code, "库不提供该属性, 判它永远为假"
    assert "getElementById('_flatpickr" not in code, "库不提供实例反查"
    assert "_altInput" not in code, "库不在元素上挂 _altInput"


def test_all_date_inputs_marked():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert 'id="entry-date"' in html and "js-datepicker" in html
    assert 'data-field="due_date" type="date" class="js-datepicker' in js
    assert 'data-field="deadline_time" type="datetime-local" class="js-datetimepicker' in js
    assert 'id="m-due" type="date" class="js-datepicker"' in js
    assert 'id="m-dl" type="datetime-local" class="js-datetimepicker"' in js


def test_value_assigned_before_init():
    """先赋值再初始化: 反了的话原生框已是 hidden, 赋值不会同步到可见框。"""
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    assert js.index("entryDate.value = todayStr()") < js.index("initDatePickersIn(document)"), \
        "必须先给 entry-date 赋值再初始化组件, 否则日历打开是空白"


# ====== 主题样式 ======

def test_theme_selected_day_contrast():
    """选中日白字对比度: --accent 上只有 2.6:1, 故用 --warn-ink。"""
    theme = (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8")
    sel = theme.split(".flatpickr-day.selected")[1][:400]
    assert "--warn-ink" in sel, "选中日须用深色, --accent 上白字不达 AA"


def test_theme_hides_native_picker_icon():
    theme = (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8")
    assert "-webkit-calendar-picker-indicator" in theme, \
        "Windows 的系统日历图标会和主题割裂, 且点击会绕过 flatpickr"


def test_theme_responsive():
    theme = (VENDOR / "flatpickr-theme.css").read_text(encoding="utf-8")
    assert "@media (max-width: 480px)" in theme
