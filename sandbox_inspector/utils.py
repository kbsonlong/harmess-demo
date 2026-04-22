import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: str, prefix: str = "F") -> str:
    """基于输入片段生成稳定的短 ID，用于 findings 的可重复定位。"""
    raw = "\n".join(str(p) for p in parts).encode("utf-8")
    h = hashlib.sha1(raw).hexdigest()[:12]
    return f"{prefix}-{h}"


def truncate_text(value: Any, *, max_chars: int) -> str:
    """按字符数截断文本，保证输出可控，避免 Token 膨胀。"""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    keep = max(0, max_chars - 14)
    return s[:keep] + "...(truncated)"


def truncate_lines(text: str, *, max_lines: int, max_chars_per_line: int) -> str:
    """按行数与单行长度截断多行文本，并追加截断标记。"""
    if text is None:
        return ""
    if max_lines <= 0:
        return ""
    lines = text.splitlines()
    limited = lines[:max_lines]
    out = [truncate_text(x, max_chars=max_chars_per_line) for x in limited]
    if len(lines) > max_lines:
        out.append("...(truncated)")
    return "\n".join(out)


def limit_list(items: list[Any], *, max_items: int) -> list[Any]:
    """限制列表最大长度，超出部分丢弃。"""
    if max_items <= 0:
        return []
    return items[:max_items]


_DUR_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_duration_seconds(raw: str, *, default_seconds: int) -> int:
    """解析简单时长字符串（如 30m/2h/1d）为秒，解析失败返回默认值。"""
    if raw is None:
        return default_seconds
    raw = raw.strip()
    if raw == "":
        return default_seconds
    m = _DUR_RE.match(raw)
    if not m:
        return default_seconds
    n = int(m.group(1))
    unit = m.group(2).lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return max(0, n * mult)


def env_str(name: str, default: str = "") -> str:
    """读取环境变量为字符串，自动 strip，空值回退到默认值。"""
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


def env_int(name: str, default: int) -> int:
    """读取环境变量为整数，解析失败回退到默认值。"""
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    if v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default


def json_dumps(data: Any) -> str:
    """以 UTF-8 友好的方式格式化输出 JSON（不转义中文）。"""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)


def first_nonempty(values: Iterable[Optional[str]]) -> Optional[str]:
    """返回第一个非空白字符串；若均为空则返回 None。"""
    for v in values:
        if v is None:
            continue
        s = v.strip()
        if s:
            return s
    return None
