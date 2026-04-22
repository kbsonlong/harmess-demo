from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional


Severity = Literal["P0", "P1", "P2"]
Conclusion = Literal["healthy", "risk", "outage"]


@dataclass(frozen=True)
class FocusRef:
    """聚焦采集（focus）所需的对象引用：kind/namespace/name/container。"""

    kind: str
    name: str
    namespace: Optional[str] = None
    container: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 友好字典。"""
        return {
            "kind": self.kind,
            "namespace": self.namespace,
            "name": self.name,
            "container": self.container,
        }


@dataclass(frozen=True)
class Evidence:
    """最小化证据片段：用于证明异常存在，避免输出大段日志。"""

    kind: str
    message: str
    ref: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 友好字典。"""
        return {"kind": self.kind, "ref": self.ref, "message": self.message}
