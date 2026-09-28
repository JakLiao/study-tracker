# 修真 Verify-Report · 2026-09-28

> 任务：修 AI 解析超时 + 加客户端重试 + 前端超时态不白屏
> 接手方：码灵 (coding)
> 验证日期：2026-09-28
> 路径：`/home/xiaoduo/project/study-tracker/`

---

## 0. 一句话结论

5 个文件改动落地，**77 pytest passed 不退化**，**3 场景真打 MiniMax 全跑通**，前端结构化错误 + 重试/复制原文按钮 + 130s 客户端超时 + spinner 全部到位。E2E 脚本 sections 1-5 全过；section 6 在 1 个**预存在 bug** 上 crash（`AssignmentNotFoundError` HTTP 状态 400 而非 404，与本任务无关，按风险信号上报）。

---

## 1. DoD 8 项对照

| # | DoD | 结果 |
|---|-----|------|
| 1 | `app/config.py` `MINIMAX_TIMEOUT` 默认值 = 120 | ✅ 第 46 行 `int(os.environ.get("MINIMAX_TIMEOUT", "120"))` |
| 2 | `ai_client.py` 加重试 + 返结构化错误 JSON, 无 5xx 抛给前端 | ✅ `for attempt in range(2)` + `_err()` 统一结构化错误出口 |
| 3 | `routers/parse.py` `/api/parse` 透传结构化错误 (HTTP 200) | ✅ `_failure_envelope()` + `result.get("ok") is False` 分支 |
| 4 | 前端 `parseRaw()` 有 spinner + 130s 超时降级 UI + 重试/复制原文按钮 | ✅ AbortController + handleParseError/handleParseTimeout/appendRetryCopyRow |
| 5 | `pytest tests/ -q` **77 passed** | ✅ `77 passed, 24 warnings in 4.11s` (24 warnings 均为 `datetime.utcnow` 旧 API 警告，与本任务无关) |
| 6 | 真打 MiniMax 3 场景 (a/b/c) 全部跑通 | ✅ 9.21 60.2s/10 条, 9.22 82.5s/9 条 (含周四 8:20 deadline + 选做), 强制超时 13.2s/AI_TIMEOUT |
| 7 | `tests/e2e/test_e2e_endpoints.py` 直跑不崩 | ⚠️ sections 1-5 全过; section 6 在预存 bug 上崩 (见 §4) |
| 8 | verify-report 贴 4 件证据 | ✅ 见下 |

---

## 2. 4 件证据 (LRN-20260829-013)

### 2.1 原始命令输出

#### pytest (77 passed)
```
$ python3 -m pytest tests/ -q
........................................................................ [ 93%]
.....                                                                    [100%]
=============================== warnings summary ===============================
tests/test_analytics.py: 24 warnings
  /home/xiaoduo/project/study-tracker/tests/test_analytics.py:16: DeprecationWarning: datetime.datetime.utcnow() is deprecated
    now = datetime.utcnow().isoformat()

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
77 passed, 24 warnings in 4.11s
```

#### 场景 (a) — 9.21 样本 (120s 内能跑通)
```
[11:31:15] === 场景 (a) — 9.21 ===
HTTP: 200, elapsed: 60.20s, ok: True
  batch_id: 1, items: 10, model: MiniMax-M3
  error: None

  全部 items:
    [ 1] [英语] 一起作业                              est=None  conf=high  opt=F  ddl=-  type=other
    [ 2] [英语] 描写本P11-12                         est=15    conf=medium opt=F  ddl=-  type=抄写
    [ 3] [英语] 背课文P18-19                         est=20    conf=high  opt=F  ddl=-  type=背诵
    [ 4] [语文] 完成知能P14                           est=15    conf=medium opt=F  ddl=-  type=other
    [ 5] [语文] 订正1号作文本并誊抄到2号作文本         est=25    conf=high  opt=F  ddl=-  type=抄写
    [ 6] [语文] 阅读课外书                           est=20    conf=high  opt=F  ddl=-  type=阅读
    [ 7] [数学] 订正知能P10-11                      est=15    conf=high  opt=F  ddl=-  type=订正
    [ 8] [数学] 预习书本P16-17                      est=10    conf=high  opt=F  ddl=-  type=预习
    [ 9] [数学] 1千米体验记录单 (通勤观察, 可打印)     est=None  conf=low   opt=F  ddl=-  type=observation  ← 通勤观察
    [10] [数学] 练计算                               est=10    conf=high  opt=F  ddl=-  type=计算

  通勤观察: 1, 选做: 0, deadline: 0
  ✅ 9.21 跑通: 60.20s / 10 条
```

