# Sandbox Inspector 安装与部署（手工创建沙箱）

本文档用于将 `sandbox_inspector/` 打包为镜像并部署一个只读沙箱 Pod，之后由上层 Agent 通过 `kubectl exec` 在沙箱内执行巡检脚本：

- 首次巡检：`python -m sandbox_inspector.cli run --max-findings 50`
- 针对单个异常深挖：`python -m sandbox_inspector.cli focus --kind Pod --namespace <ns> --name <pod>`

## 1. 前置条件

- 本机已安装并可用：
  - `docker`（支持 buildx）
  - `kubectl`
- 已登录阿里云镜像仓库（如仓库为私有）：
  - `docker login registry.cn-hangzhou.aliyuncs.com`
- 集群具备可调度节点（目标镜像为 `linux/amd64`；若你的集群节点为 `arm64`，需改为构建多架构镜像或构建 `linux/arm64` 镜像）
  - 脚本默认优先使用 in-cluster 配置（ServiceAccount token/ca.crt），避免因 kubeconfig/自签证书导致的 TLS 校验失败
  - 若你的集群 `kubectl` 依赖 kubeconfig 的 `insecure-skip-tls-verify` 或自定义 CA，而 in-cluster `ca.crt` 不匹配，可在沙箱内设置 `SANDBOX_PREFER_KUBECONFIG=1` 并提供 `KUBECONFIG`

## 2. 构建并推送 amd64 镜像

在项目根目录执行：

```bash
docker buildx create --use --name sandbox-inspector-builder 2>/dev/null || true
docker buildx inspect --bootstrap

docker buildx build \
  --platform linux/amd64 \
  -f Dockerfile.sandbox-inspector \
  -t registry.cn-hangzhou.aliyuncs.com/seam/sandbox-inspector:latest \
  --push \
  .
```

可选：追加一个不可变版本 tag（建议用于回滚）：

```bash
TAG="$(date +%Y%m%d-%H%M%S)"
docker buildx build \
  --platform linux/amd64 \
  -f Dockerfile.sandbox-inspector \
  -t registry.cn-hangzhou.aliyuncs.com/seam/sandbox-inspector:${TAG} \
  --push \
  .
```

## 3. 在集群创建镜像拉取凭证（如需要）

如果仓库是私有的，需要创建 imagePullSecret：

```bash
kubectl -n default create secret docker-registry aliyun-registry-cred \
  --docker-server=registry.cn-hangzhou.aliyuncs.com \
  --docker-username='<你的用户名>' \
  --docker-password='<你的密码或token>' \
  --docker-email='noreply@example.com'
```

说明：
- 如果仓库是公开的，可以跳过本步骤，并在 `k8s-sandbox.yaml` 中移除 `imagePullSecrets`。

## 4. 部署沙箱（RBAC + Pod）

项目已提供一份可直接 apply 的沙箱 YAML：

- [k8s-sandbox.yaml](file:///Users/zengshenglong/Code/PyWorkSpace/test/demo04/k8s-sandbox.yaml)

直接部署：

```bash
kubectl apply -f k8s-sandbox.yaml
kubectl -n default get pod k8s-sandbox -w
```

这份 YAML 包含：
- ServiceAccount: `default/k8s-sandbox-sa`
- ClusterRole: `k8s-sandbox-cluster-readonly`（对所有资源 `get/list/watch`）
- ClusterRoleBinding: `k8s-sandbox-cluster-readonly-binding`
- Pod: `default/k8s-sandbox`（镜像：`registry.cn-hangzhou.aliyuncs.com/seam/sandbox-inspector:latest`）

如需改 namespace，请同时修改：
- ServiceAccount 的 `metadata.namespace`
- ClusterRoleBinding 的 `subjects[].namespace`
- Pod 的 `metadata.namespace` 与 `spec.serviceAccountName`

## 5. 在沙箱内执行巡检（Agent 通过 exec 触发）

确认沙箱可 exec：

```bash
kubectl -n default exec -it k8s-sandbox -- sh -lc 'echo ok'
```

执行巡检（返回 JSON，建议由上层 Agent/LLM 读取并解析）：

```bash
kubectl -n default exec -it k8s-sandbox -- sh -lc \
  'python -m sandbox_inspector.cli run --max-findings 50'
```

对单个异常对象深挖（示例：Pod）：

```bash
kubectl -n default exec -it k8s-sandbox -- sh -lc \
  'python -m sandbox_inspector.cli focus --kind Pod --namespace sandbox-demo --name bad-imagepull'
```

## 6. 常见问题

### 6.1 Pod ImagePullBackOff

- 先看是否创建了 `aliyun-registry-cred`（私有仓库必需）
- 确认 `k8s-sandbox.yaml` 中 ServiceAccount 已配置：
  - `imagePullSecrets: - name: aliyun-registry-cred`

### 6.2 权限不足（Forbidden/Unauthorized）

- 先确认 ClusterRoleBinding 绑定的 ServiceAccount 与沙箱 Pod 使用的一致
- 可通过巡检 JSON 的 `permissions.missing[]` 看到缺失项

### 6.3 架构不匹配（exec format error）

该镜像按 `linux/amd64` 构建；如果集群节点为 `arm64` 会启动失败。
- 解决：构建 `linux/arm64` 或构建多架构镜像（`linux/amd64,linux/arm64`）

### 6.4 Python SDK 报 SSL 校验失败，但 kubectl 正常

现象：
- `python -m sandbox_inspector.cli run` 报 `CERTIFICATE_VERIFY_FAILED`
- `kubectl get ...` 正常

原因通常是两者使用的 TLS 信任链来源不同：
- `kubectl` 可能使用 kubeconfig 里的 `certificate-authority-data` 或 `insecure-skip-tls-verify`
- Python SDK 默认使用 in-cluster 的 `/var/run/secrets/kubernetes.io/serviceaccount/ca.crt`

解决路径：
- 推荐：让集群的 apiserver 证书与 in-cluster `ca.crt` 匹配（需要集群管理员处理）
- 工程兜底：在沙箱中提供 kubeconfig 并优先使用它：
  - `export SANDBOX_PREFER_KUBECONFIG=1`
  - `export KUBECONFIG=/path/to/config`
  - 或将 CA 写入文件并设置 `SANDBOX_SSL_CA_CERT=/path/to/ca.crt`
