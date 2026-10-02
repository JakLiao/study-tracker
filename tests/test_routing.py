"""菜单页独立 URI (hash 路由) 测试.

需求(2026-09-28): 每个菜单页有单独的前端路径, 方便直接打开对应功能页。

前端用 hash(#/board)而非真实路径: 无需后端配合、刷新不 404,
也不会和将来的 /api/* 抢前缀。后端仍保留同路径兜底(/board -> 首页),
手输路径漏掉 # 时不至于 404。

重点覆盖三个最容易出错的点:
1. 直接打开深链能落到对应页, 而不是先闪一下看板
2. 非法/空 hash 回退到看板, 且不会把 URL 改写掉
3. 前进/后退只切视图, 不写回 hash —— 否则历史记录被钉死,
   用户按返回看似回到上一页, 实际又被推回同一个 tab
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

TABS = ["board", "entry", "list", "weekly", "settings"]


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def main_py() -> str:
    return (ROOT / "app/main.py").read_text(encoding="utf-8")


# ====== 1. 静态结构 ======

def test_all_tabs_listed(app_js):
    """五页都要在路由表里, 少一个就意味着那个页打不开。"""
    m = re.search(r"const TABS = \[(.*?)\]", app_js, re.S)
    assert m, "未找到 TABS 定义"
    for t in TABS:
        assert f"'{t}'" in m.group(1), f"{t} 缺少路由条目"


def test_default_tab_is_board(app_js):
    assert re.search(r"const DEFAULT_TAB = 'board'", app_js)


def test_push_state_writes_hash(app_js):
    assert "history.pushState" in app_js, "切换菜单未写入 URL"
    assert "'#/' + tab" in app_js


def test_listens_hashchange_and_popstate(app_js):
    """只监听 hashchange 不够: pushState 不会触发 hashchange,
    但浏览器前进/后退会同时触发两者, 少监听一个就按返回键没反应。"""
    assert "addEventListener('hashchange'" in app_js
    assert "addEventListener('popstate'" in app_js


def test_recursion_guard_present(app_js):
    """必须有抑制标志。switchTab 写 hash -> hashchange -> switchTab,
    没有这层保护就是死循环, 表现为点菜单后白屏。"""
    assert "let _syncingHash = false;" in app_js
    assert app_js.count("_syncingHash = true") >= 2, "读写抑制标志的地方应成对出现"


def test_invalid_hash_falls_back(app_js):
    assert re.search(r"function tabFromHash\(\)", app_js)
    assert "return TABS.includes(raw) ? raw : DEFAULT_TAB;" in app_js


def test_switch_tab_guards_unknown_name(app_js):
    """switchTab 会被 confirmBatch 等处直接调用, 传错名字不能把界面切瞎。"""
    assert "const tab = TABS.includes(name) ? name : DEFAULT_TAB;" in app_js


def test_switch_tab_option_skips_url_write(app_js):
    assert "opts.updateUrl !== false" in app_js, "缺少 updateUrl 开关, 无法避免前进后退时写回"


def test_startup_applies_hash(app_js):
    """启动必须按 hash 决定初始页, 且不再写回 URL。"""
    assert "switchTab(tabFromHash(), { updateUrl: false });" in app_js


# ====== 2. 后端兜底 ======

def test_backend_lists_pages(main_py):
    from app.main import FRONTEND_PAGES
    for t in TABS:
        assert t in FRONTEND_PAGES, f"后端兜底缺少 {t}"


def test_backend_alias_not_catch_all(main_py):
    """必须是白名单而不是 catch-all。

    写成 catch-all 会把 /api/notfound 这类真实 404 吞成首页 ——
    排障时看到首页, 会误以为是接口问题, 方向直接跑偏。
    """
    assert "if page in FRONTEND_PAGES:" in main_py
    assert "raise HTTPException(status_code=404" in main_py


def test_backend_keeps_real_404(app_client):
    r = app_client.get("/definitely-not-a-page")
    assert r.status_code == 404, "不存在的路径必须仍是 404"


@pytest.mark.parametrize("page", TABS)
def test_backend_serves_index_for_pages(app_client, page):
    r = app_client.get(f"/{page}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


# ====== 2b. 静态资源缓存策略 (2026-10-02) ======
#
# 背景: 改完 app.js 刷新页面看不到变化。FastAPI StaticFiles 默认不发
# Cache-Control, 浏览器对"无缓存指令"的启发式策略是尽量复用本地副本,
# 普通刷新往往根本不重新校验 —— 于是代码改对了、界面却还是旧的。


def test_static_js_sends_no_cache(app_client):
    """app.js 必须显式声明 no-cache, 否则改了代码刷新也看不到。"""
    r = app_client.get("/static/app.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache", \
        f"app.js 缺少 Cache-Control: {r.headers.get('cache-control')!r}"


def test_static_css_sends_no_cache(app_client):
    """样式表同样会改, 不能只管 JS。"""
    r = app_client.get("/static/styles.css")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_index_html_sends_no_cache(app_client):
    """首页也要。

    index.html 走 FileResponse 而不是 StaticFiles 挂载, 是两条不同的代码
    路径。只在 StaticFiles 上加头, HTML 仍会被缓存 —— 结果是"页面结构是旧
    的、但引用到的 app.js 是新的"这种半新半旧状态, 排障时更难看出问题在哪。
    """
    r = app_client.get("/")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_conditional_get_still_works(app_client):
    """no-cache 不等于每次都全量重下: 内容没变应回 304。

    no-cache 的语义是"每次使用前必须校验", 而不是"不许存"。配合 ETag,
    浏览器带 If-None-Match 来问一次, 服务端回 304 且不带 body。这条锁住
    的就是"加缓存头没有把带宽吃满" —— 若哪天改成 no-store, 这里会失败。
    """
    first = app_client.get("/static/app.js")
    etag = first.headers.get("etag")
    assert etag, "StaticFiles 应自带 ETag, 没有它 no-cache 就退化成每次全量下载"

    second = app_client.get(
        "/static/app.js",
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304, \
        f"内容未变却没回 304, 实际 {second.status_code}"
    assert second.content == b"", "304 不应带 body"


def test_never_immutable(app_client):
    """不能用 immutable —— 静态资源是固定文件名, 没有内容指纹。

    immutable 等于告诉浏览器"这文件永远不变, 别再问了"。项目里资源叫
    app.js 而不是 app.<hash>.js, 内容和文件名没有绑定关系, 一旦发出去
    就再也更新不了, 比现在的缓存问题更严重。这条是防线, 防止日后有人
    为了"性能更好"顺手加上。
    """
    cc = app_client.get("/static/app.js").headers.get("cache-control", "")
    assert "immutable" not in cc, f"不得使用 immutable: {cc!r}"


def test_head_requests_also_carry_header(app_client):
    """HEAD 只取头不取 body, 但缓存指令同样要带上。"""
    r = app_client.head("/static/app.js")
    assert r.headers.get("cache-control") == "no-cache"


@pytest.mark.parametrize("page", TABS)
def test_alias_pages_send_no_cache(app_client, page):
    """菜单页(/board 等)也是 HTML, 同样会被浏览器缓存, 别只管首页。"""
    r = app_client.get(f"/{page}")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"


def test_api_responses_not_touched(app_client):
    """中间件按路径收窄, 不能顺手给 API 也挂上。

    接口响应本来就该实时, 额外挂一条 no-cache 只会给每次请求白加一次
    校验往返。这条锁住范围 —— 若中间件改成"所有 GET 都加头", 这里会失败。
    """
    r = app_client.get("/api/health")
    assert r.status_code == 200
    assert "cache-control" not in r.headers, \
        f"API 响应不该由静态资源中间件管: {r.headers.get('cache-control')!r}"


def test_404_response_not_marked(app_client):
    """中间件只看路径, 不该给 404 也打上"静态资源"标签。"""
    r = app_client.get("/definitely-not-a-page")
    assert r.status_code == 404
    assert "cache-control" not in r.headers


# ====== 3. 运行时 (jsdom) ======

node = shutil.which("node")
needs_node = pytest.mark.skipif(not node, reason="需要 node 运行前端路由")

HARNESS = r"""
const fs = require('fs');
const { JSDOM } = require('jsdom');
const [htmlPath, appJsPath, fpPath, zhPath, startHash] = process.argv.slice(2);

