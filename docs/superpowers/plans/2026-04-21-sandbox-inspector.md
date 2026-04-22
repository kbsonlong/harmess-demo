# Sandbox Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Kubernetes 沙箱 Pod 内运行结构化巡检脚本，输出强约束 JSON（摘要 + 异常目录 + 可聚焦深挖），由 LLM 通过 exec 触发执行并按异常逐条分析，避免 Token 爆炸。

**Architecture:** 采用 Python + Kubernetes SDK 在集群内采集数据；`run` 产出“最小必要”巡检 JSON；`focus` 针对单个异常对象补充事件/日志等深证据包。镜像以只读 rootfs 方式运行，默认常驻 `sleep infinity`，由外部通过 exec 触发巡检。

**Tech Stack:** Python 3.12, kubernetes Python client, Docker, kind（本地验证）

---

## File Structure

- Create: `sandbox_inspector/__init__.py`
- Create: `sandbox_inspector/cli.py`
- Create: `sandbox_inspector/inspector.py`
- Create: `sandbox_inspector/types.py`
- Create: `sandbox_inspector/utils.py`
- Create: `Dockerfile.sandbox-inspector`
- Modify: `README.md`
- Create: `tests/test_sandbox_inspector_utils.py`
- Create: `tests/test_sandbox_inspector_findings.py`

---

### Task 1: Define JSON schema + utilities

**Files:**
- Create: `sandbox_inspector/types.py`
- Create: `sandbox_inspector/utils.py`
- Test: `tests/test_sandbox_inspector_utils.py`

- [ ] **Step 1: Add utils for truncation and stable IDs**

```python
import hashlib

def stable_id(*parts: str, prefix: str = "F") -> str:
    raw = "\n".join(parts).encode("utf-8")
    h = hashlib.sha1(raw).hexdigest()[:12]
    return f"{prefix}-{h}"

def truncate_text(s: str, max_chars: int) -> str:
    if s is None:
        return ""
    s = str(s)
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[: max(0, max_chars - 12)] + "...(truncated)"
```

- [ ] **Step 2: Add unit tests for deterministic ID and truncation**

```python
from sandbox_inspector.utils import stable_id, truncate_text

def test_stable_id_deterministic():
    assert stable_id("a","b") == stable_id("a","b")
    assert stable_id("a","b") != stable_id("a","c")

def test_truncate_text():
    assert truncate_text("abc", 2).endswith("(truncated)")
    assert truncate_text("abc", 100) == "abc"
```

---

### Task 2: Implement inspector (run + focus)

**Files:**
- Create: `sandbox_inspector/inspector.py`
- Create: `sandbox_inspector/cli.py`
- Test: `tests/test_sandbox_inspector_findings.py`

- [ ] **Step 1: Implement RBAC probe via SelfSubjectAccessReview**
- [ ] **Step 2: Implement checks mapped from SKILL**
  - Nodes：NotReady/Pressure/NetworkUnavailable
  - kube-system：非 Running/Succeeded Pod、重启异常、核心组件异常
  - Workloads：deploy/sts/ds/rs/job 不达标
  - Pods：异常相位/CrashLoop/OOM/ImagePull 等
  - Storage：StorageClass + PVC/PV 非 Bound
  - Quota/LimitRange：摘要
- [ ] **Step 3: Produce structured findings with strong caps**
  - 全局 `max_findings`、`max_items_scanned`
  - 文本 `max_chars`、日志 `tail_lines`
  - evidence 仅保留最小片段
- [ ] **Step 4: Implement focus for pod/node/workload**
  - events：按 involvedObject 过滤
  - logs：tail_lines + previous（可用时）
  - 输出 focus JSON

---

### Task 3: Build Docker image for sandbox

**Files:**
- Create: `Dockerfile.sandbox-inspector`

- [ ] **Step 1: Create Dockerfile with Python runtime + dependencies**

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN pip install --no-cache-dir kubernetes==35.0.0
COPY sandbox_inspector /app/sandbox_inspector
CMD ["sleep","infinity"]
```

- [ ] **Step 2: Ensure it works with read-only rootfs**
  - 不写入 /app
  - 仅使用 /tmp（K8s sandbox 已挂载）

---

### Task 4: Documentation + local kind verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document build/load/run**

```bash
docker build -f Dockerfile.sandbox-inspector -t demo04/sandbox-inspector:local .
kind load docker-image demo04/sandbox-inspector:local --name demo04
export SANDBOX_IMAGE="demo04/sandbox-inspector:local"
uv run python kind_demo.py sandbox --namespace default
uv run python -c 'from k8s_sandbox import exec_in_sandbox; import json; print(json.dumps(exec_in_sandbox(command=["python","-m","sandbox_inspector.cli","run"]), ensure_ascii=False, indent=2))'
```

---

### Task 5: Test & sanity checks

**Files:**
- Test: `tests/test_sandbox_inspector_utils.py`
- Test: `tests/test_sandbox_inspector_findings.py`

- [ ] **Step 1: Run unit tests**

```bash
uv run python -m unittest -v
```

- [ ] **Step 2: (Optional) Run kind demo end-to-end**
  - up → bad-pod → sandbox → exec run/focus

