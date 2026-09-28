# 小学生学习跟踪平台 · study-tracker v1

本地部署、单用户的小学生作业跟踪。家长把老师发的家长群通知原文粘贴进来,AI 自动拆成「科目 / 任务 / 日期 / 截止时间 / 预估时长」清单,家长逐条确认后入库。看板按日查看完成情况,周复盘做预估偏差分析。

**架构**: FastAPI + SQLite + 原生 HTML/CSS/JS。**不**引入前端框架 / ORM / Docker。

**AI**: MiniMax 国内站 (`https://api.minimaxi.com`),Key 走环境变量 `MINIMAX_API_KEY`。Key 缺失时 AI 解析不可用,但手动录入、看板、打卡、复盘、导出全部正常。

---

## 1. 环境

| 项 | 值 |
|---|---|
| OS | WSL2 / Linux |
| Python | 3.12.3 (系统 Python, 装包需 `--break-system-packages`) |
| Node | v26.8.1 (本项目不依赖) |
| 已装依赖 | `fastapi 0.135.2` / `pydantic 2.12.5` / `requests 2.33.1` / `uvicorn 0.42.0` |

---

## 2. 安装

```bash
# 1. 装依赖 (若未装)
pip install --break-system-packages -r requirements.txt

# 2. 复制环境变量模板
cp .env.example .env
# 编辑 .env 填入 MINIMAX_API_KEY (可选, 留空则 AI 不可用, 手动可用)
```

---

## 3. 启动

推荐用 `service.sh` 管理服务 (后台启动 / 查看状态 / 优雅停止 / 看日志):

```bash
bash service.sh start                # 后台启动, 默认 127.0.0.1:8876
bash service.sh status               # 运行状态 + 健康检查
bash service.sh logs --lines 100     # 看最近日志
bash service.sh restart              # 重启
bash service.sh stop                 # 优雅停止 (先 SIGTERM, 超时才 SIGKILL)

bash service.sh start --port 9000    # 换端口
bash service.sh start --foreground   # 前台跑, Ctrl+C 退出
bash service.sh --help               # 全部参数
```

服务默认以 `setsid` 脱离终端, 关掉终端 / 断开 SSH 都不会带走服务。
PID 与日志落在 `run/` (已 gitignore): `run/service.pid`、`run/service.env`、`run/service.log`。

`service.sh` 会自动从 `~/.bashrc` 读取 `MINIMAX_API_KEY` 注入进程内存 (不落盘、不打印);
若存在 `.env` 则以 `.env` 为准。

### 手动启动 (等价于 `service.sh start --foreground`)

```bash
bash run.sh                          # 默认监听 127.0.0.1:8765
bash run.sh --port 9000              # 自定义端口
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

> ⚠️ WSL2 环境下, 若 shell 里存在 `HTTPS_PROXY`, Python `requests` 会把
> `127.0.0.1` 的请求也发给代理并收到 502。访问本机服务时设置
> `NO_PROXY=127.0.0.1,localhost` 即可 (`service.sh` 已内置)。

---

## 4. 测试

```bash
python3 -m pytest tests/ -v
```

测试套件覆盖:
- AI Prompt 单元 (JSON 健壮解析、字段约束)
- 解析接口 (真实 9.21 / 9.22 通知样本, mock 掉外部 HTTP)
- 入库、看板、打卡、复盘、设置、导出全链路

**真实 AI 调用** (消耗 token, 可选):

```bash
# 配好 MINIMAX_API_KEY 后
python3 tests/integration_real_ai.py
```

---

## 5. API 速查

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查, 含 AI 是否可达 |
| POST | `/api/parse` | AI 解析通知原文 |
| POST | `/api/assignments/batch` | 批量入库 |
| GET | `/api/board/today` | 今日看板 |
| GET | `/api/assignments?date=YYYY-MM-DD` | 按日期查 |
| PATCH | `/api/assignments/{id}/status` | 切换状态 (todo/doing/done/skipped) |
| PATCH | `/api/assignments/{id}/completion` | 录实际分钟 |
| PATCH | `/api/assignments/{id}/optional` | 改选做标记 |
| PATCH | `/api/assignments/{id}` | 编辑字段 |
| DELETE | `/api/assignments/{id}` | 删除 (已完成禁止) |
| GET | `/api/analytics/weekly?week=current\|last` | 周复盘偏差分析 |
| GET | `/api/settings` | 读设置 |
| PATCH | `/api/settings/{key}` | 改设置 |
| GET | `/api/export` | 导出全部数据 (JSON 下载) |

错误响应统一格式: `{"error": {"code": "STR_CODE", "message": "..."}}`。
错误码前缀: `1xxx` 输入错误 / `4xxx` 业务规则 / `5xxx` 服务端异常。完整见 `docs/01-功能设计.md` 第 6.2 节。

---

## 6. 目录结构

```
study-tracker/
├── app/                  # 后端
│   ├── main.py           # FastAPI 入口
│   ├── config.py         # 环境变量 + 默认配置
│   ├── db.py             # SQLite 连接 + 建表
│   ├── models.py         # pydantic DTO
│   ├── prompts.py        # AI Prompt 模板
│   ├── ai_client.py      # MiniMax API 调用 + JSON 健壮解析
│   ├── errors.py         # 统一错误响应
│   ├── seed.py           # 启动时写入默认设置
│   ├── services/         # 业务逻辑层
│   ├── routers/          # API 路由
│   └── static/           # 朴素可用前端 (HTML/CSS/JS)
├── tests/
│   ├── fixtures/         # 真实通知样本
│   └── test_*.py         # 单元 / 集成测试
├── data/
│   ├── .gitkeep          # 占位 (DB 文件不入库)
│   └── study.db          # 运行时自动生成
├── run/                  # 服务运行时 (PID/日志, 不入库)
├── .env.example
├── .gitignore
├── requirements.txt
├── run.sh                # 前台启动
├── service.sh            # 服务管理 (start/stop/restart/status/logs)
└── README.md
```

---

## 7. 密钥与安全

- `MINIMAX_API_KEY` **只从环境变量读**, 不入任何代码文件
- `.env` 已加入 `.gitignore`, 禁止入库
- 代码 / 日志 / 错误响应里**绝不出现** Key 内容
- 健康检查接口只返回 `ai_reachable: bool`, 不返回 Key 任何信息
- 不发送 `response_format` 给 MiniMax (M3 不支持), 全靠客户端 `_strip_fences` + 正则抓取容错解析

---

## 8. 数据导出 / 恢复

```bash
# 浏览器访问 /api/export 下载 JSON, 文件名 study-tracker-export-YYYYMMDD-HHmm.json
# 恢复: 用 sqlite3 .import 或写个小脚本 INSERT
```

---

## 9. v1 边界 (Non-goals)

不做账号、不做多用户、不做推送通知、不做图表、不做 OCR、不做番茄钟。完整见功能设计 §10。

---

## 10. 故障排查

| 症状 | 排查 |
|---|---|
| 启动报 `MINIMAX_API_KEY not configured` | 是 warn 不是异常, 仍能启动, 仅 AI 解析不可用 |
| `GET /api/health` 返回 `ai_reachable: false` | 同上 |
| `pip install` 报 externally-managed | 加 `--break-system-packages` |
| 端口被占 | `bash run.sh --port 9000` |
| 数据库锁死 | 关掉所有打开 DB 的进程 |