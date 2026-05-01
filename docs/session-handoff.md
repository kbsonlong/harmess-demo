# Session Handoff

用于跨会话交接：让下一个执行者在最少上下文下恢复现场并继续推进。

## 开始前必读

- AGENTS.md
- CLAUDE.md
- feature_list.json
- docs/PROGRESS.md

## 本次目标（只写一个）

- 实现 Task7：kind_demo.py/gitops_demo.py 默认使用项目内 `.demo/kubeconfig`，并统一 kubectl 与 Kubernetes client

## 变更与证据

- 变更点：
  - `agent_core/kubeconfig.py`：新增默认 kubeconfig 路径与环境变量注入
  - `kind_demo.py`：kubectl/kubernetes client 默认遵循 `.demo/kubeconfig`
  - `gitops_demo.py`：kubectl subprocess 与 kubernetes client 统一遵循 `.demo/kubeconfig`
  - `tests/test_demo_kubeconfig_default.py`：覆盖默认与显式 `KUBECONFIG` 场景
- 验证证据：
  - `./init.sh` 单测全量通过（35 passed）

## 当前阻塞/风险

- 若 `.demo/kubeconfig` 缺失或内容损坏，Kubernetes client 加载会失败（与集群未就绪属于同类失败）

## 下一步（最小可执行）

- 本地最小验证（无需手动 export KUBECONFIG）：
  - `uv run python kind_demo.py up --name demo04`
  - `uv run python gitops_demo.py metadata --cluster-name demo04`
  - `uv run python kind_demo.py down --name demo04`

## 结束前检查

- 已更新 docs/PROGRESS.md
- 已更新 feature_list.json（仅更新相关 feature 的 status/evidence）
- 已确认 reports/ 中的运行时产物不被误删