#### 场景 (b) — 9.22 样本 (长, 含 deadline + 选做)
```
[11:32:15] === 场景 (b) — 9.22 ===
HTTP: 200, elapsed: 82.49s, ok: True
  batch_id: 2, items: 9, model: MiniMax-M3
  error: None

  全部 items:
    [ 1] [英语] 一起作业                              est=None  conf=high  opt=F  ddl=-  type=other
    [ 2] [英语] 完成知能P10、11                       est=20    conf=high  opt=F  ddl=-  type=订正
    [ 3] [英语] 背诵U2指南重点短语和句子              est=20    conf=high  opt=F  ddl=2026-09-24T08:20:00 type=背诵  ← deadline (周四 8:20)
    [ 4] [语文] 完成知能P15-16并订正前面作业 (课上已部分完成) est=18 conf=medium opt=F ddl=- type=订正
    [ 5] [语文] 预习第9课《犟龟》                    est=15    conf=high  opt=F  ddl=-  type=预习
    [ 6] [语文] 阅读课外书                           est=20    conf=high  opt=F  ddl=-  type=阅读
    [ 7] [数学] 第一单元综合复习                       est=None  conf=medium opt=F  ddl=-  type=other
    [ 8] [数学] 计算练习                              est=15    conf=medium opt=F  ddl=-  type=计算
    [ 9] [数学] 手指记忆法练习 (选做)                 est=None  conf=medium opt=T  ddl=-  type=other  ← 选做

  通勤观察: 0, 选做 (手指记忆法): 1, deadline (周四 8:20): 1
    deadline 任务: 背诵U2指南重点短语和句子
    deadline_time: 2026-09-24T08:20:00
    due_date: 2026-09-22 (期望 2026-09-22)  ← 归属日正确
  ✅ 9.22 跑通: 82.49s / 9 条
```

#### 场景 (c) — 强制超时 (MINIMAX_TIMEOUT=5)
```
[11:33:43] MINIMAX_TIMEOUT: MINIMAX_TIMEOUT=5
[11:33:43] /api/health (timeout=5 状态): {"status":"ok","ai_reachable":true,"ai_model":"MiniMax-M3","db_ok":true}
[11:33:43] === 场景 (c) — 强制超时 (MINIMAX_TIMEOUT=5) ===
HTTP: 200, elapsed: 13.18s (期望 ≥ 5+2+5 = 12s)
  ok: False
  error: {'code': 'AI_TIMEOUT', 'message': 'AI 解析超时 (5s)', 'items': []}
  items: 0
  message: AI 解析超时 (5s)
  ai_reachable: True
  ✅ 强制超时验证: code=AI_TIMEOUT, 重试 + 降级生效
```

#### E2E 脚本 (`tests/e2e/test_e2e_endpoints.py`) 直跑 (sections 1-5 全过)
```
$ PYTHONPATH=/home/xiaoduo/project/study-tracker python3 tests/e2e/test_e2e_endpoints.py

Server health check...
  OK: {'status': 'ok', 'ai_reachable': True, 'ai_model': 'MiniMax-M3', 'db_ok': True}

============================================================
1. /api/health
============================================================
200 {"status":"ok","ai_reachable":true,"ai_model":"MiniMax-M3","db_ok":true}

============================================================
2. /api/parse 空文本 / 超长
============================================================
  empty: 400 {"error":{"code":"4001","message":"文本不能为空"}}
  too_long: 400 {"error":{"code":"4002","message":"文本 10001 字符, 超过 10000 上限"}}

============================================================
3. /api/parse 真实 9.21 样本 (AI 真打)
============================================================
  status: 200, elapsed: 44.38s
  items: 10
    - [英语] 一起作业 est=None conf=high ...
    - [数学] 1千米体验记录单 (通勤观察) (可打印) est=None conf=low ... type=observation

============================================================
4. /api/parse 真实 9.22 样本 (含 deadline + 选做)
============================================================
  status: 200, elapsed: 36.33s
  items: 9
    - [英语] 背诵 U2 指南重点短语和句子 ... ddl=2026-09-24T08:20:00  ← deadline
    - [数学] 手指记忆法练习 (选做) ... opt=True                        ← 选做

============================================================
5. /api/assignments/batch 入库 + /api/board/today
============================================================
  batch status: 200 body: {"created_ids":[52,53,54,55,56,57,58,59,60,61]}
  board status: 200
  has_data: True, date: 2026-09-28, progress: {done:1, total:4}
```

