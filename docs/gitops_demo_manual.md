# GitOps Demo：手动触发发布异常 + 验证 + Agent 归因分析

本文档基于 [gitops_demo.py](file:///Users/zengshenglong/Code/PyWorkSpace/test/demo04/gitops_demo.py) 的 CLI 行为，说明如何：
1) 手动触发“发布导致的异常”（ImagePullBackOff / CrashLoopBackOff）
2) 手动验证异常已发生且确由发布变更触发
3) 运行多智能体巡检，让 Agent 基于 `release_failure-*.json` 的时间窗与目标对象做归因分析

## 0. 前置条件

- 本机（macOS）已具备：Docker、Kind、kubectl
- 以项目根目录为工作目录运行命令：

```bash
cd /Users/zengshenglong/Code/PyWorkSpace/test/demo04
```

> 提示：本项目多数脚本用 `uv run python ...` 运行。

## 1. 启动 GitOps 环境（Kind + Argo CD + demo-app）

`gitops_demo.py up` 会完成：
- （默认）创建 Kind 集群
- 安装 Argo CD（kustomize）
- 等待 Argo CD 就绪（ns/CRD Established/deploy Available）
- 应用 demo-app Application 清单
- 采集并写入发布元数据到 `reports/release_metadata-<thread_id>.json`

```bash
uv run python gitops_demo.py up --cluster-name demo04
```

输出中会打印元数据文件路径（`reports/release_metadata-<thread_id>.json`）。

如果你已经有 Kind 集群，只想安装/更新 Argo CD + demo-app，可加 `--skip-kind`：

```bash
uv run python gitops_demo.py up --cluster-name demo04 --skip-kind
```

## 2. 基线采集（可选，但强烈建议）

为“归因分析”留一份基线元数据（例如 Argo CD 状态、demo-app 部署镜像、NodePort 等）。

```bash
uv run python gitops_demo.py metadata --cluster-name demo04
```

这会写入 `reports/release_metadata-<thread_id>.json`。

## 3. 手动触发“发布导致的异常”

gitops_demo 提供两种故障注入模式：
- `imagepull`：把 demo Deployment 的镜像改成一个必然拉取失败的镜像 → 触发 `ErrImagePull/ImagePullBackOff`
- `crashloop`：把容器改成会立即退出的命令 → 触发 `CrashLoopBackOff`

### 3.1 选择一个 release_id（thread_id）

建议你显式指定 `--thread-id`，这样后续 `wait-fail`、Agent 分析更好对齐：

```bash
export RELEASE_ID="release-demo01"
```

### 3.2 注入失败（inject-failure）

默认会尝试关闭 Argo CD Application 的 autosync（除非你加 `--keep-autosync`）。

ImagePull 失败注入：

```bash
uv run python gitops_demo.py inject-failure --mode imagepull --thread-id "$RELEASE_ID"
```

CrashLoop 失败注入：

```bash
uv run python gitops_demo.py inject-failure --mode crashloop --thread-id "$RELEASE_ID"
```

注入后会写入并打印 `reports/release_failure-$RELEASE_ID.json`，其中包含：
- `mode`
- `objects`（Application + Deployment）
- `actions`（是否禁用 autosync、Deployment patch 的结果）
- `time_window.start`（注入前 60s）

### 3.3 等待失败被观测到（wait-fail）

这一步会轮询 Deployment 选出的 Pod，直到看到期望的 waiting reason（或超时），并补齐 `time_window.end`：

```bash
uv run python gitops_demo.py wait-fail --mode imagepull --thread-id "$RELEASE_ID"
```

或：

```bash
uv run python gitops_demo.py wait-fail --mode crashloop --thread-id "$RELEASE_ID"
```

成功时，`reports/release_failure-$RELEASE_ID.json` 会包含：
- `observed_at`
- `time_window.end`（观测后 60s）
- `wait_result.ok = true` 与 `wait_result.hits`（命中 Pod/容器、reason、message）

## 4. 手动验证：异常确实发生，且确由发布变更触发

### 4.1 验证 Deployment 的变更（发布变更证据）

查看 demo Deployment 当前镜像（你会看到它被 patch 成无效镜像或 crash 镜像）：

```bash
kubectl -n demo-app get deploy guestbook-ui -o wide
```

也可以直接打开 `reports/release_failure-$RELEASE_ID.json` 查看 `actions.patch_deployment.previous` 与 patch 结果。

### 4.2 验证 Pod 异常状态（运行态证据）

```bash
kubectl -n demo-app get pods
kubectl -n demo-app describe pod <pod-name>
```

预期现象：
- imagepull 模式：Pod 进入 `ErrImagePull`/`ImagePullBackOff`
- crashloop 模式：Pod 进入 `CrashLoopBackOff`，重启次数递增

### 4.3 验证 Argo CD（可选）

查看 Application 状态（如果禁用了 autosync，状态可能 OutOfSync；若未禁用，可能反复同步但仍失败）：

```bash
kubectl -n argocd get applications.argoproj.io demo-app -o wide
```

## 5. 运行多智能体巡检：让 Agent 做“发布导致异常”的归因分析

### 5.1 确保 Agent 能读取 release_failure 上下文

[main.py](file:///Users/zengshenglong/Code/PyWorkSpace/test/demo04/main.py#L36-L52) 会自动从 `reports/` 里加载最新的 `release_failure-*.json`，也支持通过环境变量指定：

- 指定 thread_id（推荐）：

```bash
export RELEASE_FAILURE_THREAD_ID="$RELEASE_ID"
```

- 或直接指定文件路径：

```bash
export RELEASE_FAILURE_PATH="$(pwd)/reports/release_failure-$RELEASE_ID.json"
```

### 5.2（可选但推荐）配置 VictoriaLogs 访问（直连/代理）

当前 `victorialogs_query` 是本机直连/代理访问（不再通过沙箱执行）。如果你的 VictoriaLogs 部署在集群内，最常见做法是做 port-forward：

```bash
kubectl -n observability port-forward svc/victorialogs 9428:9428
export VICTORIALOGS_BASE_URL="http://127.0.0.1:9428"
```

如果需要走代理：

```bash
export VICTORIALOGS_PROXY_URL="http://127.0.0.1:7890"
```

### 5.3 启动 Agent 分析

```bash
uv run python main.py
```

预期行为：
- Supervisor 初始消息会注入“GitOps 发布失败上下文”（包含 `release_id`、`time_window`、目标对象）
- 后续诊断会优先围绕该时间窗与目标对象收敛证据（Events/Logs/Workload 状态）
- 产物会落盘到 `reports/`（包括 inspection_report、internal_states、token_usage 等）

### 5.4 验证 Agent 的“发布归因”是否成立（你应该检查的证据）

建议你用“证据链”的方式验证 Agent 结论：
- `reports/release_failure-$RELEASE_ID.json`：
  - 证明“发布变更发生在 time_window.start 附近”，且目标对象明确（Deployment/Application）
- `reports/inspection_report-<thread_id>.md`：
  - 是否在“时间线/证据索引”中引用同一 `release_id/time_window`
  - 是否对 targets 做了聚焦（demo-app/guestbook-ui）
  - 是否给出可复现命令（kubectl describe / events / logs 查询）与关键片段
- `reports/internal_states.json`：
  - 子专家是否返回了与 time_window 对齐的证据，而不是泛化巡检结论

## 6. 清理

```bash
uv run python gitops_demo.py down --cluster-name demo04
```

