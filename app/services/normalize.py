"""作业去重用的名称归一化.

为什么要归一化: 同一份作业重复录入时, task_name 往往存在不影响语义的差异 ——
老师通知截图 OCR 出来的全角括号 vs 家长手打的半角, AI 两次解析给出的
"背诵 U2 指南" vs "背 U2 指南", 页码写法 P15-16 vs P15 - 16。
这些若直接当字符串比, 会漏判重复, 家长就得手动去重。

关键设计: 归一化只用于"判断是否重复", 绝不写回数据库。
库里保留家长/AI 的原始 task_name, 避免"为了查重而篡改用户数据"。
"""

from __future__ import annotations

import re
import unicodedata

# 归一化时剔除的标点/符号 (中英文)。
# 保留字母数字与 CJK 字符, 其余一律删除, 这样 P15-16 / P15—16 / P15–16
# 会归一到同一个 p1516。
#
# 破折号类 (— U+2014 / – U+2013 / ― U+2015) 必须显式列出:
# 它们不落在任何 ASCII 范围里, 只用范围写法会漏掉。
# 这是最初用范围字符类时实测漏掉的两个用例。
_DASHES = "—–―−‒~〜～ー－"

# Unicode 类别 P*(标点) 与 S*(符号) 覆盖了 CJK 标点、破折号、全角符号等。
# 逐字符判定比维护一个巨大的字符类更不容易漏。
_STRIP_CATEGORIES = frozenset({"Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po",
                               "Sm", "Sc", "Sk", "So"})


def _is_strippable(ch: str) -> bool:
    if ch.isspace() or ch in _DASHES:
        return True
    return unicodedata.category(ch) in _STRIP_CATEGORIES

# 表示"选做/可做"的后缀。AI 被要求输出半角括号形式 (见 prompts.py),
# 但家长手打时常用全角, 归一化后需要剥掉才能与原文视为同一作业。
_OPTIONAL_SUFFIX = re.compile(r"[（(]?\s*(选做|可做|选读|自愿|非必做)\s*[）)]?$")


def normalize_task_name(name: str) -> str:
    """把任务名归一到"可比对"的形式.

    流程: NFKC 折叠全半角 -> casefold 大小写 -> 剥选做后缀 -> 去标点空格.

    NFKC 很关键: 它把 'Ｐ'(U+FF30) 折成 'P'、'１５' 折成 '15',
    这是纯 casefold 做不到的。

    Examples:
        >>> normalize_task_name("完成知能 P14-16")
        '完成知能p1416'
        >>> normalize_task_name("完成知能Ｐ１４-１６")
        '完成知能p1416'
        >>> normalize_task_name("手指记忆法练习 (选做)")
        '手指记忆法练习'
        >>> normalize_task_name("  听写   Unit 1  ")
        '听写unit1'
    """
    if not name:
        return ""
    # NFKC: 兼容分解 + 合成, 统一全角/半角/兼容字符
    s = unicodedata.normalize("NFKC", name)
    s = s.casefold()
    # 剥掉结尾的选做标记; 若剥完变空 (如任务名本身就叫"选做"), 保留原串
    s2 = _OPTIONAL_SUFFIX.sub("", s)
    if s2:
        s = s2
    s = "".join(ch for ch in s if not _is_strippable(ch))
    return s


def dedup_fingerprint(subject: str, task_name: str, due_date: str) -> str:
    """同一天内判断两条作业是否重复的指纹.

    只比较 (科目, 归属日, 归一化任务名) 三元组。

    为什么包含 due_date: 用户 2026-09-28 明确选择"仅同一天内去重"。
    "背课文 P18-19" 在周一和周二是两件真实的作业, 不该被合并 ——
    小学生背课文常常要背好几遍, 跨天重复是常态而非异常。

    为什么包含 subject: "复习第一单元" 在语文和数学可以同时存在,
    去掉科目会把两件不同的作业误判为重复。
    """
    return f"{subject}\x1f{due_date}\x1f{normalize_task_name(task_name)}"
