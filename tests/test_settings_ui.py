"""设置页紧凑布局测试 (2026-09-28).

用户截图反馈: 设置项的输入框太高, 四个设置就占两屏还多。
根因是短值 (模型名/时间/年级, 最长 9 字符) 也用 rows="3" 的 textarea,
每个占 120px 高。

这些用例锁住:
  1. 短值必须走单行 input, 长文本才用 textarea
  2. 布局必须是「标签 + 输入 + 保存」同一行
  3. 高度由 CSS 固定, 不靠浏览器默认值
  4. JSON 字典项仍走键值表并保持整块排布
  5. 不用 :has() 做布局分支 (老浏览器整条规则失效)
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

# 静态检查部分无需 jsdom, 始终运行; 运行时部分在文件末尾用 skipif 守卫。


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


def _code(src: str) -> str:
    """去注释, 避免注释里的文字被当成实现。"""
    s = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return "\n".join(l for l in s.splitlines() if not l.strip().startswith("//"))


def _block(css: str, sel: str) -> str:
    """取某条规则的内容(排除注释里的同名选择器)。"""
    css_no_comment = re.sub(r"/\*[\s\S]*?\*/", "", css)
    m = re.search(r"^[ \t]*" + re.escape(sel) + r" \{", css_no_comment, re.M)
    if not m:
        return ""
    end = css_no_comment.find("\n}", m.start())
    return css_no_comment[m.start():end] if end != -1 else ""


# ====== 1. 控件自适应 ======

def test_short_uses_input_long_uses_textarea(app_js):
    """核心: 不再一律用 textarea。"""
    code = _code(app_js)
    assert "const isShort =" in code
    assert '<input type="text"' in code, "短值应走单行 input"
    assert "<textarea" in code, "长文本仍需 textarea 兜底"


def test_no_unconditional_rows3_textarea(app_js):
    """旧写法是 <textarea data-key=... rows="3">, 必须消失。"""
    code = _code(app_js)
    assert re.search(r'<textarea data-key="\$\{it\.key\}" rows="3">', code) is None


def test_short_detection_rules(app_js):
    code = _code(app_js)
    assert re.search(r"const isMultiline = /\\r\?\\n/", code), "含换行视为多行"
    assert re.search(r"const isShort = !isMultiline && it\.value\.length <= \d+", code), \
        "单行且长度在阈值内才算短值"


def test_save_reads_field_not_stale_ref(app_js):
    """保存必须读当前控件的值。

    注意切分点: PATCH 语句在 saveBtn.addEventListener 的**回调体内**,
    所以要切它之后的内容, 不是之前。
    """
    code = _code(app_js)
    branch = code.split("const isShort =")[1]
    assert "await api('PATCH', `/settings/${it.key}`, { value: field.value })" in branch
    # 不应再从 textarea 变量取值
    assert "{ value: ta.value }" not in branch


# ====== 2. 紧凑布局 ======

def test_setting_row_groups_input_and_button(app_js):
    code = _code(app_js)
    assert "setting-row" in code
    assert "row.appendChild(field)" in code
    assert "row.appendChild(saveBtn)" in code
    assert "card.appendChild(row)" in code


def test_label_is_html_label_for(app_js):
    """用 <label for> 关联输入框, 点标签能聚焦, 键盘也更好用。"""
    code = _code(app_js)
    assert "<label class=\"setting-label\" for=" in code
    assert "id=\"set-${escapeAttr(it.key)}\"" in code


def test_chinese_labels_defined(app_js):
    code = _code(app_js)
    assert "SETTING_LABELS" in code
    for key in ("ai_model_version", "dinner_time", "grade", "school_dismiss_time"):
        assert key in code, f"缺少 {key} 的中文标签"


def test_original_key_kept_visible(app_js):
    """原始键以小字 code 保留, 便于家长对照与排障。"""
    code = _code(app_js)
    assert "setting-key" in code


# ====== 3. CSS 契约 ======

def test_input_fixed_height(styles):
    """固定高度是紧凑的关键 —— 不给 height 会比同行按钮高一截。"""
    b = _block(styles, ".setting-input")
    assert re.search(r"height:\s*34px", b)


def test_input_fills_width(styles):
    b = _block(styles, ".setting-input")
    assert re.search(r"flex:\s*1 1 auto", b)
    assert "min-width: 0" in b, "缺 min-width:0 会在 flex 里溢出"


def test_card_is_flex_row(styles):
    b = _block(styles, ".setting-card")
    assert re.search(r"display:\s*flex", b)
    assert "align-items: center" in b


def test_list_gap_tightened(styles):
    b = _block(styles, "#settings-content .settings-list")
    assert re.search(r"gap:\s*var\(--space-2\)", b)


def test_textarea_only_for_long_text(styles):
    b = _block(styles, ".setting-textarea")
    assert re.search(r"min-height:\s*64px", b)
    assert "height: auto" in b
    assert "resize: vertical" in b


def test_block_variant_for_dict_items(styles):
    """JSON 字典项内容多, 要整块排布。"""
    b = _block(styles, ".setting-card.is-block")
    assert re.search(r"display:\s*block", b)


def test_no_has_selector_in_css(styles):
    """:has() 在老浏览器会让整条规则失效, 布局会散。"""
    code = re.sub(r"/\*[\s\S]*?\*/", "", styles)
    assert ":has(" not in code


def test_responsive(styles):
    assert re.search(r"@media \(max-width: 480px\)[\s\S]{0,500}?\.setting-label", styles)


def test_card_padding_reduced(styles):
    """卡片内边距也要收 —— 六个设置项累加很可观。"""
    b = _block(styles, ".setting-card")
    assert re.search(r"padding:\s*var\(--space-2\)", b)


# ====== 4. 运行时 (jsdom) ======

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, jsPath, fpPath, zhPath] = process.argv.slice(2);
const SETTINGS = { items: [
  { key: 'ai_model_version', value: 'MiniMax-M3' },
  { key: 'dinner_time', value: '18:30' },
  { key: 'grade', value: '3' },
  { key: 'school_dismiss_time', value: '16:30' },
  { key: 'subject_baseline_minutes', value: '{"语文": 20, "数学": 25, "英语": 15}' },
  { key: 'task_type_baseline_minutes', value: '{"听力": 10}' },
]};
const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window;
w.fetch = async (u, o) => {
  const s = String(u);
  const ok = (d) => ({ ok: true, status: 200, text: async () => JSON.stringify(d) });
  if (s.includes('/api/settings') && !(o && o.method === 'PATCH')) return ok(SETTINGS);
  if (s.includes('/health')) return ok({ ai_model: 'x', ai_reachable: true });
  if (o && o.method === 'PATCH') return ok({});
  return ok({});
};
w.confirm = () => true;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(jsPath, 'utf8') + '\n;globalThis.__s = () => switchTab("settings");');
(async () => {
  w.__s();
  await new Promise(r => setTimeout(r, 300));
  const doc = w.document;
  const cards = [...doc.querySelectorAll('.setting-card')];
  const short = ['ai_model_version', 'dinner_time', 'grade', 'school_dismiss_time'];
  const out = { count: cards.length, short: {}, labels: [], kvCards: 0, blockCards: 0 };
  for (const k of short) {
    const c = cards.find(x => x.querySelector(`[data-key="${k}"]`));
    const f = c.querySelector(`[data-key="${k}"]`);
    out.short[k] = { tag: f.tagName, rows: f.getAttribute('rows'), value: f.value,
      hasRow: !!c.querySelector('.setting-row'),
      inputInRow: !!(c.querySelector('.setting-row .setting-input')),
      btnInRow: !!(c.querySelector('.setting-row .save-btn')) };
  }
  out.labels = cards.map(c => { const n = c.querySelector('.setting-name'); return n ? n.textContent : null; });
  out.keys = cards.map(c => { const n = c.querySelector('.setting-key'); return n ? n.textContent : null; });
  out.kvCards = cards.filter(c => c.querySelector('.settings-kv')).length;
  out.blockCards = cards.filter(c => c.classList.contains('is-block')).length;
  out.kvRows = doc.querySelectorAll('[data-kv="k"]').length;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@pytest.fixture(scope="module")
def live() -> dict:
    h = ROOT / "tmp" / "_set_harness.js"
    h.parent.mkdir(exist_ok=True)
    h.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(h), str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(ROOT / "app/static/vendor/flatpickr.min.js"),
             str(ROOT / "app/static/vendor/flatpickr.zh.js")],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1200:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if h.exists():
            h.unlink()


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
def test_live_all_settings_rendered(live):
    assert live["count"] == 6


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
@pytest.mark.parametrize("key", ["ai_model_version", "dinner_time", "grade", "school_dismiss_time"])
def test_live_short_value_uses_input(live, key):
    s = live["short"][key]
    assert s["tag"] == "INPUT", f"{key} 应为单行 input"
    assert s["rows"] is None, f"{key} 不应有 rows 属性"


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
@pytest.mark.parametrize("key", ["dinner_time", "grade", "school_dismiss_time"])
def test_live_short_value_layout_is_one_row(live, key):
    s = live["short"][key]
    assert s["hasRow"] is True
    assert s["inputInRow"] is True
    assert s["btnInRow"] is True, "保存按钮应与输入框同一行"


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
def test_live_values_preserved(live):
    assert live["short"]["dinner_time"]["value"] == "18:30"
    assert live["short"]["school_dismiss_time"]["value"] == "16:30"
    assert live["short"]["grade"]["value"] == "3"
    assert live["short"]["ai_model_version"]["value"] == "MiniMax-M3"


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
def test_live_labels_are_chinese(live):
    assert live["labels"][0] == "AI 模型版本"
    assert live["labels"][1] == "晚餐时间"
    assert live["labels"][2] == "年级"
    assert live["labels"][3] == "放学时间"


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
def test_live_original_keys_kept(live):
    assert live["keys"][3] == "school_dismiss_time"


@pytest.mark.skipif(not jsdom_available, reason="需要 node 与 jsdom")
def test_live_dict_items_use_kv_table(live):
    assert live["kvCards"] == 2
    assert live["blockCards"] == 2
    assert live["kvRows"] >= 4
