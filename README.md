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

通过环境变量：

```bash
export SANDBOX_IMAGE="busybox:1.36"
uv run python kind_demo.py sandbox --namespace default
```

或直接参数覆盖（优先级更高）：

```bash
uv run python kind_demo.py sandbox --namespace default --image "busybox:1.36"
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
