"""孩子端星星 / 勋章 / 家长 PIN (双角色改造 · 后端).

要锁住的口径:

1. **发星幂等.** 同一条作业只能发一次星 —— 靠 partial unique index 兜底。
   应用层的"先查后插"在重复 PATCH 下必然漏 (两个请求都看到"还没发过"),
   唯一索引才是唯一的裁判。
2. **撤销即扣回.** 从 done 退回 todo 时那颗星要消失, 不是多一行负数 ——
   负数行会永久留在孩子的账本里。
3. **连续天数与事件顺序无关.** 先补昨天再完成今天, 或者反过来,
   streak 都应该是 2。
4. **PIN 不落明文.** 库里存的是 pbkdf2_sha256$... 的哈希串。
5. **勋章从数据算.** 满足条件才出现在 items, 不满足的在 locked。
6. **标签只认 6 个 code**, 非法 code 走 4003。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from app import config


def _today() -> date:
    return datetime.now().astimezone().date()


def _yesterday() -> date:
    return _today() - timedelta(days=1)


def _week_days(n: int) -> list[date]:
    """当前周 (周一起算) 的前 n 天.

    勋章的"一周"口径是自然周, 所以要本周内的日期 —— 用"今天往前数 n 天"
    会在周一/周二跨到上一周去, 测试变成"看今天是星期几"的抽奖。
    """
    monday = _today() - timedelta(days=_today().weekday())
    return [monday + timedelta(days=i) for i in range(n)]


def _add(app_client, due: str, task_name: str = "晚辅任务", subject: str = "语文",
         est_minutes: int = 20, is_optional: bool = False) -> int:
    """登记一条作业, 返回 id."""
    payload = {
        "batch_id": None,
        "items": [{
            "subject": subject,
            "task_name": task_name,
            "due_date": due,
            "is_optional": is_optional,
            "est_minutes": est_minutes,
            "task_type": "背诵",
            "source_span": "(测试)",
            "confidence": "high",
        }],
    }
    r = app_client.post("/api/assignments/batch", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["created_ids"][0]


def _complete(app_client, assignment_id: int) -> dict:
    r = app_client.patch(f"/api/assignments/{assignment_id}/status", json={"status": "done"})
    assert r.status_code == 200, r.text
    return r.json()


def _revert(app_client, assignment_id: int) -> dict:
    r = app_client.patch(f"/api/assignments/{assignment_id}/status", json={"status": "todo"})
    assert r.status_code == 200, r.text
    return r.json()


def _stars(app_client) -> dict:
    r = app_client.get("/api/kid/stars")
    assert r.status_code == 200, r.text
    return r.json()


def _ledger_rows() -> list[dict]:
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT id, star_date, stars, reason, note, assignment_id FROM kid_star_ledger "
            "ORDER BY id"
        ).fetchall()


# ====== 1. 发星与幂等 ======

def test_complete_assignment_awards_one_star(app_client):
    """完成一条作业 +1 星. 必做留一条没做完, 所以不触发全完成奖励."""
    today = _today().isoformat()
    _add(app_client, today, "语文作业")
    keep = _add(app_client, today, "数学作业")

    aid = _add(app_client, today, "英语作业")
    _complete(app_client, aid)

    stars = _stars(app_client)
    assert stars["balance"] == 1
    assert stars["today_earned"] == 1
    assert stars["jar"]["stars"] == 1
    assert stars["jar"]["cap"] == 20
    assert stars["jar"]["date"] == today
    assert keep  # 另一条还没做


def test_repeat_patch_does_not_award_second_star(app_client):
    """重复 PATCH 同一作业不会发第二颗星 (幂等)."""
    today = _today().isoformat()
    _add(app_client, today, "数学作业")
    aid = _add(app_client, today, "语文作业")

    _complete(app_client, aid)
    _complete(app_client, aid)      # 重复完成
    _complete(app_client, aid)      # 再来一次

    stars = _stars(app_client)
    assert stars["balance"] == 1
    rows = [r for r in _ledger_rows() if r["assignment_id"] == aid]
    assert len(rows) == 1
    assert rows[0]["stars"] == 1
    assert rows[0]["reason"] == "earn_task"


def test_revert_from_done_revokes_the_star(app_client):
    """从 done 退回 todo 撤销那颗星, 余额回到 0."""
    today = _today().isoformat()
    _add(app_client, today, "数学作业")
    aid = _add(app_client, today, "语文作业")

    _complete(app_client, aid)
    assert _stars(app_client)["balance"] == 1

    _revert(app_client, aid)
    stars = _stars(app_client)
    assert stars["balance"] == 0
    assert stars["today_earned"] == 0
    # 撤销是"删掉那一行", 不是补一行负数
    assert [r for r in _ledger_rows() if r["assignment_id"] == aid] == []


# ====== 2. 全完成奖励与连续天数 ======

def test_all_required_done_gives_bonus_and_streak(app_client):
    """当天必做全完成: +1 颗 + 3 颗全完成奖励, streak = 1."""
    today = _today().isoformat()
    aid = _add(app_client, today, "语文作业")

    _complete(app_client, aid)

    stars = _stars(app_client)
    assert stars["balance"] == 4          # 1 (完成) + 3 (全完成)
    assert stars["today_earned"] == 4
    assert stars["streak"]["current"] == 1
    assert stars["streak"]["best"] == 1
    assert stars["streak"]["last_date"] == today

    reasons = sorted(r["reason"] for r in _ledger_rows())
    assert reasons == ["bonus_all_done", "earn_task"]


def test_bonus_all_done_only_once_per_day(app_client):
    """全完成奖励每天只发一次: 再完成另一条也不重复发."""
    today = _today().isoformat()
    a = _add(app_client, today, "语文作业")
    b = _add(app_client, today, "数学作业")

    _complete(app_client, a)
    _complete(app_client, b)

    bonuses = [r for r in _ledger_rows() if r["reason"] == "bonus_all_done"]
    assert len(bonuses) == 1
    # 余额 = 两条作业 2 星 + 一次 3 星奖励
    assert _stars(app_client)["balance"] == 5


def test_revert_revokes_the_all_done_bonus_too(app_client):
    """撤销一条完成 -> 当天不再"必做全完成" -> 那 3 颗全勤奖要收回来.

    全勤奖是**条件奖**: 条件不成立了还留着, 等于孩子白拿 3 颗星。
    口径跟"撤销完成就删那颗星"一致 —— 删掉那一行, 不补负数行。
    """
    today = _today().isoformat()
    a = _add(app_client, today, "语文作业")
    b = _add(app_client, today, "数学作业")

    _complete(app_client, a)
    _complete(app_client, b)
    assert _stars(app_client)["balance"] == 5      # 2 颗 + 3 颗全勤奖

    _revert(app_client, b)

    stars = _stars(app_client)
    assert stars["balance"] == 1                  # 只剩 a 的那颗
    assert stars["today_earned"] == 1
    assert stars["streak"]["current"] == 0        # 全勤没了, 连续天数归零
    # 全勤奖那一行是被删掉的, 不是补了一行 -3
    assert [r for r in _ledger_rows() if r["reason"] == "bonus_all_done"] == []
    assert sorted(r["stars"] for r in _ledger_rows()) == [1]


def test_two_consecutive_days_gives_streak_two(app_client):
    """连续两天完成 -> streak = 2, best = 2.

    每天只留一条必做并完成它 —— 连续天数的前提是"当天必做全部完成",
    留一条没做的, 那天就不算完成。
    """
    aid_y = _add(app_client, _yesterday().isoformat(), "昨天的作业")
    aid_t = _add(app_client, _today().isoformat(), "今天的作业")

    _complete(app_client, aid_y)
    _complete(app_client, aid_t)

    stars = _stars(app_client)
    assert stars["streak"]["current"] == 2
    assert stars["streak"]["best"] == 2


def test_streak_is_independent_of_event_order(app_client):
    """先完成今天再补昨天, streak 仍然是 2 (连续天数从数据算)."""
    aid_y = _add(app_client, _yesterday().isoformat(), "昨天的作业")
    aid_t = _add(app_client, _today().isoformat(), "今天的作业")

    _complete(app_client, aid_t)
    _complete(app_client, aid_y)

    stars = _stars(app_client)
    assert stars["streak"]["current"] == 2
    assert stars["streak"]["best"] == 2


def test_streak_breaks_when_a_day_is_not_finished(app_client):
    """中间那天没做完 -> 连续天数断掉 (不是一直 +1)."""
    d = _today()
    aid_1 = _add(app_client, (d - timedelta(days=2)).isoformat(), "前天作业")
    aid_2 = _add(app_client, (d - timedelta(days=1)).isoformat(), "昨天作业")
    aid_3 = _add(app_client, d.isoformat(), "今天作业")

    _complete(app_client, aid_1)
    _complete(app_client, aid_3)
    # 昨天那条没做 -> 昨天不算完成, 今天是新的一段连续

    stars = _stars(app_client)
    assert stars["streak"]["current"] == 1
    assert stars["streak"]["best"] == 1
    assert aid_2


# ====== 3. GET /kid/stars 的结构 ======

def test_stars_response_shape(app_client):
    """字段名与规范 §5.2 一字不差."""
    today = _today().isoformat()
    _add(app_client, today, "语文作业")
    aid = _add(app_client, today, "数学作业")
    _complete(app_client, aid)

    data = _stars(app_client)
    assert set(data) == {"balance", "today_earned", "jar", "streak", "ledger", "week"}
    assert set(data["jar"]) == {"date", "stars", "cap"}
    assert set(data["streak"]) == {"current", "best", "last_date"}
    assert set(data["week"]) == {"stars", "days_active", "tasks_done"}
    assert isinstance(data["balance"], int)
    assert isinstance(data["today_earned"], int)
    assert data["week"]["tasks_done"] == 1
    assert data["week"]["days_active"] == 1

    row = data["ledger"][0]
    # note = 家长写给人看的说明 (迁移 v6 补的列), 机器发星时为空。
    assert set(row) == {"id", "date", "stars", "reason", "note", "assignment_id", "created_at"}
    assert row["stars"] == 1
    assert row["reason"] == "earn_task"
    assert row["note"] is None
    assert row["assignment_id"] == aid


# ====== 4. 手动奖励 / 扣除 ======

def test_grant_and_redeem(app_client):
    """家长手动奖励 +5, 手动扣除 -2, 余额跟着走."""
    r = app_client.post("/api/kid/stars/grant", json={"stars": 5, "reason": "主动复习"})
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 5
    assert r.json()["today_earned"] == 5

    r = app_client.post("/api/kid/stars/redeem", json={"stars": 2, "reason": "换贴纸"})
    assert r.status_code == 200, r.text
    assert r.json()["balance"] == 3

    reasons = sorted(r_["reason"] for r_ in _ledger_rows())
    assert reasons == ["bonus_manual", "spend"]

    # 家长写的说明要能存下来 (note 列, 迁移 v6)。此前接口收下了 reason
    # 又丢掉 —— 家长填了字却 nowhere 可见, 等于骗人。
    notes = {r_["reason"]: r_["note"] for r_ in _ledger_rows()}
    assert notes["bonus_manual"] == "主动复习"
    assert notes["spend"] == "换贴纸"


def test_manual_reward_note_reaches_ledger(app_client):
    """手动奖励的说明要出现在 /kid/stars 的账本里, 且 reason 仍是机器码."""
    app_client.post("/api/kid/stars/grant", json={"stars": 3, "reason": "主动整理错题"})
    rows = _ledger_rows()
    row = next(r_ for r_ in rows if r_["reason"] == "bonus_manual")
    assert row["note"] == "主动整理错题"
    # 机器码保持不变, 前端按 code 映射中文才不会乱
    assert row["reason"] == "bonus_manual"

    data = app_client.get("/api/kid/stars").json()
    assert data["ledger"][0]["note"] == "主动整理错题"


def test_redeem_more_than_balance_is_rejected(app_client):
    """星星不够时不能把余额扣成负数."""
    app_client.post("/api/kid/stars/grant", json={"stars": 2, "reason": ""})
    r = app_client.post("/api/kid/stars/redeem", json={"stars": 5, "reason": ""})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4019"
    assert _stars(app_client)["balance"] == 2


def test_grant_rejects_out_of_range(app_client):
    """stars 必须在 1..20."""
    r = app_client.post("/api/kid/stars/grant", json={"stars": 21, "reason": ""})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


# ====== 5. 家长 PIN ======

def test_pin_not_set_verify_returns_false(app_client):
    """没设过 PIN 时 verify 返回 ok=false (不是错误)."""
    r = app_client.get("/api/kid/pin/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}

    r = app_client.post("/api/kid/pin/verify", json={"pin": "1234"})
    assert r.status_code == 200
    assert r.json() == {"ok": False}


def test_pin_set_verify_ok_and_wrong_pin_4020(app_client):
    """设置 -> 输对 ok=true; 输错 4020; DB 里不存明文."""
    r = app_client.post("/api/kid/pin", json={"pin": "1234"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    assert app_client.get("/api/kid/pin/status").json() == {"enabled": True}

    r = app_client.post("/api/kid/pin/verify", json={"pin": "1234"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = app_client.post("/api/kid/pin/verify", json={"pin": "9999"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4020"

    # 库里只有哈希, 没有明文
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        row = conn.execute("SELECT pin_hash FROM parent_access").fetchone()
    assert row is not None
    assert row["pin_hash"].startswith("pbkdf2_sha256$")
    assert "1234" not in row["pin_hash"]
    assert row["pin_hash"].count("$") == 3


def test_pin_bad_format_rejected(app_client):
    """PIN 必须 4 位数字."""
    r = app_client.post("/api/kid/pin", json={"pin": "12a4"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_pin_clear(app_client):
    """清掉 PIN 后又回到"没设过"."""
    app_client.post("/api/kid/pin", json={"pin": "1234"})
    r = app_client.delete("/api/kid/pin")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert app_client.get("/api/kid/pin/status").json() == {"enabled": False}
    assert app_client.post("/api/kid/pin/verify", json={"pin": "1234"}).json() == {"ok": False}


# ====== 6. 勋章 ======

def test_medals_split_into_items_and_locked(app_client):
    """拿到第一颗星 -> first_star 进 items; 还没拿到的进 locked."""
    today = _today().isoformat()
    _add(app_client, today, "语文作业")
    aid = _add(app_client, today, "数学作业")
    _complete(app_client, aid)

    r = app_client.get("/api/kid/medals")
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"items", "locked"}

    earned = {m["medal_key"] for m in data["items"]}
    locked = {m["medal_key"] for m in data["locked"]}
    assert "first_star" in earned
    assert "streak_3" in locked
    assert "streak_7" in locked
    assert "speedster" in locked
    assert not (earned & locked)

    for m in data["items"] + data["locked"]:
        assert set(m) == {"medal_key", "name", "desc", "icon", "earned_date", "meta"}
        assert m["name"] and m["desc"]
        # icon 是内联 SVG path 的名字, 不是 emoji
        assert m["icon"] and not any(ord(ch) > 0x2000 for ch in m["icon"])


def test_streak_medal_earned_after_three_days(app_client):
    """连续三天完成 -> streak_3 进 items.

    每天只留一条必做并完成它 —— 那天要"必做全部完成"才算一天。
    """
    d = _today()
    for i in range(3):
        day = (d - timedelta(days=2 - i)).isoformat()
        aid = _add(app_client, day, f"作业 {i}")
        _complete(app_client, aid)

    data = app_client.get("/api/kid/medals").json()
    earned = {m["medal_key"] for m in data["items"]}
    assert "streak_3" in earned
    assert "streak_7" in {m["medal_key"] for m in data["locked"]}


def test_focused_medal_from_reflections(app_client):
    """本周 >=3 条小结且专注度全为 good -> focused 进 items."""
    for day in _week_days(3):
        r = app_client.post("/api/daily-wrap", json={
            "date": day.isoformat(), "difficulty": "ok", "focus": "good", "mood": "ok",
        })
        assert r.status_code == 200, r.text

    data = app_client.get("/api/kid/medals").json()
    assert "focused" in {m["medal_key"] for m in data["items"]}


def test_focused_medal_locked_when_focus_is_not_all_good(app_client):
    """有一条小结专注度不是 good -> focused 拿不到 (在 locked 里)."""
    days = _week_days(3)
    for i, day in enumerate(days):
        r = app_client.post("/api/daily-wrap", json={
            "date": day.isoformat(), "difficulty": "ok",
            "focus": "bad" if i == 1 else "good", "mood": "ok",
        })
        assert r.status_code == 200, r.text

    data = app_client.get("/api/kid/medals").json()
    assert "focused" in {m["medal_key"] for m in data["locked"]}


def test_speedster_medal_from_actual_minutes(app_client):
    """本周完成 >=5 项且实耗比预估少两成 -> speedster 进 items."""
    for i, day in enumerate(_week_days(5)):
        aid = _add(app_client, day.isoformat(), f"作业 {i}", est_minutes=20)
        _complete(app_client, aid)
        r = app_client.patch(
            f"/api/assignments/{aid}/completion", json={"actual_minutes": 10}
        )
        assert r.status_code == 200, r.text

    data = app_client.get("/api/kid/medals").json()
    assert "speedster" in {m["medal_key"] for m in data["items"]}


def test_speedster_locked_when_not_faster_than_estimate(app_client):
    """实耗跟预估一样 -> 拿不到效率小达人."""
    for i, day in enumerate(_week_days(5)):
        aid = _add(app_client, day.isoformat(), f"作业 {i}", est_minutes=20)
        _complete(app_client, aid)
        app_client.patch(
            f"/api/assignments/{aid}/completion", json={"actual_minutes": 20}
        )

    data = app_client.get("/api/kid/medals").json()
    assert "speedster" in {m["medal_key"] for m in data["locked"]}


# ====== 7. 晚间小结标签 ======

def test_reflection_tags_roundtrip(app_client):
    """标签存得进、取得出."""
    d = _today().isoformat()
    r = app_client.post("/api/daily-wrap", json={
        "date": d, "difficulty": "ok", "focus": "ok", "mood": "ok",
        "tags": ["many_errors", "focused"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["reflection"]["tags"] == ["many_errors", "focused"]

    got = app_client.get(f"/api/daily-wrap?date={d}").json()
    assert got["reflection"]["tags"] == ["many_errors", "focused"]


def test_reflection_invalid_tag_rejected(app_client):
    """非法 code 走 4003."""
    d = _today().isoformat()
    r = app_client.post("/api/daily-wrap", json={
        "date": d, "difficulty": "ok", "mood": "ok",
        "tags": ["not_a_real_tag"],
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


def test_reflection_tags_can_be_cleared(app_client):
    """传空数组 = 清空标签; 不传 = 保留原标签."""
    d = _today().isoformat()
    app_client.post("/api/daily-wrap", json={
        "date": d, "difficulty": "ok", "mood": "ok", "tags": ["reread"],
    })
    app_client.post("/api/daily-wrap", json={
        "date": d, "difficulty": "hard", "mood": "bad",
    })
    assert app_client.get(f"/api/daily-wrap?date={d}").json()["reflection"]["tags"] == ["reread"]

    app_client.post("/api/daily-wrap", json={
        "date": d, "difficulty": "hard", "mood": "bad", "tags": [],
    })
    assert app_client.get(f"/api/daily-wrap?date={d}").json()["reflection"]["tags"] == []