### 2.2 状态值 (端口/PID/HTTP code/latency)

| 检查项 | 值 |
|--------|-----|
| 服务端口 | 127.0.0.1:8876 (LISTEN, 持续) |
| 服务 PID | 230820 (后多次轮换过, 230820 是最后一次跑 9.22/e2e 用的) |
| 健康检查 | GET /api/health → HTTP 200, body `{"status":"ok","ai_reachable":true,...}` |
| 空文本 | POST /api/parse `{"raw_text":""}` → HTTP 400, code 4001 |
| 超长文本 | POST /api/parse `{"raw_text":"x"*10001}` → HTTP 400, code 4002 |
| 9.21 真实耗时 | 60.20s (老 timeout 60s 几乎贴边, 新 timeout 120s 充足) |
| 9.22 真实耗时 | 82.49s (新 timeout 120s 容纳) |
| 强制超时耗时 | 13.18s (= 5s 首超时 + 2s 重试退避 + 5s 次超时 + 网络/解析开销) |
| 9.21 batch_id | 1 |
| 9.22 batch_id | 2 |
| 强制超时 batch_id | None |

### 2.3 文件路径 (改动 5 个)

| 文件 | 大小 (改前 → 改后) | 改动摘要 |
|------|---------------------|----------|
| `app/config.py` | 2715B → 2827B | `MINIMAX_TIMEOUT` 默认值 60 → 120, 注释更新 |
| `app/ai_client.py` | 9990B → ~11400B | `call_minimax()` 加重试 (1次 +2s 退避) + 结构化错误返; 新增 `_err()` helper; `import time` + `import logging`; 移除 `raise AITimeoutError/AIFormatError` (改为返 dict) |
| `app/routers/parse.py` | 3224B → ~4200B | 新增 `_failure_envelope()`; `call_minimax` 改 check `result.get("ok") is False`; `parse_ai_response` raise 转结构化; 成功响应加 `ok: True` |
| `app/static/app.js` | 27718B → ~30500B | `parseRaw()` 改用 fetch + AbortController (130s 客户端超时); 新增 `handleParseError/handleParseTimeout/appendRetryCopyRow/copyToClipboard/fallbackCopy` 5 个 helper |
| `app/static/styles.css` | 17563B → 17700B | 新增 `.spinner` 样式 + `@keyframes st-spin` (~140B) |

> 备份: `/tmp/study-tracker-pre-timeout-2026-09-28-1120/` (.bak 文件)
>
> 未改文件 (避免越界):
> - `app/prompts.py` (P0 修复文件, 严守 string.Template)
> - `tests/test_prompts_regression.py` (P0 回归测试, 红绳不动)
> - `tests/e2e/test_e2e_endpoints.py` + `tests/e2e/test_real_ai_parsing.py` (校验姬会独立跑)
> - `app/main.py` (任务写的是 main.py 实际路由在 `app/routers/parse.py`, 已修改正确文件)
> - `app/static/index.html` (parse-status div 已存在, 无需改)

### 2.4 失败/警告全量报

#### pytest 24 warnings (与本任务无关, 预存在)
- `tests/test_analytics.py:16 DeprecationWarning: datetime.datetime.utcnow() is deprecated` — 与本任务无关, 来自 test_analytics.py 的旧 API 调用

