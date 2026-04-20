---
name: "k8s-inspector"
description: "对 Kubernetes 集群做巡检与健康检查，输出问题清单与修复建议。用户提到“巡检/健康检查/集群异常/Pod 异常/排障”时调用。"
---

# Kubernetes 巡检（K8s Inspector）

## 目标

- 快速判断集群整体健康度（控制面、节点、核心组件、工作负载、网络、存储、事件）。
- 以“问题清单 + 证据 + 建议动作”的形式输出，可直接用于工单/值班交接。
- 默认只读巡检；任何会改动集群状态的操作，必须先征得用户明确同意。

## 权限与提权策略（必须遵守）

- 沙箱在巡检开始前已由系统创建；不要尝试创建/修改任何 RBAC、ServiceAccount、Role/ClusterRole、(Cluster)RoleBinding。
- 若发现权限不足（命令返回 Forbidden/Unauthorized，或 `kubectl auth can-i ...` 返回 no），跳过当前检查项：
  - 在报告中记录：缺少的权限（资源/动词/作用域）+ 对应失败证据（关键 stderr 片段即可）
  - 给出管理员处理建议：对固定 Role/ClusterRole 进行授权（不在 Agent 内提权处理）

## 适用场景（触发条件）

- 用户要求：Kubernetes 巡检、集群健康检查、集群例行检查、上线前检查、故障排查。
- 用户描述：节点 NotReady、Pod CrashLoopBackOff/ErrImagePull、服务不可达、DNS 异常、PVC Pending、频繁重启、告警飙升。

## 开始前需要确认的信息（沙箱内默认值，尽量不打断执行）

- 运行位置：巡检动作在集群内只读 sandbox 沙箱中执行（默认）。
- 权限边界：RBAC 只读（默认）；不做任何写操作。
- 范围：全局巡检或指定 namespace（默认全局）。
- 时间窗：事件/日志关注最近多久（默认 2 小时）。
- 约束：是否允许在 sandbox 内做 `kubectl exec` 级别的连通性验证（默认只采集，不主动探测）。

## 输出格式要求

- 先给一页摘要：总体结论（健康/风险/故障）+ Top 3 风险点。
- 然后给问题列表（按严重度排序）：P0/P1/P2，每条包含：
  - 现象：简短描述
  - 证据：关键字段/命令输出片段（不要堆大段日志）
  - 影响面：哪些 namespace/服务/节点
  - 可能原因：1-3 条假设
  - 建议动作：优先级与回滚点

## 巡检流程（默认只读）

### 1) 基础连通性与上下文

- 记录 sandbox 的 namespace / Pod / ServiceAccount（用于还原权限与执行环境）。
- 采集只读权限边界（抽样验证即可，避免输出过长）：
  - `kubectl auth can-i get pods -A`
  - `kubectl auth can-i get nodes`
  - `kubectl auth can-i get events -A`
- 采集版本与 API 可用性：
  - `kubectl version`
  - `kubectl cluster-info`

### 2) 节点健康与资源压力

- 节点状态与角色分布：
  - `kubectl get nodes -o wide`
  - `kubectl describe node <notready-node>`（只对异常节点）
- 关注点：
  - NotReady/NetworkUnavailable
  - DiskPressure/MemoryPressure/PIDPressure
  - 节点漂移（频繁变更 InternalIP/不可达）
- 若 metrics 可用：
  - `kubectl top nodes`
  - `kubectl top pods -A --sort-by=cpu`

### 3) 控制面与核心系统组件（kube-system）

- `kubectl get pods -n kube-system -o wide`
- 优先检查：
  - CoreDNS（Pending/CrashLoop/高重启）
  - kube-proxy / CNI 相关组件
  - 控制面组件（若以 Pod 形式运行）
- 对异常 Pod 执行（按需）：
  - `kubectl describe pod -n kube-system <pod>`
  - `kubectl logs -n kube-system <pod> --tail=200`

### 4) 工作负载健康（全 namespace）

