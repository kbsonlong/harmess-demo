# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Kubernetes cluster inspection and health-check system that uses a multi-agent architecture (deepagents + LangGraph) to perform automated cluster diagnostics. The system runs inspection commands inside sandboxed pods for security and returns structured JSON findings.

## Harness Model (Five Subsystems)

Project-specific definitions for the “Harness 五子系统模型” live in `AGENTS.md` (root) and `docs/PROGRESS.md`.

**Key Architecture:**
- **Multi-agent system**: Supervisor (Coordinator) orchestrates three sub-agents: planner → executor → validator, enforcing a strict workflow (plan → execute → validate → aggregate → report)
- **Sandbox execution**: All cluster inspection commands run through `k8s_sandbox.exec_in_sandbox()` which executes inside privileged sandbox pods with read-only RBAC
- **Structured inspection**: The `sandbox_inspector` module performs comprehensive health checks and outputs JSON with severity-graded findings (P0/P1/P2)
- **Skills system**: Domain knowledge is stored in `skills/k8s-inspector/SKILL.md` which agents use to guide inspection procedures

## Development Commands

**Run main inspection multi-agent system:**
```bash
uv run python main.py
```

**Run sandbox inspector directly (inside cluster):**
```bash
# Full inspection
kubectl exec -n default k8s-sandbox -- python -m sandbox_inspector.cli run --max-findings 50

# Focus on specific resource
kubectl exec -n default k8s-sandbox -- python -m sandbox_inspector.cli focus --kind Pod --namespace <ns> --name <pod>
```

**Local Kind cluster management:**
```bash
# Create cluster
uv run python kind_demo.py up --name demo04

# Create problematic pod for testing
uv run python kind_demo.py bad-pod --namespace sandbox-demo --pod-name bad-imagepull

# Destroy cluster
uv run python kind_demo.py down --name demo04
```

**Testing:**
```bash
# Run all tests (from project root)
pytest

# Run specific test file
pytest tests/test_k8s_sandbox.py

# Run with coverage
pytest --cov=.
```

**Build and deploy sandbox inspector:**
```bash
# Build image (adjust platform for your cluster - linux/amd64 or linux/arm64)
docker buildx build --platform linux/arm64 --provenance=false --sbom=false -f Dockerfile.sandbox-inspector -t demo04/sandbox-inspector:local --load .

# Load into Kind cluster
kind load docker-image demo04/sandbox-inspector:local --name demo04

# Deploy sandbox pod
kubectl apply -f k8s-sandbox.yaml
```

**Quick full inspection workflow:**
```bash
./run_inspection.sh
```

## Key Architectural Concepts

### Multi-Agent Workflow (main.py)

The system enforces a strict 4-step workflow that agents cannot bypass:
1. **Task planning**: Supervisor writes detailed TODOs to `reports/todos.json`
2. **Task assignment**: Supervisor delegates to expert sub-agents based on their expertise
3. **Data aggregation**: Results collected in `reports/internal_states.json`
4. **Final delivery**: Report generated only after all TODOs marked `completed`

**Critical**: The supervisor must receive actual observations from sub-agents before marking TODOs complete. No early exits or premature report generation.

### Sandbox Execution Model (k8s_sandbox.py)

- **Entry point**: `exec_in_sandbox()` function wraps all kubectl-style commands
- **Pod selection**: Automatically selects best sandbox pod by readiness/creation time, or accepts `pod_name`/`label_selector`
- **Container selection**: Prefers container named "sandbox", falls back to first container
- **Validation**: Enforces command is list of strings, validates pod_name vs label_selector exclusivity
- **Timeout handling**: 30s default timeout with cleanup on exec timeout

**Usage pattern**:
```python
from k8s_sandbox import exec_in_sandbox
result = exec_in_sandbox(
    namespace="default",
    command=["kubectl", "get", "pods", "-A", "--field-selector=status.phase!=Running"]
)
# Returns: {stdout, stderr, exit_code, namespace, pod_name, container, ...}
```

### Sandbox Inspector (sandbox_inspector/)

Structured health check module that outputs JSON findings:
- **inspector.py**: Core Inspector class with `run()` (full inspection) and `focus()` (single resource deep-dive)
- **cli.py**: Command-line interface with `run` and `focus` subcommands
- **types.py**: Data structures (FocusRef, Evidence, Severity, Conclusion)

**Key design**:
- Pagination with hard limits to prevent token explosion
- Truncation of logs/evidence to configurable max lengths
- Permission probing via SelfSubjectAccessReview API
- Two-phase inspection: broad scan (run) → targeted focus (focus)