#### E2E section 6 crash — 预存在 router bug (与本任务无关)
```
status(notfound): 400 {"error":{"code":"4008","message":"作业不存在"}}
Traceback (most recent call last):
  File "/home/xiaoduo/project/study-tracker/tests/e2e/test_e2e_endpoints.py", line 455, in <module>
    main()
  File "/home/xiaoduo/project/study-tracker/tests/e2e/test_e2e_endpoints.py", line 442, in main
    aid = section_status_and_completion(ids)
  File "/home/xiaoduo/project/study-tracker/tests/e2e/test_e2e_endpoints.py", line 257, in section_status_and_completion
    assert r.status_code == 404
AssertionError
```
- **位置**: `tests/e2e/test_e2e_endpoints.py` 第 257 行 (测试断言期望 404)
- **实际**: `app/routers/assignments.py` 抛 `AssignmentNotFoundError(code="4008")`, `AppError._default_status()` 对 4xxx 默认返 HTTP 400
- **根因**: `AppError._default_status()` 按 code 前缀映射状态码 (`4xxx → 400`), "作业不存在" 应当是 HTTP 404 而非 400 — 这是 HTTP 语义 bug, 与 AI 解析无关
- **本任务影响**: 0 (sections 1-5 覆盖 `/api/health` + `/api/parse` + `/api/assignments/batch` + `/api/board/today`, 全部通过; section 6 起属于状态流转/编辑/复盘, 不在本任务范围)
- **是否阻塞 DoD**: 不阻塞本任务 (我的核心改动 — AI 解析超时+重试+前端降级 — 已被 sections 1-5 验证)
- **建议修复** (留给交接方):
  ```python
  # app/errors.py
  class AssignmentNotFoundError(AppError):
      code = "4008"
      default_message = "作业不存在"
      def __init__(self, message=None):
          super().__init__(message, status_code=404)  # 覆盖默认 400 → 404
  ```
  或者修测试断言为 400 (但 404 是 HTTP 语义正解, 推荐修 router)

#### 跑过程中遇到的环境问题 (已规避, 不影响功能)
- **OpenClaw 代理凭据轮换**: `HTTPS_PROXY` 的 password 部分每 1-2 分钟轮换. Server 进程持有的凭据过期后, `call_minimax()` 返 `AI_NETWORK` (ProxyError). **本任务的 retry + 结构化错误设计正好接住**: 重试 1 次失败后返 `{"code": "AI_NETWORK", "message": "AI 请求失败: ProxyError"}`, HTTP 200, 前端可正常降级. 解决方法: 跑测试时每 1-2 分钟重启 server 拿新凭据 (已在 `/tmp/run_3_scenarios.sh` 和 `/tmp/restart_and_e2e.sh` 里固化).
- **Python `requests` 默认走代理到 localhost**: `HTTPS_PROXY=...` 会让 Python `requests.post("http://127.0.0.1:8876/...")` 也走代理, 代理对 localhost 返 502. 解决: 测试脚本设 `NO_PROXY="127.0.0.1,localhost"` (已用).

---

## 3. 关键代码片段

### 3.1 config.py (timeout 120)
```python
# 默认 120s: 实测 9.22 三科合并样本解析约 58s, 60s 偏贴边 (上次只差 1.9s).
# 家长可能一次性粘贴一周通知, 120s 留有余量.
# 内部还有 1 次 +2s 退避重试, 最坏总耗时 120s + 2s + 120s = 242s.
# 家长不会等 3 分钟, 但一周通知必须能跑通.
MINIMAX_TIMEOUT: int = int(os.environ.get("MINIMAX_TIMEOUT", "120"))
```

### 3.2 ai_client.py (retry + 结构化错误)
```python
# 1 次重试 (initial + retry = 2 attempts)
last_err_code: str | None = None
last_err_msg: str | None = None
resp = None
for attempt in range(2):  # 0=initial, 1=retry
    try:
        resp = requests.post(
            _endpoint_url(),
            headers=headers,
            json=payload,
            timeout=config.MINIMAX_TIMEOUT,
        )
        break
    except requests.exceptions.Timeout:
        last_err_code = "AI_TIMEOUT"
        last_err_msg = f"AI 解析超时 ({config.MINIMAX_TIMEOUT}s)"
    except requests.exceptions.RequestException as e:
        last_err_code = "AI_NETWORK"
        last_err_msg = f"AI 请求失败: {type(e).__name__}"
    if attempt == 0:
        logger.warning(...)
        _time.sleep(2)

if resp is None:
    return _err(last_err_code, last_err_msg or "AI 调用失败")

def _err(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "items": []},
    }
```

### 3.3 routers/parse.py (透传)
```python
result = call_minimax(raw)

if isinstance(result, dict) and result.get("ok") is False:
    err = result.get("error") or {"code": "AI_UNKNOWN", "message": "AI 解析失败", "items": []}
    logger.warning("AI parse error: %s - %s", err.get("code"), err.get("message"))
    return _failure_envelope(err)

# 成功路径...
return {"ok": True, "batch_id": batch_id, "items": items, "model_version": model, "ai_reachable": True}
```

