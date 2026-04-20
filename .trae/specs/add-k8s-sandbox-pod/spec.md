# K8S Pod Sandbox Spec

## Why
当前排查 K8S 集群问题时，常需要临时进入集群网络环境执行诊断命令，但直接使用高权限 Pod/ServiceAccount 风险高。需要一个“最小权限”的排查沙箱 Pod，让排查在受控权限边界内完成。

## What Changes
- 新增一个“创建/使用/清理”K8S 沙箱 Pod 的能力，默认以最小权限（read-only RBAC + 强安全上下文）运行
- 将该能力以工具形式接入现有 agent，使其可在需要时创建沙箱并执行诊断命令
- 通过环境变量配置集群访问与默认参数（namespace、image、ttl 等）

## Impact
- Affected specs: K8S 沙箱创建、RBAC 最小权限、Pod 安全上下文、命令执行与清理
- Affected code: main.py（工具注册）、新增 k8s_sandbox 模块（封装 kubectl/manifest）、新增最小测试与示例配置

## ADDED Requirements

### Requirement: Create Sandbox Pod
系统 SHALL 提供创建 K8S 沙箱 Pod 的能力，并返回可用于后续执行命令与清理的标识（namespace、podName、serviceAccountName）。

#### Scenario: Success case
- **WHEN** 调用方提供 namespace（或使用默认 namespace）
- **AND** 集群访问配置可用（例如存在 KUBECONFIG 或 in-cluster 配置）
- **THEN** 系统创建一个沙箱 Pod（以及必要的 ServiceAccount/Role/RoleBinding）
- **AND** 返回创建结果（podName 等）

#### Scenario: Failure case
- **WHEN** 集群不可达或权限不足导致创建失败
- **THEN** 系统返回明确错误信息（包含失败阶段：RBAC 创建/Pod 创建/等待就绪）

### Requirement: Restricted Permissions (RBAC)
系统 SHALL 默认创建最小权限的 Role/RoleBinding，限制沙箱 Pod 的 API 权限在目标 namespace 内，并且默认不授予读取 Secret、写入/修改资源的权限。

#### Default RBAC Profile: readonly
- 允许：get/list/watch pods、pods/log、services、endpoints、events（可扩展但需明确列出）
- 不允许：create/update/patch/delete 任意资源
- 不允许：get/list/watch secrets
- 可选：是否允许 pods/exec（默认允许，以便在沙箱内执行诊断命令；若关闭则仅允许外部日志/描述能力）

### Requirement: Restricted Runtime (Pod Security)
系统 SHALL 为沙箱 Pod 设置强约束安全上下文，避免容器越权与主机逃逸风险。

#### Security Defaults
- `runAsNonRoot: true`
- `allowPrivilegeEscalation: false`
- `readOnlyRootFilesystem: true`（如镜像不兼容，必须提供可选开关并在文档中声明风险）
- `capabilities.drop: ["ALL"]`
- `seccompProfile.type: RuntimeDefault`（若集群不支持则降级但需告警）
- 禁止 `hostNetwork/hostPID/hostIPC`
- 禁止 `privileged: true`
- 默认不挂载 `hostPath`

### Requirement: Execute Diagnostics in Sandbox
系统 SHALL 提供在沙箱 Pod 中执行诊断命令的能力，并返回 stdout/stderr/exitCode。

#### Scenario: Success case
- **WHEN** 沙箱 Pod Ready
- **AND** 调用方提供命令（结构化参数，非拼接字符串）
- **THEN** 系统在 Pod 内执行命令并返回结果

#### Scenario: Safety case
- **WHEN** 调用方提供危险命令或超时命令
- **THEN** 系统应用超时限制与命令参数校验（避免 shell 注入）

### Requirement: TTL and Cleanup
系统 SHALL 支持为沙箱 Pod 设置 TTL（默认例如 15 分钟），并提供显式清理接口删除 Pod 及其 RBAC 资源。

#### Scenario: Success case
- **WHEN** 调用方请求清理或 TTL 到期触发清理
- **THEN** 系统删除 Pod、ServiceAccount、Role、RoleBinding

## MODIFIED Requirements
### Requirement: Agent Tooling
agent 初始化 SHALL 支持注册新增的 k8s 沙箱工具函数，保持现有 internet_search 功能不变。

## REMOVED Requirements
无

## Assumptions (可在实现阶段调整)
- 通过调用 `kubectl` 命令实现（优先减少新增依赖）；后续可替换为 Kubernetes Python Client
- 沙箱镜像默认使用一个包含常见排查工具的镜像（例如 curl/dig/nslookup），最终以环境变量覆盖
- 默认以 namespace 级别隔离；不提供 cluster-admin 级别能力