const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'), {
  url: 'http://127.0.0.1:8876/' + startHash,
  runScripts: 'outside-only', pretendToBeVisual: true,
});
const w = dom.window, doc = w.document;
const requested = [];
w.fetch = async (u, o) => {
  requested.push(String(u));
  return { ok: true, status: 200, text: async () => JSON.stringify({ items: [], created_ids: [], skipped: [] }) };
};
w.confirm = () => true; w.alert = () => {}; w.scrollTo = () => {};
w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
w.eval(fs.readFileSync(fpPath, 'utf8'));
w.eval(fs.readFileSync(zhPath, 'utf8'));
w.eval(fs.readFileSync(appJsPath, 'utf8'));

const out = { hashAfterStart: w.location.hash, activeView: null, requested: [] };
const active = () => {
  const v = doc.querySelector('.view.active');
  return v ? v.id : null;
};
const nav = () => {
  const b = [...doc.querySelectorAll('#tabs button')].find(x => x.classList.contains('active'));
  return b ? b.dataset.tab : null;
};

setTimeout(() => {
  out.activeView = active();
  out.activeTab = nav();

  // 点菜单 -> URL 应更新
  const entryBtn = [...doc.querySelectorAll('#tabs button')].find(b => b.dataset.tab === 'entry');
  entryBtn.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  out.hashAfterClick = w.location.hash;
  out.viewAfterClick = active();

  // 模拟前进/后退: 直接改 hash 并触发 hashchange
  w.location.hash = '#/weekly';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  out.hashAfterPop = w.location.hash;
  out.viewAfterPop = active();
  out.tabAfterPop = nav();

  // 非法 hash -> 回退看板, 且不应改写 URL
  w.location.hash = '#/not-a-real-page';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  out.viewAfterBad = active();
  out.hashAfterBad = w.location.hash;

  out.requested = requested;
  console.log(JSON.stringify(out));
  process.exit(0);
}, 300);
"""


def _run_live(start_hash: str) -> dict:
    h = ROOT / "tmp" / "_route_harness.js"
    h.parent.mkdir(exist_ok=True)
    h.write_text(HARNESS, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, str(h),
             str(ROOT / "app/static/index.html"),
             str(ROOT / "app/static/app.js"),
             str(ROOT / "app/static/vendor/flatpickr.min.js"),
             str(ROOT / "app/static/vendor/flatpickr.zh.js"),
             start_hash],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        assert r.returncode == 0, f"harness 失败:\n{r.stderr[-1500:]}"
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        if h.exists():
            h.unlink()


@needs_node
def test_deeplink_entry():
    """直接打开 /#/entry 应落在登记页, 且不先渲染看板。

    注意只匹配看板页面接口 "/api/board", 不能用 "/board" —— 顶栏的
    loadHealth 会请求 /api/board/dates 取最近有记录的日期, 那是日期栏
    本身需要的, 与"有没有先渲染看板"无关, 误判会让这条测试永远失败。
    """
    out = _run_live("#/entry")
    assert out["activeView"] == "view-entry", out
    assert out["activeTab"] == "entry", out
    assert not any(u.startswith("/api/board?") or u == "/api/board" for u in out["requested"]), \
        f"深链直达登记页时不该先请求看板页面数据: {out['requested']}"


@needs_node
def test_deeplink_weekly():
    out = _run_live("#/weekly")
    assert out["activeView"] == "view-weekly", out


@needs_node
def test_deeplink_settings():
    out = _run_live("#/settings")
    assert out["activeView"] == "view-settings", out


@needs_node
def test_empty_hash_defaults_to_board():
    out = _run_live("")
    assert out["activeView"] == "view-board", out


@needs_node
def test_click_menu_updates_url():
    """点菜单后地址栏要变, 这样才能复制链接分享。"""
    out = _run_live("#/board")
    assert out["hashAfterClick"] == "#/entry", out
    assert out["viewAfterClick"] == "view-entry", out


@needs_node
def test_popstate_switches_view():
    out = _run_live("#/board")
    assert out["viewAfterPop"] == "view-weekly", out
    assert out["tabAfterPop"] == "weekly", out


@needs_node
def test_invalid_hash_falls_back_to_board():
    out = _run_live("#/not-a-real-page")
    assert out["activeView"] == "view-board", out


@needs_node
def test_no_infinite_hash_loop():
    """死循环防护: hashchange 后不应再写回 hash。"""
    out = _run_live("#/board")
    assert out["hashAfterBad"] == "#/not-a-real-page", \
        "非法 hash 不应被改写(说明 applyHashRoute 又写回去了, 会形成死循环)"
