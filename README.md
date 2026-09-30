# 作业小助手 · study-tracker

本地部署、单用户的小学生作业跟踪工具。家长把老师发的家长群通知**原文粘贴**进来，AI 自动拆成「科目 / 任务 / 归属日 / 截止时间 / 预计用时」清单，逐条确认后入库。看板按日看完成情况，日历回看历史，周复盘做预估偏差分析。

- **架构**：FastAPI + SQLite + 原生 HTML/CSS/JS。不引入前端框架 / ORM / Docker。
- **数据**：全部留在本机 `data/study.db`，不上传任何服务器。
- **AI**：MiniMax 国内站，Key 走环境变量。**Key 缺失时 AI 解析不可用，其余功能全部正常。**

---

## 1. 功能组成

五个页面，每个页面有独立 URI，可直接打开 / 收藏 / 分享：

| URI | 页面 | 作用 |
|---|---|---|
| `#/board` | 看板 | 默认页。按日展示进度条、待完成 / 已收尾 / 选做三个分区，操作作业状态 |
| `#/entry` | 登记 | 粘贴通知原文 → AI 解析 → 预览表逐条确认 → 批量入库 |
| `#/list` | 日历 | 月历视图，点日期看当天详情，同样可操作状态 |
| `#/weekly` | 复盘 | 每周预估 vs 实际用时偏差、正确率统计 |
| `#/settings` | 设置 | 各科预估用时、作业类型基线、放学/晚餐时间等 |

> 直接访问 `http://127.0.0.1:8876/board`（不带 `#`）也能打开对应页，后端做了同路径兜底。

### 核心流程

```
粘贴老师通知 → AI 拆分 → 预览表确认 → 入库 → 看板/日历执行 → 复盘偏差
```

### 作业状态

| 状态 | 含义 |
|---|---|
| `todo` | 待完成 |
| `done` | 完成 |
| `skipped` | 跳过（算已收尾，但不算完成） |

`is_optional` 标记选做。**进度条的分子分母都只算必做**——选做不参与进度统计，避免它把进度条永久压住走不满。

### 页面能力

**看板**：进度条、今日待办数、待完成 / 已收尾 / 选做分区、实耗时录入、截止时间提醒、最近 3 个有记录日期的快捷切换。选做分组默认展开、浅灰底色。

**登记**：粘贴原文后 AI 解析成表格，含科目、选做、预计用时、置信度、原文片段，可逐条勾选、改状态、删草稿行。原文弹窗可看 AI 依据的原文。

**日历**：月历按月汇总，一次请求返回整月每日统计（必做 / 选做分开计数），点日期看详情。必做全完成显示绿色。

**复盘 / 设置**：见上表。

---

## 2. 环境要求

| 项 | 值 |
|---|---|
| OS | WSL2 / Linux / macOS |
| Python | 3.12+ |
| 依赖 | `fastapi` `pydantic` `requests` `uvicorn`（见 `requirements.txt`） |
| Node | 运行服务不需要。只有跑前端集成测试（jsdom）时才需要，见 §6 |

> WSL2 用户的 pip 可能报 `externally-managed-environment`，加 `--break-system-packages`，或用虚拟环境（见下）。

---

## 3. 部署

```bash
# 1. 装依赖
pip install --break-system-packages -r requirements.txt

# 或者用虚拟环境（推荐，不污染系统 Python）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 之后所有命令都在这个环境里跑，或用 .venv/bin/python 代替 python3

# 2. 准备配置（不配也能跑，只是 AI 不可用）
cp .env.example .env
$EDITOR .env          # 填 MINIMAX_API_KEY，见第 4 节

# 3. 启动
bash service.sh start
```

首次启动会自动建库、建表并写入默认设置，数据落在 `data/study.db`。

---

## 4. 配置

配置有两个来源：**系统环境变量优先**，其次 `.env`（项目根目录，已 gitignore）。

`.env.example` 是完整模板，逐项说明：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MINIMAX_API_KEY` | 空 | **必填**才有 AI。从 [MiniMax 控制台](https://api.minimaxi.com) 获取。留空则 AI 解析禁用，手动录入仍可用 |
| `MINIMAX_MODEL` | `MiniMax-M3` | 模型版本 |
| `MINIMAX_API_BASE` | `https://api.minimaxi.com` | 国内站。国际站换成对应域名 |
| `MINIMAX_TIMEOUT` | `120` | 单次调用超时（秒）。内部还有 1 次退避重试，最坏约 `120 + 2 + 120 = 242s` |
| `HOST` | `127.0.0.1` | 监听地址。**改成 `0.0.0.0` 才能被局域网其他设备访问** |
| `PORT` | `8876` | 监听端口。与 `run.sh` / `service.sh` / `app/config.py` 一致。**8765 已被 serve_gallery.py 占用，不要改回** |
| `STUDY_DB_PATH` | `./data/study.db` | 数据库位置。相对路径相对项目根解析，也可填绝对路径 |