- 快速筛选异常资源：
  - `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded`
  - `kubectl get deploy -A`
  - `kubectl get rs -A`
  - `kubectl get ds -A`
  - `kubectl get sts -A`
  - `kubectl get job -A`
- 常见判定：
  - Deployment/StatefulSet 不达标（Ready/Available 与期望不一致）
  - Pod CrashLoopBackOff / OOMKilled / ImagePullBackOff / CreateContainerConfigError
  - Job 失败重试、BackoffLimitExceeded
- 对 Top 异常对象补充证据：
  - `kubectl describe <kind> -n <ns> <name>`
  - `kubectl logs -n <ns> <pod> --previous --tail=200`（若有重启）

### 5) 事件（Events）与异常趋势

- 按时间排序查看最近事件：
  - `kubectl get events -A --sort-by=.metadata.creationTimestamp`
- 聚焦类别：
  - 拉镜像失败（权限/仓库/网络）
  - 探针失败（Readiness/Liveness）
  - 调度失败（资源不足、亲和/反亲和、污点容忍、PVC 未绑定）
  - 节点驱逐（Evicted）

### 6) 网络与 DNS（只读优先）

- 核心检查：
  - CNI 组件 Pod 状态（通常在 kube-system）
  - Service/Endpoints 是否匹配：
    - `kubectl get svc -A`
    - `kubectl get endpoints -A`
- 若用户允许在 sandbox 内做连通性验证，可选执行：
  - 在 sandbox 现有容器内执行 `nslookup`/`curl` 等只读探测（不创建/删除集群资源）。

### 7) 存储（PV/PVC/StorageClass）

- `kubectl get storageclass`
- `kubectl get pvc -A`
- `kubectl get pv`
- 关注点：
  - PVC Pending（未匹配 StorageClass/容量/访问模式）
  - PV Released/Failed
  - 附加/挂载失败事件（AttachVolume/MountVolume）

### 8) 变更风险与容量（轻量）

- 检查资源配额与限制（如存在）：
  - `kubectl get resourcequota -A`
  - `kubectl get limitrange -A`
- 检查高风险配置（只提示，不擅自修改）：
  - 未设置 requests/limits 导致抢占/驱逐风险
  - 探针过严导致抖动
  - 单副本关键服务缺少 PDB/反亲和

## 诊断到问题时的处理原则

- 先给“可证伪”的假设：每个问题最多 3 条原因假设，并说明下一步验证动作。
- 优先选择“最小侵入”的验证方式（describe/events/logs），再考虑临时 Pod/抓包等手段。
- 给出修复动作时，必须包含回滚点（例如回滚镜像 tag、撤销配置、缩容恢复）。

## 常见问题到建议动作（速查）

- ImagePullBackOff：检查镜像名/tag、镜像仓库连通性、imagePullSecrets、节点出网/DNS。
- CrashLoopBackOff：先看 `--previous` 日志与退出码；再看探针、配置注入、依赖服务；必要时临时提高日志等级（需确认）。
- OOMKilled：核对 requests/limits 与实际峰值；检查内存泄漏；评估 HPA/VPA（仅建议）。
- Pending（调度失败）：describe Pod 看调度原因（资源/亲和/污点/PVC）；再对症处理。
- PVC Pending：核对 StorageClass/Provisioner、事件、访问模式；检查存储后端健康。

## 交付物模板（可直接复制到工单/巡检报告）

### 摘要

- 结论：健康 / 风险 / 故障
- 关键发现：
  - P0：…
  - P1：…
  - P2：…

### 详细问题清单

1. [P0] <问题标题>
   - 现象：…
   - 证据：…
   - 影响面：…
   - 可能原因：…
   - 建议动作：…

## 示例提问（用于快速收敛范围）

- “巡检的 sandbox 在哪个 namespace / Pod / ServiceAccount 下运行？”
- “需要全局巡检还是只看某个 namespace/应用？”
- “是否允许在 sandbox 里做 `kubectl exec` 的 DNS/网络连通性验证（不创建任何资源）？”
