# Kubernetes 集群健康度检查报告

## 1. kube-system 组件状态 ✅

| 组件 | 状态 | 就绪 | 重启次数 | 运行时间 |
|------|------|------|----------|----------|
| coredns (x2) | Running | 1/1 | 0 | 2d5h |
| etcd | Running | 1/1 | 0 | 2d5h |
| kindnet | Running | 1/1 | 0 | 2d5h |
| kube-apiserver | Running | 1/1 | 0 | 2d5h |
| kube-controller-manager | Running | 1/1 | 0 | 2d5h |
| kube-proxy | Running | 1/1 | 0 | 2d5h |
| kube-scheduler | Running | 1/1 | 0 | 2d5h |

**评估：✅ 健康** - 所有 kube-system 组件正常运行，无异常

---

## 2. 各命名空间 Pod 状态汇总

### 命名空间概览

| 命名空间 | Pod 数量 | Running | Pending | Failed | ImagePullBackOff |
|----------|---------|---------|---------|--------|------------------|
| default | 1 | 1 | 0 | 0 | 0 |
| kube-system | 9 | 9 | 0 | 0 | 0 |
| local-path-storage | 1 | 1 | 0 | 0 | 0 |
| sandbox-demo | 1 | 0 | 0 | 0 | 1 |

**总体状态：⚠️ 存在异常 Pod**

---

## 3. 异常 Pod 详细诊断

### 异常 Pod: `bad-imagepull` (sandbox-demo 命名空间)

| 属性 | 值 |
|------|-----|
| 命名空间 | sandbox-demo |
| 状态 | Pending |
| 就绪 | 0/1 |
| 重启次数 | 0 |
| 运行时间 | 2d5h |
| 节点 | demo04-control-plane |

### 容器状态

| 容器 | 状态 | 就绪 | 重启次数 |
|------|------|------|----------|
| bad | Waiting (ImagePullBackOff) | False | 0 |

### 问题原因

**镜像拉取失败** - 镜像不存在或权限不足

```
Image: this-image-should-not-exist.invalid:0
Error: failed to pull and unpack image "docker.io/library/this-image-should-not-exist.invalid:0"
Reason: pull access denied, repository does not exist or may require authorization
```

### 事件分析

| 时间 | 类型 | 原因 | 事件 |
|------|------|------|------|
| 21m | Normal | Pulling | 尝试拉取镜像 |
| 16m | Warning | Failed | 镜像拉取失败 |
| 81s | Normal | BackOff | 退避重试中 |
| 81s | Warning | Failed | ImagePullBackOff |

**评估：❌ 严重异常** - 镜像 `this-image-should-not-exist.invalid:0` 不存在，导致 Pod 无法启动

---

## 4. 异常事件分析

### 集群事件汇总

| 命名空间 | 对象 | 类型 | 原因 | 消息 |
|----------|------|------|------|------|
| sandbox-demo | pod/bad-imagepull | Warning | Failed | Failed to pull image |
| sandbox-demo | pod/bad-imagepull | Warning | ImagePullBackOff | Error: ImagePullBackOff |

### 事件分析

**主要问题：镜像拉取失败**
- 镜像名称：`this-image-should-not-exist.invalid:0`
- 错误类型：Repository does not exist or authorization failed
- 影响：Pod 处于 Pending 状态，无法调度运行

**建议修复：**
1. 删除异常 Pod：`kubectl delete pod bad-imagepull -n sandbox-demo`
2. 检查镜像名称是否正确
3. 验证镜像仓库访问权限

---

## 5. 工作负载健康度评估

### Deployment 状态

| 命名空间 | 名称 | 就绪 | 最新 | 可用 | 状态 |
|----------|------|------|------|------|------|
| kube-system | coredns | 2/2 | 2 | 2 | ✅ 健康 |
| local-path-storage | local-path-provisioner | 1/1 | 1 | 1 | ✅ 健康 |

### DaemonSet 状态

| 命名空间 | 名称 | 期望 | 当前 | 就绪 | 状态 |
|----------|------|------|------|------|------|
| kube-system | kindnet | 1 | 1 | 1 | ✅ 健康 |
| kube-system | kube-proxy | 1 | 1 | 1 | ✅ 健康 |

### StatefulSet 状态

无 StatefulSet 资源

---

## 6. 总体健康度评估

### 健康度评分：91% ⚠️

| 组件类型 | 健康状态 | 评分 |
|----------|----------|------|
| kube-system 组件 | ✅ 全部健康 | 100% |
| 命名空间 Pod | ⚠️ 存在异常 | 91% |
| Deployment | ✅ 全部健康 | 100% |
| DaemonSet | ✅ 全部健康 | 100% |
| 集群事件 | ⚠️ 存在异常事件 | 100% |

### 问题汇总

| 优先级 | 问题 | 影响范围 | 建议操作 |
|--------|------|----------|----------|
| 🔴 高 | Pod `bad-imagepull` 镜像拉取失败 | sandbox-demo 命名空间 | 删除异常 Pod，修复镜像配置 |

### 修复建议

```bash
# 1. 删除异常 Pod
kubectl delete pod bad-imagepull -n sandbox-demo

# 2. 验证修复后 Pod 状态
kubectl get pods -n sandbox-demo -w

# 3. 检查镜像是否存在
docker pull this-image-should-not-exist.invalid:0

# 4. 检查镜像仓库权限
kubectl logs <pod-name> -n sandbox-demo
```

---

## 7. 健康度详情

### ✅ 正常组件 (10 个)
- kube-system: 8 个组件 (coredns x2, etcd, kindnet, kube-apiserver, kube-controller-manager, kube-proxy, kube-scheduler)
- default: 1 个 Pod (k8s-sandbox)
- local-path-storage: 1 个 Pod (local-path-provisioner)

### ⚠️ 异常组件 (1 个)
- sandbox-demo: 1 个 Pod (bad-imagepull) - ImagePullBackOff

### 节点状态
- demo04-control-plane: 运行中，承载所有 Pod

---

**检查时间**: 2026-04-20  
**集群**: demo04-control-plane  
**检查者**: Kubernetes 负载专家