`.env` 解析规则：按 `KEY=VALUE` 逐行，跳过空行与 `#` 注释，不支持引号嵌套；**已存在的系统环境变量不会被 `.env` 覆盖**。

> 关于 `thinking` 深度：作业拆分只是结构化抽取，不需要深度推理，请求里固定带 `thinking: {"type": "disabled"}` 关闭思考，实测延迟从 2.81s 降到 1.22s、token 从 337 降到 144。`reasoning_effort=low` 那种降档参数属于 M3.1-Flash-Preview，**MiniMax-M3 不支持**，传了会被静默忽略。

---

## 5. 启动 / 停止

推荐用 `service.sh` 管理。它用 `setsid` 让服务脱离终端，关掉终端或断开 SSH 都不会带走服务。

```bash
bash service.sh start              # 后台启动，默认 127.0.0.1:8876
bash service.sh status             # 运行状态 + 健康检查
bash service.sh logs --lines 100   # 看最近日志
bash service.sh restart            # 重启
bash service.sh stop               # 优雅停止（先 SIGTERM，10s 超时才升级 SIGKILL）

bash service.sh start --port 9000  # 换端口
bash service.sh start --host 0.0.0.0   # 允许局域网访问
bash service.sh start --foreground # 前台跑，Ctrl+C 退出
bash service.sh --help             # 全部参数
```

不带参数直接执行 `bash service.sh` 等同于 `status`。

### 前台启动（调试用）

```bash
bash run.sh                        # 默认 127.0.0.1:8876
bash run.sh --port 9000
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8876
```

### 运行时产物

都在 `run/`（已 gitignore）：

| 文件 | 内容 |
|---|---|
| `run/service.pid` | 进程 PID |
| `run/service.env` | 实际运行的 HOST / PORT |
| `run/service.log` | stdout + stderr |

`service.sh` 有两处安全设计：停止前会校验 PID 确实是本项目的 uvicorn 进程，避免 PID 被系统回收后误杀无关进程；启动后会等健康检查通过才算成功。

### 局域网访问

```bash
bash service.sh start --host 0.0.0.0 --port 8876
# 另一台设备访问 http://<本机IP>:8876/
```

> ⚠️ 服务**没有任何鉴权**，改 `0.0.0.0` 等于把数据开放给同网段。仅在可信网络（如家里 WiFi）这么做，且不要暴露到公网。
>
> 另：WSL2 下若 shell 里有 `HTTPS_PROXY`，Python `requests` 会把 `127.0.0.1` 的请求也发给代理并收到 502。访问本机服务需设 `NO_PROXY=127.0.0.1,localhost`（`service.sh` 已内置）。

---

## 6. 测试

```bash
# 1. 先装前端集成测试依赖 (仅 jsdom, 一次即可; 依赖声明在 package.json)
npm install

# 2. 跑测试
python3 -m pytest tests/ -q

# 单独跑纯 JS 回归用例 (不需要 jsdom, 只用 Node 内置模块)
npm run test:js
```

`tests/conftest.py` 会为每个测试分配独立临时数据库并默认关闭真实 AI 调用，所以跑测试不会动你的数据、也不会消耗 token。

覆盖范围：AI Prompt 与 JSON 解析、解析接口、入库与去重、看板与进度口径、月历汇总、状态流转、设置、前端渲染与路由（Node + jsdom）。

> **为什么 `package.json` 要入库**：jsdom 用例是 `pytest.mark.skipif` 保护的 —— 没装 jsdom 时它们**静默 skip**，pytest 依然显示全绿，但前端断言一条都没跑。依赖声明入库后，`npm install` 一步就能补齐。若看到 `-rs` 输出里成片的 `SKIPPED ... 需要 node 与 jsdom`，就是这一步没做。

> `tests/e2e/test_real_ai_parsing.py` 是**手工脚本不是 pytest 用例**（文件内无 `test_` 函数，pytest 收集到 0 条）——它会真打 AI 并消耗额度，故刻意不转成 pytest。需真实打 AI 时用 `python3 tests/e2e/test_real_ai_parsing.py` 手工跑。

