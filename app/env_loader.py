""".env 解析与连接串改写 (config 与测试共用).

为什么单独抽出来:
  config.py 要读 .env 拿 DATABASE_URL, tests/conftest.py 也要读同一份
  .env 才能推出测试库连接串 —— 两处各写一份解析器的话, 改格式时容易只改
  一处, 于是"服务读到了 A 家库、测试连到了 B 家库"这种错位很难发现。
  放在这里就没有第二份实现。

解析规则 (与旧版保持一致, 不引入 python-dotenv 依赖):
  KEY=VALUE, 跳过空行与 # 开头的行, 不支持引号嵌套。
  已存在的系统环境变量优先级最高 —— .env 只是"没配时的兜底", 不该覆盖
  运维显式传进来的值。
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def load_dotenv_if_exists(root: Path) -> None:
    """把 .env 里的键值注入 os.environ (已存在的键不覆盖)."""
    env_path = root / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # 已有系统环境变量不覆盖 (系统优先)
        if key and key not in os.environ:
            os.environ[key] = value


def database_url_with_name(url: str, name: str) -> str:
    """把连接串里的库名换成 name, 其余部分 (含密码) 原样保留。

    测试库与业务库同实例、不同库名: 这样测试 TRUNCATE 业务表时碰不到
    真实家庭数据, 而连接参数 (主机/端口/用户/密码) 只有一份, 不用在两个
    地方各写一遍 —— 密码泄露的口子也就只有一个。
    """
    if not url:
        return ""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
