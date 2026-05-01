## 本地 Kind + 失败 Pod 示例

### 前置条件
- Docker Desktop 已启动
- 已安装 `kind`（例如通过 Homebrew 安装）

### 1) 拉起 Kind 集群

```bash
uv run python kind_demo.py up --name demo04
```

### 2) 创建一个无法启动的 Pod（ImagePullBackOff）

```bash
uv run python kind_demo.py bad-pod --namespace sandbox-demo --pod-name bad-imagepull
```

默认会使用一个不存在的镜像，Pod 会进入 `ErrImagePull/ImagePullBackOff`，用于演示排查镜像拉取/网络/registry 等问题。

### 3) 创建沙箱 Pod（支持自定义镜像）

通过 YAML 手工创建（推荐）：

```bash
kubectl apply -f k8s-sandbox.yaml
```

### 4) 清理 Kind 集群

```bash
uv run python kind_demo.py down --name demo04
```

### 常用环境变量
- `KUBECONFIG`：指定 kubeconfig 路径（可选）
- `SANDBOX_NAMESPACE`：默认 namespace（默认 `default`）
- `SANDBOX_IMAGE`：沙箱镜像（默认 `busybox:1.36`）
- `SANDBOX_TTL_SECONDS`：沙箱 TTL（默认 `900`）
- `SANDBOX_ALLOW_EXEC`：是否允许 `pods/exec`（默认 `true`）
- `SANDBOX_READONLY_ROOTFS`：是否只读根文件系统（默认 `true`）

---

## Sandbox Inspector（结构化巡检）

目标：把巡检采集固定为“强约束 JSON”，LLM 只做分析与逐条深挖，避免直接把大量 `kubectl` 输出塞进上下文导致 Token 爆炸。

### 1) 构建并加载巡检镜像到 kind

```bash
docker buildx build --platform linux/arm64 --provenance=false --sbom=false -f Dockerfile.sandbox-inspector -t demo04/sandbox-inspector:local --load .
kind load docker-image demo04/sandbox-inspector:local --name demo04
```

### 2) 创建使用该镜像的沙箱 Pod

```bash
kubectl apply -f k8s-sandbox.yaml
```

### 3) 在沙箱内执行巡检（run）

```bash
uv run python -c 'from k8s_sandbox import exec_in_sandbox; import json; print(json.dumps(exec_in_sandbox(command=["python","-m","sandbox_inspector.cli","run"]), ensure_ascii=False, indent=2))'
```

输出的 stdout 为巡检 JSON（包含 summary + findings 列表）。

### 4) 针对单条异常聚焦采集（focus）

从 `findings[].focus_refs[0]` 取出 `{kind,namespace,name,container}`，再执行：

```bash
uv run python -c 'from k8s_sandbox import exec_in_sandbox; import json; print(json.dumps(exec_in_sandbox(command=["python","-m","sandbox_inspector.cli","focus","--kind","Pod","--namespace","kube-system","--name","coredns-xxxxx"]), ensure_ascii=False, indent=2))'
```

---

## Kind + Argo CD + demo-app（GitOps 最小清单）

### 1) 安装 Argo CD（kustomize 引用官方 install.yaml）

```bash
kubectl apply -k manifests/gitops/argocd
kubectl -n argocd wait --for=condition=Available deploy/argocd-server --timeout=300s
```

本仓库默认将 `argocd-server` Service patch 为 NodePort：
- HTTP: 30080
- HTTPS: 30443

### 2) 创建 demo-app（Argo CD Application）

```bash
kubectl apply -f manifests/gitops/argocd/apps/demo-app.yaml
```

### 3) 输出发布元数据（落盘到 reports/）

```bash
uv run python gitops_demo.py metadata
```

会生成：`reports/release_metadata-<thread_id>.json`
