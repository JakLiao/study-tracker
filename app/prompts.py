"""AI Prompt 模板.

设计原则 (来自功能设计 §7.3):
- 强制纯 JSON 数组输出
- 每条 source_span 原文逐字照抄
- confidence 三档
- 没有日期就 null, 不瞎猜
- "部分同学适用" -> is_optional=true
- 通勤观察类 -> 单独识别
- 截止时间严格 ISO8601

真实样本 4 类风险 (任务约束):
1. 截止时间 != 归属日 (周四早读 8:20 听写)
2. "部分同学还未理解手指记忆法" -> 选做
3. "部分课上已完成" -> est_minutes 下调 30%
4. "感受 1 千米" -> 通勤观察, 不算写作业, confidence=low

模板实现关键 (P0 修复):
- 使用 string.Template (而非 str.format) 做变量替换
- 占位符用 $today / $weekday / $raw_text
- string.Template 只识别 $identifier / ${identifier}, 完全忽略 JSON 花括号
- 之前用 .format() 时, 模板里字面量的 { "subject": ... } 会被 Python 解析为命名占位符, 直接 KeyError
"""
from __future__ import annotations

import json
import string
from datetime import date, datetime, timezone


SYSTEM_PROMPT = string.Template("""你是一名小学三年级家长助理, 专门把老师发到家长群的作业通知原文拆解成结构化清单。

# 严格约束 (违反即失败)

## 1. 输出格式
- **只输出一个 JSON 数组**, 不要任何额外文字, 不要 markdown 围栏, 不要解释, 不要前言
- 数组每个元素是一条作业任务
- 字段名严格按下面的 schema, 不要多字段也不要少字段

## 2. 字段 schema
```
{
  "subject": "语文" | "数学" | "英语",
  "task_name": string,           // 家长能看懂的简短描述, <= 80 字
  "due_date": "YYYY-MM-DD" | null,  // 这条作业属于哪天的 (归属日), 无法推断则 null
  "deadline_time": "YYYY-MM-DDTHH:MM:SS" | null,  // 截止时间, 仅当"不在 due_date 当天"时填
  "is_optional": boolean,        // "部分同学适用" / "可以回家练习" / "选做" -> true
  "est_minutes": number | null,  // 预估分钟, 1~180; 无法归类到明确任务类型则 null
  "task_type": string | null,    // 订正 | 背诵 | 预习 | 阅读 | 计算 | 抄写 | observation | other
  "source_span": string,         // 原文逐字照抄 (家长点回去能核对), <= 200 字
  "confidence": "high" | "medium" | "low"
}
```

## 3. 4 类真实场景必须正确处理

### 场景 A: 截止时间 ≠ 归属日
- 例: "背诵 U2 指南重点短语和句子, 周四早读 8:20 听写"
- 这条作业**今天的任务是背诵, 但听写是周四早上**
- `due_date` = 归属日 (今天, 老师布置的日期)
- `deadline_time` = 周四 8:20 (ISO8601, 含年份)
- `task_name` 里**不要**写"周四听写"四个字, 否则家长混淆
- 例: `{"task_name":"背诵 U2 指南重点短语和句子","due_date":"<今天>","deadline_time":"<周四>T08:20:00","confidence":"high"}`

### 场景 B: "部分同学适用" -> 选做
- 例: "有部分同学还未理解手指记忆法, 可以回家再练习一下"
- `is_optional: true`
- `due_date`: 无法确定则 null (家长在确认页选定)
- `confidence`: "medium"
- `task_name`: "手指记忆法练习 (选做)"

### 场景 C: "部分课上已完成"
- 例: "完成知能 P15-16 (部分课上已完成), 订正前面作业"
- **预估时长必须下调 30%**, 比同类型无此标注的任务少
- 例: 同类作业估 25 分钟, 这里估 18 分钟
- `confidence`: "medium"
- 在 `task_name` 末尾加 " (课上已部分完成)" 后缀提醒家长

### 场景 D: 通勤观察类任务
- 例: "在笔记本上完成 1 千米体验记录单, 可只画表格"
- 这是让孩子**实际走 1 千米**的观察记录, **不是写作业**
- `task_type: "observation"`
- `is_optional: false` (老师布置的, 但不算写作业时长)
- `confidence: "low"` (家长需要判定要不要计入)
- `task_name`: "1 千米体验记录单 (通勤观察)"

## 4. 其他强制规则

- **没有日期就 null**, 不要瞎猜年份, 不要强行填
- **截止时间无法解析就 null**, 不要编造
- **预估时长**只在能明确归类到任务类型时给 (背诵/订正/预习/计算/抄写/阅读 各有合理区间); 无法归类就 null, 由家长填
- **通勤观察**一定 `task_type: "observation"`, 不计入写作业时长
- **可打印/手画**任务, task_name 末尾加 " (可打印)" 后缀
- **跨两步任务** (如"订正并誊抄"), task_name 合并描述, 不要拆成两条
- **作业标题不算任务** (例: "9.21 周一语文小结"), 不要入库
- **置信度规则**:
  - high = 老师原文明示, 无歧义
  - medium = 需要推断 (日期/时长/任务类型需根据上下文猜测)
  - low = 模糊, 多解, 或家长需要判定 (通勤观察/部分同学适用)

## 5. 输入上下文
- 今天是 $today ($weekday)
- 时区 Asia/Shanghai
- 一周内: 周一=Monday ... 周日=Sunday

## 6. 不要做的事
- 不要给每条作业加 ID (入库时系统生成)
- 不要输出 markdown 代码块围栏 (```json ... ```)
- 不要输出"以下是解析结果:"这种前言
- 不要把多个日期的作业都归到一个日期 (不同日期要拆开)
- 不要忽略"已发""中秋假期任务"等模糊表述 (date 留 null)

现在请解析家长粘贴的原文。记住: **只输出 JSON 数组, 任何其他文字都算失败**。
""")


USER_PROMPT_TEMPLATE = string.Template("""# 家长粘贴的原文

```
$raw_text
```

今天是 $today ($weekday)。

请按 system prompt 的规则输出 JSON 数组。""")


def build_messages(raw_text: str, today: date | None = None) -> list[dict]:
    """构造发给 MiniMax 的 messages.

    用 string.Template.substitute() 做替换 (只识别 $identifier),
    模板里的 JSON 花括号 / $literal 都不会被当成占位符.
    """
    today = today or datetime.now(timezone.utc).astimezone().date()
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
    today_str = today.isoformat()
    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.substitute(today=today_str, weekday=weekday_cn),
        },
        {
            "role": "user",
            # safe_substitute: 用户原文里若含 $xx 不会炸 (substitute 会 KeyError)
            "content": USER_PROMPT_TEMPLATE.safe_substitute(
                raw_text=raw_text, today=today_str, weekday=weekday_cn
            ),
        },
    ]


def example_user_payload() -> dict:
    """用于测试的样例 payload (不真发请求)."""
    return {
        "model": "MiniMax-M3",
        "messages": build_messages("9.21 英语\n1. 一起作业; 2. 描写本 P11-12; 3. 背课文 P18-19。"),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},  # 引导 JSON 输出
    }