---

## 7. API 速查

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查（`ai_reachable` 仅表示 Key 已配置，不会真发请求） |
| POST | `/api/parse` | AI 解析通知原文 |
| POST | `/api/assignments/batch` | 批量入库 |
| GET | `/api/assignments?date=YYYY-MM-DD` | 按日期查 |
| GET | `/api/assignments/calendar?year=&month=` | 按月汇总（必做/选做分开计数） |
| PATCH | `/api/assignments/{id}/status` | 切换状态 |
| PATCH | `/api/assignments/{id}/completion` | 录实际用时 |
| PATCH | `/api/assignments/{id}/optional` | 改选做标记 |
| PATCH | `/api/assignments/{id}` | 编辑字段 |
| DELETE | `/api/assignments/{id}` | 删除 |
| GET | `/api/board/today` | 今日看板 |
| GET | `/api/board?date=YYYY-MM-DD` | 指定日期看板 |
| GET | `/api/board/dates?limit=3` | 最近 N 个日期, **"今天"永远在第一位** (无作业时为 0/0 空卡) |
| GET | `/api/analytics/weekly?week=current\|last` | 周复盘 |
| GET | `/api/settings` | 读设置 |
| PATCH | `/api/settings/{key}` | 改设置 |
| GET | `/api/export` | 导出全部数据（JSON 下载） |

交互式文档：`http://127.0.0.1:8876/docs`（FastAPI 自带）。

错误响应统一为 `{"error": {"code": "...", "message": "..."}}`。错误码前缀：`1xxx` 输入错误 / `4xxx` 业务规则 / `5xxx` 服务端异常。

---

## 8. 目录结构

```
study-tracker/
├── app/
│   ├── main.py           # FastAPI 入口 + 静态资源 + 前端路径兜底
│   ├── config.py         # 环境变量与默认配置
│   ├── db.py             # SQLite 连接与建表
│   ├── models.py         # pydantic DTO
│   ├── prompts.py        # AI Prompt 模板
│   ├── ai_client.py      # MiniMax 调用 + JSON 健壮解析
│   ├── errors.py         # 统一错误响应
│   ├── seed.py           # 启动时写入默认设置
│   ├── services/         # 业务逻辑（作业/复盘/导出/设置/归一化）
│   ├── routers/          # API 路由
│   └── static/
│       ├── index.html
│       ├── app.js        # 前端逻辑 + hash 路由
│       ├── styles.css
│       └── vendor/       # flatpickr 4.6.13（本地，不走 CDN）
├── tests/                # pytest
├── data/study.db         # 运行时生成，不入库
├── run/                  # PID / 日志，不入库
├── docs/                 # 功能设计、页面设计、交接说明
├── .env.example
├── requirements.txt
├── run.sh                # 前台启动
├── service.sh            # 服务管理
└── README.md
```

---

## 9. 数据与安全

- `MINIMAX_API_KEY` 只从环境变量 / `.env` 读，**不入代码、不打印、不出现在日志或错误响应里**
- `.env`、`data/study.db`、`run/`、`tmp/`、`artifacts/` 均已 gitignore
- 健康检查只返回 `ai_reachable: bool`
- 数据全在本机，除 AI 解析请求外无任何外发

**备份**：直接复制 `data/study.db`。或访问 `/api/export` 导出 JSON。

**恢复**：把备份文件放回 `data/study.db`（先停服务），或用 sqlite3 导入。

---

## 10. 故障排查

| 症状 | 处理 |
|---|---|
| 启动提示 `MINIMAX_API_KEY 未配置` | 是警告不是错误，服务正常启动，仅 AI 解析不可用 |
| `/api/health` 返回 `ai_reachable: false` | 同上 |
| `pip install` 报 externally-managed | 加 `--break-system-packages`，或用 venv |
| 端口被占 | `bash service.sh start --port 9000` |
| 进度条数字看着不对 | 进度分子分母都只算必做；选做单独统计。若出现 `settled > required` 那是不变式被破坏，属 bug |
| WSL2 访问 502 | 代理拦截，设 `NO_PROXY=127.0.0.1,localhost` |
| 数据库锁死 | 关掉所有可能占用 DB 的进程 |

日志：`bash service.sh logs --lines 200` 或看 `run/service.log`。

---

## 11. v1 边界

不做账号、不做多用户、不做推送通知、不做图表、不做 OCR、不做番茄钟。设计取舍见 `docs/01-功能设计.md`。