**Output schema**:
```json
{
  "schema_version": "1",
  "generated_at": "ISO-8601",
  "context": {"namespace", "pod_name", "service_account", "kubernetes_version"},
  "permissions": {"checks": [], "missing": []},
  "summary": {"conclusion": "healthy|risk|outage", "counts": {"P0": 0, "P1": 0, "P2": 0}, "top_findings": []},
  "findings": [
    {
      "id": "stable_id",
      "severity": "P0|P1|P2",
      "type": "finding_type",
      "title": "Human-readable title",
      "symptom": "Brief description",
      "evidence": [{"kind": "...", "ref": {...}, "message": "..."}],
      "focus_refs": [{"kind": "Pod", "namespace": "...", "name": "..."}]
    }
  ],
  "stats": {"scanned": {}, "truncated": {}}
}
```

### Skills System

Skills are markdown files in `skills/` directory that provide domain knowledge to agents:
- **Structure**: Frontmatter with name/description, followed by detailed procedures
- **Usage**: Agents load and follow skill procedures during task execution
- **k8s-inspector skill**: Comprehensive K8s inspection procedures with filter patterns to avoid output bloat

**When working with skills**:
- Skills are authoritative procedures - agents must follow them step-by-step
- Skills include specific kubectl filter patterns to manage output volume
- Skills define permission boundaries and escalation procedures

### RBAC and Security

The system uses read-only RBAC for sandbox pods:
- **ClusterRole**: `k8s-sandbox-cluster-readonly` grants get/list/watch on core resources
- **ServiceAccount**: `k8s-sandbox-sa` in target namespace
- **ClusterRoleBinding**: Binds SA to ClusterRole cluster-wide
- **Pod security**: runAsNonRoot, readOnlyRootFilesystem, drop all capabilities

**Environment variables**:
- `KUBECONFIG`: Path to kubeconfig (optional, uses default if not set)
- `SANDBOX_NAMESPACE`: Default namespace for sandbox operations (default: "default")
- `SANDBOX_INSECURE_SKIP_TLS_VERIFY`: Skip TLS verification (set to "1" for self-signed certs)
- `SANDBOX_PREFER_KUBECONFIG`: Prefer kubeconfig over in-cluster config (set to "1" if needed)
- `WECOM_WEBHOOK_URL`: 企业微信机器人 Webhook 地址；当巡检报告（reports/inspection_report-*.md）生成后，自动推送 Markdown 消息
- `AGENT_PROFILE`: 选择 profiles/<name>.json（默认 default）
- `AGENT_PROFILE_PATH`: 直接指定 profile JSON 的绝对路径；优先级高于 AGENT_PROFILE

## Testing Approach

- **Unit tests**: `tests/` directory uses unittest with mock patching for Kubernetes API calls
- **Test patterns**: Mock `k8s_sandbox._get_core_v1()` and `k8s_sandbox.stream` to simulate exec responses
- **Focus areas**: Command validation, stdout/stderr streaming, timeout handling, error scenarios

## Common Operations

**When adding new inspection checks**:
1. Add check method to `sandbox_inspector/inspector.py` following `_check_*` pattern
2. Call from `Inspector.run()` method
3. Update `InspectorConfig` if new limits/parameters needed
4. Add corresponding procedure to `skills/k8s-inspector/SKILL.md`
5. Add unit tests in `tests/test_sandbox_inspector_*.py`

**When debugging multi-agent issues**:
- Check `reports/todos.json` for planned vs completed tasks
- Check `reports/internal_states.json` for what sub-agents actually returned
- Enable ToolEventPrinter callbacks in main.py (already enabled)
- Look for early exit patterns where supervisor reports without sub-agent observations

**When sandbox exec fails**:
1. Verify sandbox pod exists: `kubectl get pod -l app=k8s-sandbox`
2. Verify RBAC: `kubectl auth can-i get pods --as=system:serviceaccount:default:k8s-sandbox-sa`
3. Test exec manually: `kubectl exec -it k8s-sandbox -- python -m sandbox_inspector.cli run`
4. Check logs: `kubectl logs k8s-sandbox`

**When working with Kind clusters**:
- Default cluster name: `demo04`
- Default kubeconfig: Kind creates this automatically
- Context: `kind-demo04`
- Architecture mismatch is common - build images for `linux/arm64` on Apple Silicon

## Important Constraints

- **No `-o json` in sandbox**: Avoid raw JSON output from kubectl - it bloats context. Use field selectors and custom columns instead.
- **Filter everything**: Always use `--field-selector` or `grep` to reduce output volume before sending to LLM
- **Structured over free-form**: Use sandbox_inspector for structured JSON rather than free-form kubectl output
- **Permission boundaries**: System assumes read-only access. Write operations require explicit user confirmation and separate RBAC.
- **Token budget**: Inspection outputs are truncated to prevent context overflow. Configure via `InspectorConfig`.