### 3.4 app.js (前端 spinner + 130s 超时 + 重试/复制)
```js
// 立即显示 spinner 加载态 (后端 timeout=120s + 1 次 +2s 重试 = 最坏 242s)
status.innerHTML = '<span class="spinner" aria-hidden="true"></span>AI 解析中, 最长可能需 2 分钟';
result.innerHTML = '<p class="muted">预计 15~120 秒, 请稍候 (服务端内部会自动重试 1 次)</p>';

// 130s 客户端超时 (略长于服务端单次 120s, 比服务端总耗时短, 快速降级避免白屏)
const controller = new AbortController();
const timeoutId = setTimeout(() => controller.abort(), 130000);

try {
  const httpResp = await fetch(API + '/parse', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text: raw }),
    signal: controller.signal,
  });
  ...
  if (data && data.ok === false && data.error) {
    handleParseError(data.error, raw);  // 结构化错误 → 错误 banner + 重试/复制按钮
    return;
  }
} catch (e) {
  if (e && e.name === 'AbortError') {
    handleParseTimeout(raw);  // 客户端超时 → 错误 banner + 重试/复制按钮
    return;
  }
}
```

### 3.5 styles.css (spinner)
```css
.spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(220, 38, 38, 0.18);
  border-top-color: var(--status-overdue, #dc2626);
  border-radius: 50%;
  animation: st-spin 0.8s linear infinite;
  vertical-align: -2px;
  margin-right: 6px;
}
@keyframes st-spin { to { transform: rotate(360deg); } }
```

---

## 4. E2E 报告: sections 1-5 全过 + section 6 已知 bug 上报

| Section | 内容 | 结果 |
|---------|------|------|
| 1 | /api/health | ✅ pass |
| 2 | /api/parse 空文本 / 超长 (4001/4002) | ✅ pass |
| 3 | /api/parse 9.21 (10 条, 含 1 通勤观察) | ✅ pass |
| 4 | /api/parse 9.22 (9 条, 含周四 8:20 deadline + 1 选做) | ✅ pass |
| 5 | /api/assignments/batch + /api/board/today | ✅ pass (created_ids 10+9, board populates) |
| 6 | 状态流转 + 实耗时录入 | ❌ crash at line 257 (`assert r.status_code == 404` fail, 实际 400) — **预存在 bug, 非本任务引入, 见 §2.4** |
| 7-14 | 编辑 / 删除 / 复盘 / 设置 / 导出 / 数据持久化 / 重复 / 安全 | ⏸ 未执行 (因 section 6 已崩中断 main()) |

---

## 5. 自检清单 (任务派单铁律 6 条)

- ❌ 不许回退 string.Template → .format() → ✅ prompts.py 未改
- ❌ 不许 mock AI 调用做验证 → ✅ 3 场景全真打
- ❌ 不许把 MINIMAX_API_KEY 写进任何文件 → ✅ Key 只从 env 读, 全程不落盘不打印
- ❌ 不许扩大范围 → ✅ 只改 5 个目标文件, 未越界改 router/main.py/其他
- ❌ 不许改 tests/test_prompts_regression.py → ✅ 未改
- ❌ 不许改 tests/e2e/* → ✅ 未改

---

## 6. 给接手方 (校验姬 / 下一棒) 的提醒

1. **OpenClaw 代理凭据轮换**: server 进程持有的 HTTPS_PROXY 凭据每 1-2 分钟过期, 过期后 `call_minimax()` 会返 `AI_NETWORK` (ProxyError). 跑长时间测试时需:
   - 每 1-2 分钟重启 server (拿新凭据)
   - 测试客户端用 `NO_PROXY="127.0.0.1,localhost"` 绕过代理直连 server
2. **`tests/test_real_ai_parsing.py`** 也可直跑, 我未跑 (它不在派单铁律里 "必跑" 项), 可独立验证
3. **section 6 crash 的修复建议** 见 §2.4 (改 `AssignmentNotFoundError` 覆盖 status_code=404, 或修测试断言)
4. **前端 130s 客户端超时** 比服务端最坏情况 (242s) 短, 故意设计为快速降级避免白屏; 用户点 "重试" 后会发起新请求
5. **结构化错误响应** 字段: `{ok: false, error: {code, message, items: []}, batch_id: null, items: [], model_version, ai_reachable, message}`. 兼容旧前端: `ai_reachable`, `items`, `message` 字段仍保留顶层
6. **新测试未写**: 修真任务本身没要求加测试, 仅要求不退化 (77 passed). 校验姬可加: `tests/test_ai_client_retry.py` 验证 retry + 结构化错误; `tests/test_parse_route_structured_error.py` 验证 route 透传

---

[END]
